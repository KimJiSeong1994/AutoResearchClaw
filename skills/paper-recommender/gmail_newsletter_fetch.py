#!/usr/bin/env python3
"""Fetch Gmail messages via user-approved OAuth and publish newsletter links.

This script performs direct Gmail API collection only after the Google account
owner has authorized the ``gmail.readonly`` OAuth scope. It stores OAuth tokens
outside the repo by default, writes a sanitized JSONL export, then can call
``newsletter_ingest.py`` to publish extracted research/post links into PaperWiki.

Default local state:
  ~/Desktop/paper-wiki/google-oauth/credentials.json  # OAuth client secret
  ~/Desktop/paper-wiki/google-oauth/token.json        # user refresh token
  ~/Desktop/paper-wiki/newsletter-exports/gmail-api-YYYY-MM-DD.jsonl
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import socketserver
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import date as _date
from pathlib import Path
from typing import Any, Iterable

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
DEFAULT_ROOT = Path.home() / "Desktop" / "paper-wiki"
DEFAULT_OAUTH_DIR = DEFAULT_ROOT / "google-oauth"
DEFAULT_EXPORT_DIR = DEFAULT_ROOT / "newsletter-exports"


def _post_form(url: str, data: dict[str, str], *, timeout: int = 60) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get_json(url: str, token: str, *, timeout: int = 60) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _load_client_secret(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("installed") or raw.get("web") or raw
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    if not client_id or not client_secret:
        raise ValueError(f"{path} is not an OAuth client secret JSON")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "token_uri": data.get("token_uri") or TOKEN_URL,
    }


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    server: "_OAuthServer"

    def log_message(self, fmt: str, *args: object) -> None:  # silence stdout logs
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.auth_code = (params.get("code") or [None])[0]
        self.server.auth_error = (params.get("error") or [None])[0]
        ok = self.server.auth_code and not self.server.auth_error
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if ok:
            self.wfile.write(
                b"<html><body><h1>Gmail authorization complete</h1>"
                b"<p>You can close this tab and return to AutoResearchClaw.</p></body></html>"
            )
        else:
            msg = f"Authorization failed: {self.server.auth_error or 'missing code'}"
            self.wfile.write(msg.encode())


class _OAuthServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _OAuthCallbackHandler)
        self.auth_code: str | None = None
        self.auth_error: str | None = None


def run_oauth_flow(*, credentials_path: Path, token_path: Path) -> dict[str, Any]:
    client = _load_client_secret(credentials_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with _OAuthServer() as srv:
        redirect_uri = f"http://127.0.0.1:{srv.server_address[1]}/"
        params = {
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GMAIL_READONLY_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
        url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
        print("Opening Google OAuth consent URL for gmail.readonly...")
        if not webbrowser.open(url):
            print(url)
        deadline = time.time() + 300
        while time.time() < deadline and not srv.auth_code and not srv.auth_error:
            srv.handle_request()
        if srv.auth_error:
            raise RuntimeError(f"OAuth denied: {srv.auth_error}")
        if not srv.auth_code:
            raise TimeoutError("OAuth timed out waiting for browser approval")
        token = _post_form(
            client["token_uri"],
            {
                "code": srv.auth_code,
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    token.update(
        {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "token_uri": client["token_uri"],
            "scope": GMAIL_READONLY_SCOPE,
            "created_at": int(time.time()),
        }
    )
    if token.get("expires_in"):
        token["expires_at"] = int(time.time()) + int(token["expires_in"]) - 60
    _atomic_json(token_path, token)
    return token


def _atomic_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_or_refresh_token(*, token_path: Path, credentials_path: Path, auth_if_missing: bool) -> dict[str, Any]:
    if not token_path.exists():
        if not auth_if_missing:
            raise FileNotFoundError(f"missing Gmail token: {token_path}")
        if not credentials_path.exists():
            raise FileNotFoundError(f"missing OAuth client secret: {credentials_path}")
        return run_oauth_flow(credentials_path=credentials_path, token_path=token_path)
    token = json.loads(token_path.read_text(encoding="utf-8"))
    if token.get("access_token") and int(token.get("expires_at") or 0) > int(time.time()):
        return token
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        if not auth_if_missing:
            raise RuntimeError("token has no refresh_token; rerun with --auth")
        return run_oauth_flow(credentials_path=credentials_path, token_path=token_path)
    client_id = token.get("client_id")
    client_secret = token.get("client_secret")
    token_uri = token.get("token_uri") or TOKEN_URL
    if (not client_id or not client_secret) and credentials_path.exists():
        client = _load_client_secret(credentials_path)
        client_id = client["client_id"]
        client_secret = client["client_secret"]
        token_uri = client["token_uri"]
    refreshed = _post_form(
        token_uri,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    token.update(refreshed)
    token["token_uri"] = token_uri
    token["client_id"] = client_id
    token["client_secret"] = client_secret
    if refreshed.get("expires_in"):
        token["expires_at"] = int(time.time()) + int(refreshed["expires_in"]) - 60
    _atomic_json(token_path, token)
    return token


def list_message_ids(*, access_token: str, query: str, max_results: int) -> list[str]:
    ids: list[str] = []
    page_token = ""
    while len(ids) < max_results:
        params: dict[str, str | int] = {
            "maxResults": min(500, max_results - len(ids)),
            "q": query,
            "includeSpamTrash": "false",
        }
        if page_token:
            params["pageToken"] = page_token
        url = f"{GMAIL_API}/users/me/messages?{urllib.parse.urlencode(params)}"
        data = _get_json(url, access_token)
        ids.extend(m["id"] for m in data.get("messages", []) if m.get("id"))
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break
    return ids[:max_results]


def get_message(*, access_token: str, message_id: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"format": "full"})
    return _get_json(f"{GMAIL_API}/users/me/messages/{message_id}?{params}", access_token)


def _header(headers: list[dict[str, str]], name: str) -> str:
    lname = name.lower()
    for h in headers:
        if h.get("name", "").lower() == lname:
            return h.get("value", "")
    return ""


def _decode_body(data: str | None) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _walk_payload(part: dict[str, Any]) -> Iterable[str]:
    mime = part.get("mimeType") or ""
    body = part.get("body") or {}
    if mime in {"text/plain", "text/html"}:
        text = _decode_body(body.get("data"))
        if text:
            yield text
    for child in part.get("parts") or []:
        yield from _walk_payload(child)


def message_to_jsonl_record(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    return {
        "subject": _header(headers, "Subject"),
        "from": _header(headers, "From"),
        "date": _header(headers, "Date"),
        "body": "\n".join(_walk_payload(payload)),
        "gmail_id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
    }


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def run_newsletter_ingest(*, export_path: Path, wiki_root: Path, run_date: str, sender_allowlist: str) -> None:
    script = Path(__file__).with_name("newsletter_ingest.py")
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(export_path),
            "--wiki-root",
            str(wiki_root),
            "--date",
            run_date,
            "--sender-allowlist",
            sender_allowlist,
        ],
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--credentials", default=str(DEFAULT_OAUTH_DIR / "credentials.json"))
    p.add_argument("--token", default=str(DEFAULT_OAUTH_DIR / "token.json"))
    p.add_argument("--auth", action="store_true", help="Run browser OAuth flow if token is missing/invalid")
    p.add_argument("--query", default="newer_than:7d (newsletter OR research OR arxiv OR paper OR papers)")
    p.add_argument("--max-results", type=int, default=50)
    p.add_argument("--export", default="", help="Output JSONL path; default newsletter-exports/gmail-api-DATE.jsonl")
    p.add_argument("--date", default=_date.today().isoformat())
    p.add_argument("--wiki-root", default="/Users/jiseong/Library/Mobile Documents/com~apple~CloudDocs/PaperWiki/PaperWiki")
    p.add_argument("--sender-allowlist", default="newsletter,research,arxiv,substack,medium,openai,deepmind,anthropic,semanticscholar,paperswithcode,alpha signal,import ai,the batch,latent space")
    p.add_argument("--publish", action="store_true", help="Run newsletter_ingest.py after fetching JSONL")
    args = p.parse_args(argv)

    credentials_path = Path(args.credentials).expanduser()
    token_path = Path(args.token).expanduser()
    export_path = Path(args.export).expanduser() if args.export else DEFAULT_EXPORT_DIR / f"gmail-api-{args.date}.jsonl"
    wiki_root = Path(args.wiki_root).expanduser()

    try:
        token = load_or_refresh_token(
            token_path=token_path,
            credentials_path=credentials_path,
            auth_if_missing=args.auth,
        )
        access_token = token["access_token"]
        ids = list_message_ids(access_token=access_token, query=args.query, max_results=args.max_results)
        rows = [message_to_jsonl_record(get_message(access_token=access_token, message_id=mid)) for mid in ids]
        write_jsonl(export_path, rows)
        print(f"fetched Gmail messages: {len(rows)}")
        print(f"wrote sanitized JSONL export: {export_path}")
        if args.publish:
            run_newsletter_ingest(
                export_path=export_path,
                wiki_root=wiki_root,
                run_date=args.date,
                sender_allowlist=args.sender_allowlist,
            )
    except Exception as exc:
        print(f"gmail newsletter fetch failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
