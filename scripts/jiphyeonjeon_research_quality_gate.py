#!/usr/bin/env python3
"""Evidence/citation quality gate for 집현전-지도교수.

The gate is deliberately conservative and read-only: it summarizes whether an
artifact has enough public evidence signals to proceed to editorial/publication
review. It does not publish, approve, or mutate source artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

URL_RE = re.compile(r"https?://[^\s)\]}>'\"]+")
OVERCLAIM_RE = re.compile(r"\b(always|never|guarantee[sd]?|proves?|definitive(?:ly)?|revolutionary)\b", re.IGNORECASE)
NON_EVIDENCE_DOMAINS = {"discord.com", "cdn.discordapp.com", "media.discordapp.net"}
NON_EVIDENCE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("items", "candidates", "rows", "papers", "records", "sources", "evidence"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
        return [value]
    return []


def _load_artifact(path: Path) -> tuple[str, list[dict[str, Any]]]:
    text = _read_text(path)
    if path.suffix.lower() == ".jsonl":
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        return text, rows
    if path.suffix.lower() == ".json":
        return text, _json_rows(json.loads(text))
    return text, []


def _urls_from_row(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("url", "source_url", "link", "canonical_url", "doi_url", "pdf_url"):
        value = row.get(key)
        if value:
            urls.extend(URL_RE.findall(str(value)))
    for value in row.values():
        if isinstance(value, str):
            urls.extend(URL_RE.findall(value))
    return sorted(set(url.rstrip(".,") for url in urls))


def _domain(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _is_evidence_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    domain = parts.netloc.lower()
    if domain in NON_EVIDENCE_DOMAINS:
        return False
    path = parts.path.lower()
    if path.endswith(NON_EVIDENCE_EXTENSIONS):
        return False
    return parts.scheme in {"http", "https"} and bool(domain)


def evaluate(path: Path, *, min_evidence: int, min_domains: int) -> dict[str, Any]:
    text, rows = _load_artifact(path)
    text_urls_all = sorted(set(url.rstrip(".,") for url in URL_RE.findall(text)))
    text_urls = [url for url in text_urls_all if _is_evidence_url(url)]
    row_urls = [[url for url in _urls_from_row(row) if _is_evidence_url(url)] for row in rows]
    row_count = len(rows)
    evidenced_rows = sum(1 for urls in row_urls if urls)
    evidence_coverage_pct = 100 if row_count == 0 and text_urls else (round((evidenced_rows / row_count) * 100) if row_count else 0)
    domains = sorted({domain for url in text_urls for domain in [_domain(url)] if domain})
    issues: list[str] = []
    if len(text_urls) < min_evidence:
        issues.append(f"evidence_url_count_below_{min_evidence}")
    if len(domains) < min_domains:
        issues.append(f"source_diversity_below_{min_domains}")
    if row_count and evidence_coverage_pct < 80:
        issues.append("row_evidence_coverage_below_80_pct")
    overclaims = sorted(set(match.group(0).lower() for match in OVERCLAIM_RE.finditer(text)))
    if overclaims:
        issues.append("possible_overclaim_language")
    if not issues:
        status = "pass"
    elif len(text_urls) == 0 or evidence_coverage_pct < 50:
        status = "fail"
    else:
        status = "needs_review"
    return {
        "agent_id": "jiphyeonjeon-advisor",
        "agent_name": "집현전-지도교수",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "artifact_path": str(path),
        "quality_status": status,
        "quality_gate_passed": status == "pass",
        "publication_blocked": status != "pass",
        "advisory_only": True,
        "requires_human_promotion_review": True,
        "downstream_status": "pending_future_phase",
        "evidence_url_count": len(text_urls),
        "source_diversity": len(domains),
        "source_domains": domains,
        "row_count": row_count,
        "evidenced_row_count": evidenced_rows,
        "evidence_coverage_pct": evidence_coverage_pct,
        "possible_overclaim_terms": overclaims,
        "issues": issues,
        "no_mutation": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate evidence/citation quality for a research artifact.")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--min-evidence", type=int, default=2)
    parser.add_argument("--min-domains", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.artifact.expanduser(), min_evidence=args.min_evidence, min_domains=args.min_domains)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
