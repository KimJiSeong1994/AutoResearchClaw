from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from paper_recommender.candidates import paper_key
from paper_recommender.config import Settings

_SAFE_PATH_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")


def _safe_md(value: Any) -> str:
    if value is None:
        return ""
    out = str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    out = re.sub(r"[<>\[\]]", "", out)
    return out.strip()


def _paper_url(p: dict[str, Any]) -> str | None:
    arxiv = p.get("arxiv_id")
    if isinstance(arxiv, str) and _ARXIV_ID_RE.match(arxiv):
        return f"https://arxiv.org/abs/{arxiv}"
    pid = p.get("paper_id") or p.get("id")
    if isinstance(pid, str) and _SAFE_PATH_ID_RE.match(pid):
        return f"https://jiphyeonjeon.kr/papers/{quote(pid, safe='')}"
    url = p.get("url")
    if isinstance(url, str) and url.startswith(("https://", "http://")) and "]" not in url and "(" not in url:
        return url
    return None


def render_weekly_report(
    settings: Settings,
    *,
    profile: dict[str, Any],
    soul_md: str | None,
    user_id: str | None,
    queries: list[dict[str, str]],
    candidates: list[dict[str, Any]],
    report: dict[str, Any],
    run_iso: str,
) -> str:
    by_id = {paper_key(p): p for p in candidates}
    week = datetime.fromisoformat(run_iso.replace("Z", "+00:00")).strftime("%G-W%V")
    lines: list[str] = [
        "---",
        f'date: "{run_iso[:10]}"',
        f'week: "{week}"',
        'source: paper-recommender',
        'report_type: weekly-soul-trends',
        "tags:",
        "  - paper-recommender",
        "  - weekly",
        "  - research-trends",
    ]
    if user_id:
        lines.append(f'user_id: "{_safe_md(user_id)}"')
    lines.extend(["---", "", f"# Weekly research trends — {week}", ""])
    lines.append("> Coverage caveat: " + _safe_md(report.get("coverage_caveat")))
    lines.append("")

    glance = _safe_md(report.get("at_a_glance"))
    if glance:
        lines.extend(["## At a glance", "", glance, ""])

    lines.extend(["## Search coverage", ""])
    if queries:
        for q in queries:
            lines.append(f"- **{_safe_md(q.get('axis'))}:** `{_safe_md(q.get('query'))}` — {_safe_md(q.get('rationale'))}")
    else:
        lines.append("- No generated queries were available.")
    lines.append("")

    lines.extend(["## Trend clusters", ""])
    clusters = report.get("clusters") or []
    if not clusters:
        lines.append("_No evidence-backed clusters were available this week._")
        lines.append("")
    for i, cluster in enumerate(clusters, 1):
        lines.append(f"### {i}. {_safe_md(cluster.get('title'))}")
        lines.append("")
        if cluster.get("summary"):
            lines.append(_safe_md(cluster.get("summary")))
            lines.append("")
        if cluster.get("why_it_matters"):
            lines.append(f"**Why it matters:** {_safe_md(cluster.get('why_it_matters'))}")
            lines.append("")
        lines.append("**Evidence papers**")
        for pid in cluster.get("paper_ids") or []:
            p = by_id.get(str(pid))
            if not p:
                continue
            title = _safe_md(p.get("title")) or str(pid)
            year = _safe_md(p.get("year") or "?")
            venue = _safe_md(p.get("venue") or p.get("source") or "-")
            url = _paper_url(p)
            title_part = f"[{title}]({url})" if url else title
            lines.append(f"- {title_part} ({year}, {venue})")
        lines.append("")

    weak = report.get("weak_signals") or []
    if weak:
        lines.extend(["## Weak signals / contradictions", ""])
        for item in weak:
            lines.append(f"- {_safe_md(item)}")
        lines.append("")

    top = candidates[: settings.weekly_report.top_papers]
    if top:
        lines.extend(["## Reading queue", ""])
        for i, p in enumerate(top, 1):
            title = _safe_md(p.get("title")) or "(untitled)"
            query = _safe_md(p.get("_trend_query"))
            lines.append(f"{i}. **{title}** — {query}")
        lines.append("")

    if soul_md:
        lines.extend([
            "<details><summary>SOUL context snapshot</summary>",
            "",
            _safe_md(soul_md),
            "",
            "</details>",
            "",
        ])
    return "\n".join(lines)


def write_weekly_artifacts(
    settings: Settings,
    *,
    profile: dict[str, Any],
    soul_md: str | None,
    user_id: str | None,
    queries: list[dict[str, str]],
    candidates: list[dict[str, Any]],
    report: dict[str, Any],
    run_iso: str,
) -> Path:
    now = datetime.fromisoformat(run_iso.replace("Z", "+00:00"))
    target = settings.artifacts_root / now.strftime(settings.weekly_report.output_subdir_fmt)
    target.mkdir(parents=True, exist_ok=True)
    note = render_weekly_report(
        settings,
        profile=profile,
        soul_md=soul_md,
        user_id=user_id,
        queries=queries,
        candidates=candidates,
        report=report,
        run_iso=run_iso,
    )
    (target / settings.weekly_report.note_filename).write_text(note, encoding="utf-8")
    raw = {
        "run_at": run_iso,
        "user_id": user_id,
        "profile": profile,
        "soul_present": bool(soul_md),
        "queries": queries,
        "candidate_count": len(candidates),
        "candidates": [
            {
                "paper_id": paper_key(p),
                "title": p.get("title"),
                "authors": p.get("authors"),
                "year": p.get("year"),
                "venue": p.get("venue") or p.get("source"),
                "url": p.get("url"),
                "pdf_url": p.get("pdf_url"),
                "doi": p.get("doi"),
                "arxiv_id": p.get("arxiv_id"),
                "trend_query": p.get("_trend_query"),
                "trend_axis": p.get("_trend_axis"),
            }
            for p in candidates[: settings.weekly_report.top_papers]
        ],
        "report": report,
    }
    (target / settings.weekly_report.raw_filename).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target
