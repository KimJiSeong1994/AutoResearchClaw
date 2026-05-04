#!/usr/bin/env python3
"""Fetch Gmail newsletters through OAuth and publish a Discord-ready briefing.

Uses the official Gmail REST API with the narrow readonly scope. Email bodies are
kept in memory only for URL extraction; persisted outputs contain metadata and
extracted URLs, not full message bodies.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Iterable

import httpx

from newsletter_ingest import (
    NewsletterMessage,
    publish_items,
    render_topic_briefing,
    select_items,
    _atomic_write_text,
)

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


def load_oauth_client(path: Path) -> OAuthClient:
    data = _read_json(path)
    raw = data.get("installed") or data.get("web") or data
    client_id = str(raw.get("client_id") or "").strip()
    client_secret = str(raw.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise ValueError(f"OAuth client file missing client_id/client_secret: {path}")
    return OAuthClient(client_id=client_id, client_secret=client_secret)


def build_auth_url(client: OAuthClient, *, redirect_uri: str, state: str = "newsletter") -> str:
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(
    client: OAuthClient,
    *,
    code: str,
    redirect_uri: str,
    token_path: Path,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    owns = http is None
    http = http or httpx.Client(timeout=30)
    try:
        resp = http.post(
            TOKEN_URL,
            data={
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        token = resp.json()
        if "refresh_token" not in token:
            raise ValueError("Google token response did not include refresh_token; use prompt=consent and a Desktop OAuth client")
        token["client_id"] = client.client_id
        token["client_secret"] = client.client_secret
        token["scope"] = token.get("scope") or GMAIL_READONLY_SCOPE
        token_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(token_path, json.dumps(token, ensure_ascii=False, indent=2) + "\n")
        try:
            token_path.chmod(0o600)
        except OSError:
            pass
        return token
    finally:
        if owns:
            http.close()


def refresh_access_token(token_path: Path, http: httpx.Client | None = None) -> str:
    token = _read_json(token_path)
    refresh_token = str(token.get("refresh_token") or "").strip()
    client_id = str(token.get("client_id") or "").strip()
    client_secret = str(token.get("client_secret") or "").strip()
    if not refresh_token or not client_id or not client_secret:
        raise ValueError(f"token file missing refresh_token/client_id/client_secret: {token_path}")
    owns = http is None
    http = http or httpx.Client(timeout=30)
    try:
        resp = http.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        access_token = str(resp.json().get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Google refresh response did not include access_token")
        return access_token
    finally:
        if owns:
            http.close()


def _headers_by_name(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for header in payload.get("headers") or []:
        name = str(header.get("name") or "").lower()
        if name:
            out[name] = str(header.get("value") or "")
    return out


def _decode_body_data(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")


def _walk_parts(part: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield part
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            yield from _walk_parts(child)


def message_to_newsletter(message: dict[str, Any]) -> NewsletterMessage:
    payload = message.get("payload") or {}
    headers = _headers_by_name(payload)
    chunks: list[str] = []
    for part in _walk_parts(payload):
        mime_type = str(part.get("mimeType") or "")
        if mime_type not in {"text/plain", "text/html"}:
            continue
        body = part.get("body") or {}
        chunks.append(_decode_body_data(str(body.get("data") or "")))
    return NewsletterMessage(
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        received_at=headers.get("date", ""),
        body="\n".join(c for c in chunks if c),
    )


def fetch_gmail_messages(
    *,
    access_token: str,
    query: str,
    max_messages: int,
    http: httpx.Client | None = None,
) -> list[NewsletterMessage]:
    owns = http is None
    http = http or httpx.Client(timeout=30)
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_messages:
            params: dict[str, Any] = {"maxResults": min(100, max_messages - len(ids))}
            if query:
                params["q"] = query
            if page_token:
                params["pageToken"] = page_token
            resp = http.get(f"{GMAIL_BASE}/users/me/messages", headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            ids.extend(str(m["id"]) for m in data.get("messages", []) if "id" in m)
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        out: list[NewsletterMessage] = []
        for msg_id in ids[:max_messages]:
            resp = http.get(
                f"{GMAIL_BASE}/users/me/messages/{msg_id}",
                headers=headers,
                params={"format": "full"},
            )
            resp.raise_for_status()
            out.append(message_to_newsletter(resp.json()))
        return out
    finally:
        if owns:
            http.close()


def publish_gmail_briefing(
    *,
    token_path: Path,
    wiki_root: Path,
    briefing_path: Path,
    run_date: str,
    sender_allowlist: list[str],
    query: str,
    max_messages: int,
    include_all_urls: bool,
) -> tuple[Path, Path, Path, int]:
    access_token = refresh_access_token(token_path)
    messages = fetch_gmail_messages(access_token=access_token, query=query, max_messages=max_messages)
    items = select_items(messages, sender_allowlist=sender_allowlist, include_all_urls=include_all_urls)
    raw_path, page_path = publish_items(
        wiki_root=wiki_root,
        run_date=run_date,
        source_path=Path("gmail-api"),
        items=items,
    )
    briefing_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        briefing_path,
        render_topic_briefing(
            run_date=run_date,
            items=items,
            source_name="gmail-api",
        ),
    )
    return raw_path, page_path, briefing_path, len(items)


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--client-secret", default=os.environ.get("GMAIL_CLIENT_SECRET_PATH", ""))
    p.add_argument("--token", default=os.environ.get("GMAIL_TOKEN_PATH", str(Path.home() / ".openclaw" / "workspace" / "secrets" / "gmail-token.json")))
    p.add_argument("--redirect-uri", default=os.environ.get("GMAIL_REDIRECT_URI", "http://localhost:8765/oauth2callback"))
    p.add_argument("--print-auth-url", action="store_true")
    p.add_argument("--exchange-code", default="")
    p.add_argument("--wiki-root", default=os.environ.get("NEWSLETTER_WIKI_ROOT", str(Path.home() / ".openclaw" / "workspace" / "wiki")))
    p.add_argument("--briefing-path", default=os.environ.get("NEWSLETTER_REPORT_PATH", str(Path.home() / ".openclaw" / "workspace" / "reports" / "newsletter-briefing-latest.md")))
    p.add_argument("--date", default=os.environ.get("NEWSLETTER_DATE", _date.today().isoformat()))
    p.add_argument("--sender-allowlist", default=os.environ.get("NEWSLETTER_SENDER_ALLOWLIST", ""))
    p.add_argument("--query", default=os.environ.get("GMAIL_NEWSLETTER_QUERY", "newer_than:7d"))
    p.add_argument("--max-messages", type=int, default=int(os.environ.get("NEWSLETTER_MAX_MESSAGES", "100")))
    p.add_argument("--include-all-urls", action="store_true")
    args = p.parse_args(argv)

    token_path = Path(args.token).expanduser()
    if args.print_auth_url or args.exchange_code:
        if not args.client_secret:
            print("--client-secret or GMAIL_CLIENT_SECRET_PATH is required for OAuth setup", file=sys.stderr)
            return 2
        client = load_oauth_client(Path(args.client_secret).expanduser())
        if args.print_auth_url:
            print(build_auth_url(client, redirect_uri=args.redirect_uri))
            return 0
        exchange_code(client, code=args.exchange_code, redirect_uri=args.redirect_uri, token_path=token_path)
        print(f"wrote token: {token_path}")
        return 0

    allow = _split_csv(args.sender_allowlist)
    if not allow:
        print("Gmail API briefing requires --sender-allowlist or NEWSLETTER_SENDER_ALLOWLIST", file=sys.stderr)
        return 2
    if not token_path.exists():
        print(f"Gmail token file not found: {token_path}", file=sys.stderr)
        return 1
    try:
        raw_path, page_path, briefing_path, count = publish_gmail_briefing(
            token_path=token_path,
            wiki_root=Path(args.wiki_root).expanduser(),
            briefing_path=Path(args.briefing_path).expanduser(),
            run_date=args.date,
            sender_allowlist=allow,
            query=args.query,
            max_messages=args.max_messages,
            include_all_urls=args.include_all_urls,
        )
    except Exception as exc:
        print(f"Gmail newsletter briefing failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {raw_path}")
    print(f"wrote {page_path}")
    print(f"wrote {briefing_path}")
    print(f"items: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
