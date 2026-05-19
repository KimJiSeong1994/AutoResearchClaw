#!/usr/bin/env python3
"""Cross-surface content identity report for 집현전-편집자.

This script is intentionally read-only. It accepts JSON or JSONL artifacts from
Miner, newsletter, recommendation, card-news, or wiki-adjacent surfaces and
emits a small canonical identity/duplicate report without mutating inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}
SENSITIVE_QUERY_KEYS = {"access_token", "api_key", "apikey", "auth", "code", "key", "password", "secret", "sig", "signature", "token"}
PRIVATE_HOSTS = {"localhost", "metadata.google.internal"}
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ARXIV_RE = re.compile(r"(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
OPENREVIEW_RE = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_-]+)", re.IGNORECASE)
WORD_RE = re.compile(r"[a-z0-9가-힣]+", re.IGNORECASE)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        return rows
    value = json.loads(text)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("items", "candidates", "rows", "papers", "records"):
            nested = value.get(key)
            if isinstance(nested, list):
                rows.extend(row for row in nested if isinstance(row, dict))
        return rows or [value]
    return []


def _first_str(row: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _is_private_host(host: str) -> bool:
    hostname = host.split(":", 1)[0].strip("[]").lower()
    if not hostname or hostname in PRIVATE_HOSTS or hostname.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _clean_url(raw: str) -> str:
    if not raw:
        return ""
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if parts.username or parts.password or "@" in parts.netloc:
        return ""
    host = parts.netloc.lower()
    if _is_private_host(host):
        return ""
    path = re.sub(r"/+$", "", parts.path or "/") or "/"
    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        key_l = key.lower()
        if (
            key_l in SENSITIVE_QUERY_KEYS
            or key_l in TRACKING_QUERY_KEYS
            or any(key_l.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES)
        ):
            continue
        query_pairs.append((key, value))
    query = urlencode(sorted(query_pairs))
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _title_fingerprint(title: str) -> str:
    words = WORD_RE.findall(title.lower())
    collapsed = " ".join(words)
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()[:16] if collapsed else ""


def canonical_key(row: dict[str, Any]) -> str:
    """Return a stable best-effort canonical key for cross-surface dedupe."""
    doi = _first_str(row, ("doi", "DOI"))
    if not doi:
        blob = " ".join(str(row.get(k) or "") for k in ("url", "source_url", "link", "id"))
        m = DOI_RE.search(blob)
        doi = m.group(0) if m else ""
    if doi:
        return "doi:" + doi.lower().rstrip(".")

    arxiv = _first_str(row, ("arxiv_id", "arxiv", "paper_id"))
    if not arxiv:
        blob = " ".join(str(row.get(k) or "") for k in ("url", "source_url", "link", "id"))
        m = ARXIV_RE.search(blob)
        arxiv = m.group(1) if m else ""
    if arxiv and ARXIV_RE.search(arxiv):
        return "arxiv:" + ARXIV_RE.search(arxiv).group(1).lower()  # type: ignore[union-attr]

    url = _clean_url(_first_str(row, ("url", "source_url", "link", "canonical_url")))
    if url:
        m = OPENREVIEW_RE.search(url)
        if m:
            return "openreview:" + m.group(1)
        return "url:" + url

    title = _first_str(row, ("title", "name"))
    title_key = _title_fingerprint(title)
    if title_key:
        return "title:" + title_key
    row_blob = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return "row:" + hashlib.sha256(row_blob.encode("utf-8")).hexdigest()[:16]


def build_report(paths: list[Path]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    input_counts: dict[str, int] = {}
    for path in paths:
        rows = _read_rows(path)
        input_counts[str(path)] = len(rows)
        for index, row in enumerate(rows):
            key = canonical_key(row)
            grouped[key].append(
                {
                    "input_path": str(path),
                    "row_index": index,
                    "title": _first_str(row, ("title", "name"))[:180],
                    "url": _clean_url(_first_str(row, ("url", "source_url", "link", "canonical_url"))),
                }
            )
    duplicate_groups = [
        {"canonical_key": key, "count": len(rows), "items": rows}
        for key, rows in sorted(grouped.items())
        if len(rows) > 1
    ]
    return {
        "agent_id": "jiphyeonjeon-editor",
        "agent_name": "집현전-편집자",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "input_counts": input_counts,
        "item_count": sum(input_counts.values()),
        "canonical_count": len(grouped),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups,
        "no_mutation": True,
        "advisory_only": True,
        "requires_human_promotion_review": True,
        "downstream_status": "pending_future_phase",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only cross-surface canonical identity report.")
    parser.add_argument("inputs", nargs="+", type=Path, help="JSON or JSONL artifacts to inspect")
    args = parser.parse_args(argv)
    try:
        report = build_report([path.expanduser() for path in args.inputs])
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
