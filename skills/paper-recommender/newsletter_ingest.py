#!/usr/bin/env python3
"""Publish local Google/Gmail newsletter exports into the PaperWiki vault.

This intentionally does **not** authenticate to Google or read a mailbox by
itself.  It accepts a user-supplied local export (Gmail Takeout ``.mbox`` or
sanitized JSONL) and writes a raw-first, idempotent wiki intake:

  - {wiki_root}/raw/newsletters/{date}/items.json
  - {wiki_root}/pages/newsletter-ingest-{date}.md

Only message metadata and extracted research/post URLs are persisted.  Full
email bodies are never written to the wiki output.
"""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import mailbox
import os
import re
import sys
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Iterable, Iterator


_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?)]}>'\""

_RESEARCH_HOST_HINTS = (
    "arxiv.org",
    "doi.org",
    "openreview.net",
    "semanticscholar.org",
    "paperswithcode.com",
    "aclanthology.org",
    "proceedings.mlr.press",
    "neurips.cc",
    "icml.cc",
    "openai.com",
    "anthropic.com",
    "deepmind.google",
    "ai.googleblog.com",
    "github.com",
)

_DEFAULT_MAX_MESSAGES = 500


@dataclass(frozen=True)
class NewsletterMessage:
    subject: str
    sender: str
    received_at: str
    body: str


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _safe_title(value: str) -> str:
    value = _clean_text(value)
    return value.replace("[[", "[ [").replace("]]", "] ]").replace("|", "\\|")


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out: list[str] = []
    for payload, charset in parts:
        if isinstance(payload, bytes):
            out.append(payload.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(payload)
    return _clean_text("".join(out))


def _message_body(msg: mailbox.mboxMessage) -> str:
    """Return decoded text/html body for URL extraction only."""
    chunks: list[str] = []
    if msg.is_multipart():
        parts = msg.walk()
    else:
        parts = [msg]
    for part in parts:
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if payload is None:
            raw_payload = part.get_payload()
            if isinstance(raw_payload, str):
                chunks.append(raw_payload)
            continue
        charset = part.get_content_charset() or "utf-8"
        chunks.append(payload.decode(charset, errors="replace"))
    return "\n".join(chunks)


def load_mbox(path: Path) -> Iterator[NewsletterMessage]:
    for msg in mailbox.mbox(path):
        yield NewsletterMessage(
            subject=_decode_header(msg.get("subject")),
            sender=_decode_header(msg.get("from")),
            received_at=_clean_text(msg.get("date")),
            body=_message_body(msg),
        )


def load_jsonl(path: Path) -> Iterator[NewsletterMessage]:
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
            body = raw.get("body") or raw.get("text") or raw.get("html") or ""
            yield NewsletterMessage(
                subject=_clean_text(str(raw.get("subject") or "")),
                sender=_clean_text(str(raw.get("from") or raw.get("sender") or "")),
                received_at=_clean_text(str(raw.get("date") or raw.get("received_at") or "")),
                body=str(body),
            )


def iter_messages(path: Path) -> Iterator[NewsletterMessage]:
    suffix = path.suffix.lower()
    if suffix in {".mbox", ".mbx"}:
        yield from load_mbox(path)
        return
    if suffix in {".jsonl", ".ndjson"}:
        yield from load_jsonl(path)
        return
    raise ValueError(f"unsupported source type for {path}; expected .mbox or .jsonl")


def load_messages(path: Path, *, max_messages: int | None = None) -> list[NewsletterMessage]:
    messages: list[NewsletterMessage] = []
    for idx, msg in enumerate(iter_messages(path), start=1):
        if max_messages is not None and idx > max_messages:
            break
        messages.append(msg)
    return messages


def enforce_source_size(path: Path, *, max_source_bytes: int | None) -> None:
    if max_source_bytes is None:
        return
    size = path.stat().st_size
    if size > max_source_bytes:
        raise ValueError(
            f"source export is {size} bytes, above --max-source-bytes={max_source_bytes}; "
            "split or sanitize the export first"
        )


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    unescaped = html.unescape(text)
    for match in _URL_RE.finditer(unescaped):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def classify_url(url: str) -> str:
    lower = url.lower()
    if "arxiv.org/abs/" in lower or "arxiv.org/pdf/" in lower:
        return "paper:arxiv"
    if "doi.org/" in lower:
        return "paper:doi"
    if any(host in lower for host in ("openreview.net", "semanticscholar.org", "aclanthology.org", "proceedings.mlr.press")):
        return "paper"
    if "github.com/" in lower:
        return "code"
    if any(host in lower for host in ("openai.com", "anthropic.com", "deepmind.google", "ai.googleblog.com")):
        return "research-post"
    return "post"


def is_research_url(url: str) -> bool:
    lower = url.lower()
    return any(hint in lower for hint in _RESEARCH_HOST_HINTS)


def select_items(
    messages: Iterable[NewsletterMessage],
    *,
    sender_allowlist: list[str],
    include_all_urls: bool = False,
) -> list[dict[str, str]]:
    allow = [s.lower() for s in sender_allowlist]
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for msg in messages:
        sender_lower = msg.sender.lower()
        if allow and not any(token in sender_lower for token in allow):
            continue
        for url in extract_urls(msg.body):
            if not include_all_urls and not is_research_url(url):
                continue
            key = (msg.subject, url)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "title": msg.subject or "(untitled newsletter item)",
                    "url": url,
                    "kind": classify_url(url),
                    "sender": msg.sender,
                    "received_at": msg.received_at,
                }
            )
    return items


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def render_page(*, run_date: str, items: list[dict[str, str]], source_name: str) -> str:
    out = [
        "---",
        f'date: "{run_date}"',
        "type: newsletter-ingest",
        "tags:",
        "  - newsletters",
        "  - llm-wiki",
        "---",
        f"# Newsletter intake — {run_date}",
        "",
        "> [!info] Privacy boundary",
        "> Generated from a user-provided local export. Full email bodies and credentials are not stored in this page.",
        "",
        f"- Source export: `{source_name}`",
        f"- Extracted items: {len(items)}",
        "",
    ]
    if not items:
        out += ["No research/post URLs matched the configured filters.", ""]
    else:
        out += ["## Items", ""]
        for item in items:
            title = _safe_title(item["title"])
            sender = _safe_title(item["sender"])
            out.append(f"- **{title}** — [{item['kind']}]({item['url']})")
            if sender or item["received_at"]:
                out.append(f"  - from: {sender or 'unknown'} · received: {item['received_at'] or 'unknown'}")
        out.append("")
    out.append("*Generated by `newsletter_ingest.py`*")
    return "\n".join(out) + "\n"


def publish_items(
    *,
    wiki_root: Path,
    run_date: str,
    source_path: Path,
    items: list[dict[str, str]],
) -> tuple[Path, Path]:
    raw_dir = wiki_root / "raw" / "newsletters" / run_date
    pages_dir = wiki_root / "pages"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / "items.json"
    page_path = pages_dir / f"newsletter-ingest-{run_date}.md"
    payload = {
        "date": run_date,
        "source_file": source_path.name,
        "privacy": "metadata-and-extracted-urls-only; full email bodies omitted",
        "items": items,
    }
    _atomic_write_text(raw_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(
        page_path,
        render_page(run_date=run_date, items=items, source_name=source_path.name),
    )
    return raw_path, page_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Local Gmail Takeout .mbox or sanitized .jsonl export")
    parser.add_argument("--wiki-root", required=True, help="PaperWiki/PaperWiki root")
    parser.add_argument("--date", default=_date.today().isoformat(), help="Run date folder/page date")
    parser.add_argument(
        "--sender-allowlist",
        default="",
        help="Comma-separated sender/domain substrings to include",
    )
    parser.add_argument(
        "--allow-all-senders",
        action="store_true",
        help="Explicitly process all messages in the local export; use only with sanitized exports",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=_DEFAULT_MAX_MESSAGES,
        help=f"Maximum messages to inspect from the export (default: {_DEFAULT_MAX_MESSAGES})",
    )
    parser.add_argument(
        "--max-source-bytes",
        type=int,
        default=25 * 1024 * 1024,
        help="Maximum export file size to read (default: 25 MiB); set 0 to disable",
    )
    parser.add_argument(
        "--include-all-urls",
        action="store_true",
        help="Include all extracted URLs instead of research/post host hints only",
    )
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser()
    wiki_root = Path(args.wiki_root).expanduser()
    if not source.exists():
        print(f"source export not found: {source}", file=sys.stderr)
        return 1
    allow = [s.strip() for s in args.sender_allowlist.split(",") if s.strip()]
    if not allow and not args.allow_all_senders:
        print(
            "newsletter ingest requires --sender-allowlist or explicit --allow-all-senders",
            file=sys.stderr,
        )
        return 2
    if args.max_messages < 1:
        print("--max-messages must be >= 1", file=sys.stderr)
        return 2
    max_source_bytes = None if args.max_source_bytes == 0 else args.max_source_bytes
    try:
        enforce_source_size(source, max_source_bytes=max_source_bytes)
        messages = load_messages(source, max_messages=args.max_messages)
        items = select_items(messages, sender_allowlist=allow, include_all_urls=args.include_all_urls)
        raw_path, page_path = publish_items(
            wiki_root=wiki_root,
            run_date=args.date,
            source_path=source,
            items=items,
        )
    except Exception as exc:
        print(f"newsletter ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {raw_path}")
    print(f"wrote {page_path}")
    print(f"items: {len(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
