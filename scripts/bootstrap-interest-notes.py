#!/usr/bin/env python3
"""Bootstrap PaperWiki interest-area notes from existing signals.

Generates initial ``pages/interests/<slug>.md`` notes from
``runtime/traveler-scout-topics.json`` into a target vault. Notes carry
``source: bootstrap`` so user-curated notes (``source: user``) are never
overwritten. Output is deterministic and idempotent: rerunning produces
byte-identical files for bootstrap-owned notes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIORITY_WEIGHT = {"high": 0.8, "medium": 0.6, "low": 0.4}
STOPWORDS = {"and", "for", "the", "a", "an", "of", "to", "in", "on", "with"}


def title_from_id(topic_id: str) -> str:
    return " ".join(w.capitalize() for w in topic_id.replace("_", " ").split())


def keywords_from_query(query: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for word in query.split():
        token = word.strip().lower()
        if not token or token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def read_frontmatter_source(path: Path) -> str:
    """Return the ``source`` frontmatter value, or the ``"unknown"`` sentinel.

    Fail-safe: any text we cannot positively parse as a closed frontmatter block
    yields ``"unknown"`` so callers never treat it as bootstrap-owned and never
    overwrite it. Normalizes a UTF-8 BOM and leading blank lines before fence
    detection so user notes with leading whitespace are still recognized.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    text = text.lstrip("﻿")
    while "\n" in text:
        nl = text.find("\n")
        if text[:nl].strip():
            break
        text = text[nl + 1 :]
    if not text.startswith("---\n"):
        return "unknown"
    end = text.find("\n---\n", 4)
    if end < 0:
        return "unknown"
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        if key.strip() == "source":
            return val.strip().strip('"\'')
    return "unknown"


def render_note(topic: dict) -> str:
    topic_id = str(topic.get("id", ""))
    title = title_from_id(topic_id)
    priority = str(topic.get("priority", "medium")).lower()
    weight = PRIORITY_WEIGHT.get(priority, 0.6)
    keywords = keywords_from_query(str(topic.get("query", "")))
    scope = str(topic.get("scope", "")).strip()
    fm_lines = [
        "---",
        "type: interest",
        "aliases: []",
        "interest_status: active",
        f"interest_weight: {weight}",
        f"seed_keywords: [{', '.join(keywords)}]",
        "related_tags: []",
        "source: bootstrap",
        "---",
    ]
    body_lines = [f"# {title}", ""]
    if scope:
        body_lines.append(scope)
        body_lines.append("")
    body_lines.append("## Anchors")
    body_lines.append("<!-- add [[wikilinks]] to anchor notes -->")
    body_lines.append("")
    return "\n".join(fm_lines) + "\n" + "\n".join(body_lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bootstrap PaperWiki interest-area notes")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--traveler-topics", default=str(ROOT / "runtime" / "traveler-scout-topics.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    vault = Path(args.vault).expanduser()
    topics_path = Path(args.traveler_topics).expanduser()
    interests_dir = vault / "pages" / "interests"

    try:
        data = json.loads(topics_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        error = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(error, ensure_ascii=False, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    topics = data.get("topics", [])

    interests_root = interests_dir.resolve()
    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    seen_slugs: set[str] = set()

    for topic in topics:
        topic_id = str(topic.get("id", ""))
        if not topic_id:
            continue
        slug = re.sub(r"[^a-z0-9-]+", "-", topic_id.lower().replace("_", "-")).strip("-")
        if not slug:
            skipped.append(f"{topic_id} (empty_slug)")
            continue
        if slug in seen_slugs:
            skipped.append(f"pages/interests/{slug}.md (duplicate_slug)")
            continue
        seen_slugs.add(slug)
        target = interests_dir / f"{slug}.md"
        # Defense-in-depth path-traversal guard: the resolved target must stay
        # within pages/interests/ even though the slug sanitizer already strips
        # path separators.
        try:
            resolved = target.resolve()
        except OSError:
            skipped.append(f"pages/interests/{slug}.md (unresolvable)")
            continue
        if not str(resolved).startswith(str(interests_root) + os.sep):
            skipped.append(f"pages/interests/{slug}.md (path_escape)")
            continue
        rel = f"pages/interests/{slug}.md"
        content = render_note(topic)
        if target.exists():
            # Fail-safe preserve: only overwrite a file we can positively confirm
            # we own (source: bootstrap) and whose content actually differs.
            # Anything else (user / unknown / unclosed frontmatter) is preserved.
            if read_frontmatter_source(target) != "bootstrap":
                skipped.append(rel)
                continue
            if target.read_text(encoding="utf-8") == content:
                skipped.append(rel)
                continue
            if not args.dry_run:
                target.write_text(content, encoding="utf-8")
            updated.append(rel)
        else:
            if not args.dry_run:
                interests_dir.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            created.append(rel)

    summary = {
        "ok": True,
        "dry_run": args.dry_run,
        "created": sorted(created),
        "updated": sorted(updated),
        "skipped": sorted(skipped),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
