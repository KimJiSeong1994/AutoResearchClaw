#!/usr/bin/env python3
"""Fetch PDFs for autoresearch-recommended papers into raw/papers/.

Mirrors the naming convention used by `scripts/collect_from_local_db.py` in
the PaperWiki vault so PDFs from the bookmark pipeline and PDFs from the
autoresearch pipeline coexist in `raw/papers/` without collision:

  raw/papers/paper-{slug}.pdf   + paper-{slug}.json    (success)
  raw/papers/stub-{slug}.json                          (PDF unresolvable)

Idempotent: skips any title where `paper-{slug}.pdf` or `stub-{slug}.json`
already exists, regardless of which pipeline produced it.

Inputs:
  - {wiki_root}/raw/autoresearch/{date}/daily-research-raw.json   (preferred,
    has clusters[].items[] with arxiv_id/doi/url)
  - {wiki_root}/raw/autoresearch/{date}/daily-research-papers.md  (fallback)

Usage:
  autoresearch_pdf_fetch.py {wiki_root}                   # latest date
  autoresearch_pdf_fetch.py {wiki_root} --date 2026-05-02
  autoresearch_pdf_fetch.py {wiki_root} --all-dates
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from wiki_publish import _parse_paper_bullets  # noqa: E402

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 PaperWiki-AutoResearch/1.0"
)
TIMEOUT = 30
RETRY = 3


# ─────────────────── slug / naming (matches collect_from_local_db.py) ──


def slugify_title(title: str, max_len: int = 120) -> str:
    s = (title or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[\\/:*?\"<>|]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-_")
    if not s:
        s = "untitled"
    if len(s) > max_len:
        s = s[:max_len].rstrip(" .-_")
    return s


def existing_marker(papers_dir: Path, slug: str) -> str | None:
    if (papers_dir / f"paper-{slug}.pdf").exists():
        return "paper"
    if (papers_dir / f"stub-{slug}.json").exists():
        return "stub"
    return None


# ─────────────────── HTTP / PDF download ──


def http_get(url: str, *, timeout: float = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _extract_pdf_link(html: bytes, base: str) -> str | None:
    try:
        text = html.decode("utf-8", errors="ignore")
    except Exception:
        return None
    for pat in [
        r'<meta[^>]+name="citation_pdf_url"[^>]+content="([^"]+)"',
        r'href="([^"]+\.pdf[^"]*)"',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            u = m.group(1)
            if u.startswith("/"):
                u = urllib.parse.urljoin(base, u)
            return u
    return None


def _download(url: str) -> bytes:
    last_err: BaseException | None = None
    for attempt in range(1, RETRY + 1):
        try:
            data = http_get(url)
            if data.startswith(b"%PDF"):
                return data
            head = data[:2048].lower()
            if b"<html" in head or b"<!doctype" in head:
                emb = _extract_pdf_link(data, url)
                if emb and emb != url:
                    data2 = http_get(emb)
                    if data2.startswith(b"%PDF"):
                        return data2
            raise ValueError(f"response not a PDF (first bytes: {data[:8]!r})")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < RETRY:
                time.sleep(2 ** attempt)
                continue
            if attempt >= RETRY:
                raise
        except Exception as e:
            last_err = e
            if attempt < RETRY:
                time.sleep(2)
                continue
            raise
    raise last_err if last_err else RuntimeError("download failed")


def _candidate_urls(item: dict[str, Any]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(url: str | None, src: str) -> None:
        if url and url not in seen:
            seen.add(url)
            candidates.append((url, src))

    aid = item.get("arxiv_id")
    if aid:
        _add(f"https://arxiv.org/pdf/{aid}.pdf", "arxiv")

    doi = (item.get("doi") or "").strip()
    if doi:
        if doi.startswith("10.3390/"):
            _add(f"https://www.mdpi.com/{doi[8:]}/pdf", "mdpi")
        if doi.startswith("10.1007/"):
            _add(f"https://link.springer.com/content/pdf/{doi}.pdf", "springer")
        if doi.startswith("10.1038/"):
            _add(f"https://www.nature.com/articles/{doi[8:]}.pdf", "nature")
        if doi.startswith("10.1109/"):
            _add(f"https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber={doi[7:]}", "ieee")
        # Generic DOI landing → relies on citation_pdf_url meta tag follow
        _add(f"https://doi.org/{doi}", "doi-landing")

    url = item.get("url")
    if url:
        # Direct PDF link first (often arxiv abs page → handled separately by arxiv_id)
        _add(url, "url-direct")

    return candidates


def fetch_one(item: dict[str, Any]) -> tuple[bytes | None, str | None, str]:
    errs: list[str] = []
    for cand_url, src in _candidate_urls(item):
        try:
            data = _download(cand_url)
            return data, cand_url, ""
        except Exception as e:
            errs.append(f"{src}: {type(e).__name__}: {str(e)[:120]}")
            continue
    return None, None, "; ".join(errs) or "no candidates"


# ─────────────────── input parsing ──


def items_from_raw_json(date_dir: Path) -> list[dict[str, Any]]:
    p = date_dir / "daily-research-raw.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for cluster in data.get("clusters", []) or []:
        for item in cluster.get("items", []) or []:
            out.append(item)
    return out


def items_from_papers_md(date_dir: Path) -> list[dict[str, Any]]:
    p = date_dir / "daily-research-papers.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    _keep = {"title", "source", "year", "url", "arxiv_id", "doi", "abstract"}
    return [{k: v for k, v in paper.items() if k in _keep} for paper in _parse_paper_bullets(text)]


def items_for_date(date_dir: Path) -> list[dict[str, Any]]:
    items = items_from_raw_json(date_dir)
    if items:
        return items
    return items_from_papers_md(date_dir)


# ─────────────────── output ──


def write_paper(papers_dir: Path, slug: str, item: dict, pdf: bytes, src_url: str) -> None:
    pdf_path = papers_dir / f"paper-{slug}.pdf"
    json_path = papers_dir / f"paper-{slug}.json"
    pdf_path.write_bytes(pdf)
    json_path.write_text(
        json.dumps(
            {
                "title": item.get("title"),
                "authors": item.get("authors") or [],
                "year": item.get("year"),
                "doi": item.get("doi"),
                "arxiv_id": item.get("arxiv_id"),
                "url": item.get("url"),
                "abstract": item.get("abstract"),
                "source_pipeline": "autoresearch",
                "via_url": src_url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "bytes": len(pdf),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_stub(papers_dir: Path, slug: str, item: dict, error: str) -> None:
    json_path = papers_dir / f"stub-{slug}.json"
    json_path.write_text(
        json.dumps(
            {
                "title": item.get("title"),
                "authors": item.get("authors") or [],
                "year": item.get("year"),
                "doi": item.get("doi"),
                "arxiv_id": item.get("arxiv_id"),
                "url": item.get("url"),
                "abstract": item.get("abstract"),
                "source_pipeline": "autoresearch",
                "pdf_status": "unavailable",
                "pdf_missing_reason": error[:600],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ─────────────────── orchestration ──


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("wiki_root", help="Path to PaperWiki/PaperWiki/")
    p.add_argument("--date", help="YYYY-MM-DD (default: latest)")
    p.add_argument("--all-dates", action="store_true", help="process every date dir")
    p.add_argument("--limit", type=int, default=200, help="max items per date (default 200)")
    p.add_argument("--sleep", type=float, default=0.5, help="seconds between downloads")
    args = p.parse_args()

    wiki_root = Path(args.wiki_root).expanduser()
    papers_dir = wiki_root / "raw" / "papers"
    auto_dir = wiki_root / "raw" / "autoresearch"
    papers_dir.mkdir(parents=True, exist_ok=True)

    if not auto_dir.exists():
        print("autoresearch dir missing; nothing to do", file=sys.stderr)
        return 0

    all_dirs = sorted(
        d for d in auto_dir.iterdir()
        if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)
    )
    if args.date:
        date_dirs = [auto_dir / args.date]
    elif args.all_dates:
        date_dirs = all_dirs
    else:
        date_dirs = all_dirs[-1:] if all_dirs else []

    fetched = stubbed = skipped = 0
    for date_dir in date_dirs:
        items = items_for_date(date_dir)
        print(f"[{date_dir.name}] {len(items)} items to consider", file=sys.stderr)
        for i, item in enumerate(items[: args.limit], 1):
            title = (item.get("title") or "").strip()
            if not title:
                continue
            slug = slugify_title(title)
            existing = existing_marker(papers_dir, slug)
            if existing:
                skipped += 1
                continue
            pdf, src_url, err = fetch_one(item)
            if pdf:
                write_paper(papers_dir, slug, item, pdf, src_url or "")
                fetched += 1
                print(f"  [{i}/{len(items)}] paper-{slug[:55]}  ({len(pdf)} bytes)", file=sys.stderr)
            else:
                write_stub(papers_dir, slug, item, err)
                stubbed += 1
                print(f"  [{i}/{len(items)}] stub-{slug[:55]}", file=sys.stderr)
            if args.sleep > 0:
                time.sleep(args.sleep)

    print(f"\nsummary: fetched={fetched}, stubbed={stubbed}, skipped={skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
