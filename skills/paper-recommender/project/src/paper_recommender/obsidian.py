from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import re
from urllib.parse import quote

from paper_recommender.candidates import paper_key
from paper_recommender.config import Settings
from paper_recommender.rerank import score_stats


def _safe_md(s: str | None) -> str:
    """Defang LLM-generated text against Markdown/HTML injection.

    The reason field flows from the LLM directly into Obsidian. A malicious or
    sloppy LLM output containing pipes, brackets, or angle tags could break
    table rendering or inject links. Strip the smallest set that breaks
    Markdown structure while keeping Korean text intact.
    """
    if not s:
        return ""
    out = str(s).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    out = re.sub(r"[<>\[\]]", "", out)
    return out.strip()


_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
_SAFE_PATH_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def _arxiv_url(p: dict[str, Any]) -> str | None:
    aid = p.get("arxiv_id")
    if isinstance(aid, str) and _ARXIV_ID_RE.match(aid):
        return f"https://arxiv.org/abs/{aid}"
    pid = p.get("paper_id") or p.get("id")
    if isinstance(pid, str) and _ARXIV_ID_RE.match(pid):
        return f"https://arxiv.org/abs/{pid}"
    return None


def _jh_url(p: dict[str, Any]) -> str | None:
    pid = p.get("paper_id") or p.get("id")
    if isinstance(pid, str) and _SAFE_PATH_ID_RE.match(pid):
        return f"https://jiphyeonjeon.kr/papers/{quote(pid, safe='')}"
    return None


def _format_authors(authors: Any) -> str:
    if isinstance(authors, list):
        top = [_safe_md(str(a)) for a in authors[:4]]
        tail = " et al." if len(authors) > 4 else ""
        return ", ".join(a for a in top if a) + tail
    return _safe_md(str(authors or ""))


def _render_pick(i: int, p: dict[str, Any]) -> list[str]:
    title = _safe_md(p.get("title")) or "(no title)"
    score = p.get("score")
    reason = _safe_md(p.get("reason"))
    year = _safe_md(str(p.get("year") or "?"))
    venue = _safe_md(str(p.get("venue") or p.get("source") or "-"))
    authors = _format_authors(p.get("authors"))
    arxiv = _arxiv_url(p)
    jh = _jh_url(p)

    lines = [f"#### {i}. {title}", ""]
    lines.append(f"- **Score:** {score}")
    lines.append(f"- **Year / venue:** {year} / {venue}")
    if authors:
        lines.append(f"- **Authors:** {authors}")
    link_bits: list[str] = []
    if arxiv:
        link_bits.append(f"[arXiv]({arxiv})")
    if jh:
        link_bits.append(f"[집현전]({jh})")
    if link_bits:
        lines.append(f"- **Links:** {' · '.join(link_bits)}")
    if reason:
        lines.append(f"- **추천 이유:** {reason}")
    abs_ = (p.get("abstract") or p.get("summary") or "").strip()
    if abs_:
        abs_short = " ".join(abs_.split())
        if len(abs_short) > 600:
            abs_short = abs_short[:599] + "…"
        lines.append("")
        lines.append(f"> {_safe_md(abs_short)}")
    lines.append("")
    return lines


def _overlap_metric(variants_picks: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    if len(variants_picks) != 2:
        return None
    keys = list(variants_picks.keys())
    a_ids = {paper_key(p) for p in variants_picks[keys[0]]}
    b_ids = {paper_key(p) for p in variants_picks[keys[1]]}
    inter = a_ids & b_ids
    union = a_ids | b_ids
    return {
        "variant_a": keys[0],
        "variant_b": keys[1],
        "count_a": len(a_ids),
        "count_b": len(b_ids),
        "shared": len(inter),
        "jaccard": (len(inter) / len(union)) if union else 1.0,
        "only_in_a": sorted(a_ids - b_ids),
        "only_in_b": sorted(b_ids - a_ids),
    }


def render_note(
    settings: Settings,
    profile: dict[str, Any],
    narrative_md: str | None,
    soul_md: str | None,
    user_id: str | None,
    variants_picks: dict[str, list[dict[str, Any]]],
    run_iso: str,
) -> str:
    today = run_iso[:10]
    lines: list[str] = []
    lines.append("---")
    lines.append(f'date: "{today}"')
    lines.append(f"variants: {sorted(variants_picks.keys())}")
    if user_id:
        lines.append(f'user_id: "{user_id}"')
    lines.append('source: paper-recommender')
    lines.append("tags:")
    lines.append("  - paper-recommender")
    lines.append("  - daily")
    lines.append("---")
    lines.append("")
    lines.append(f"# Paper recommendations — {today}")
    lines.append("")

    overlap = _overlap_metric(variants_picks)
    if overlap:
        lines.append("## A/B comparison")
        lines.append("")
        lines.append(
            f"- **{overlap['variant_a']}**: {overlap['count_a']} picks · "
            f"**{overlap['variant_b']}**: {overlap['count_b']} picks"
        )
        lines.append(
            f"- **Shared**: {overlap['shared']} · "
            f"**Jaccard**: {overlap['jaccard']:.2f}"
        )
        lines.append("")

    # Score-distribution sanity row: collapse manifests as std≈0 and spread≈0;
    # a healthy listwise rerank with cross-batch modulation shows std > 0.
    has_score_stats = any(
        any(p.get("score") is not None for p in picks) for picks in variants_picks.values()
    )
    if has_score_stats:
        lines.append("## Score distribution (collapse check)")
        lines.append("")
        lines.append("| variant | n | mean | std | min–max | spread |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for v, picks in variants_picks.items():
            if not picks:
                continue
            stats = score_stats(picks)
            lines.append(
                f"| {v} | {stats['n']} | {stats['mean']:.2f} | {stats['std']:.2f} "
                f"| {stats['min']:.2f}–{stats['max']:.2f} | {stats['spread']:.2f} |"
            )
        lines.append("")

    lines.append("## Profile snapshot")
    lines.append("")
    for b in profile.get("interests", []):
        lines.append(f"- {b}")
    kws = profile.get("keywords") or []
    if kws:
        lines.append("")
        lines.append(f"**Keywords:** {', '.join(kws)}")
    methods = profile.get("methodology_focus") or []
    if methods:
        lines.append(f"**Methods:** {', '.join(methods)}")
    if soul_md:
        lines.append("")
        lines.append(f"<details><summary>Soul (evolving, {len(soul_md.encode('utf-8'))} bytes)</summary>")
        lines.append("")
        lines.append(soul_md)
        lines.append("")
        lines.append("</details>")
    if narrative_md and not soul_md:
        lines.append("")
        lines.append("<details><summary>Narrative profile</summary>")
        lines.append("")
        lines.append(narrative_md)
        lines.append("")
        lines.append("</details>")
    lines.append("")

    for variant, picks in variants_picks.items():
        lines.append(f"## Picks — {variant}")
        lines.append("")
        if not picks:
            lines.append("_No recommendations cleared the score threshold for this variant._")
            lines.append("")
            continue
        for i, p in enumerate(picks, 1):
            lines.extend(_render_pick(i, p))

    return "\n".join(lines)


def write_artifacts(
    settings: Settings,
    profile: dict[str, Any],
    narrative_md: str | None,
    soul_md: str | None,
    user_id: str | None,
    candidates: list[dict[str, Any]],
    variants_picks: dict[str, list[dict[str, Any]]],
) -> Path:
    now = datetime.now()
    run_iso = now.isoformat(timespec="seconds")
    subdir = now.strftime(settings.output.daily_subdir_fmt)
    target = settings.artifacts_root / subdir
    target.mkdir(parents=True, exist_ok=True)

    note = render_note(
        settings, profile, narrative_md, soul_md, user_id, variants_picks, run_iso
    )
    (target / settings.output.note_filename).write_text(note, encoding="utf-8")

    if narrative_md:
        (target / "profile.md").write_text(narrative_md, encoding="utf-8")

    if soul_md and user_id:
        souls_dir = target / "souls"
        souls_dir.mkdir(parents=True, exist_ok=True)
        (souls_dir / f"{user_id}.md").write_text(soul_md, encoding="utf-8")

    raw = {
        "run_at": run_iso,
        "user_id": user_id,
        "profile": profile,
        "narrative_present": bool(narrative_md),
        "soul_present": bool(soul_md),
        "soul_bytes": len(soul_md.encode("utf-8")) if soul_md else 0,
        "scoring_mode": settings.rerank.scoring_mode,
        "score_stats": {v: score_stats(picks) for v, picks in variants_picks.items()},
        "variants": {
            v: [
                {
                    "paper_id": paper_key(p),
                    "title": p.get("title"),
                    "authors": p.get("authors"),
                    "year": p.get("year"),
                    "venue": p.get("venue") or p.get("source"),
                    "source": p.get("source"),
                    "url": p.get("url"),
                    "pdf_url": p.get("pdf_url"),
                    "doi": p.get("doi"),
                    "arxiv_id": p.get("arxiv_id"),
                    "score": p.get("score"),
                    "rank": p.get("_rank"),
                    "anchor": p.get("_anchor"),
                    "reason": p.get("reason"),
                }
                for p in picks
            ]
            for v, picks in variants_picks.items()
        },
        "overlap": _overlap_metric(variants_picks),
        "candidate_count": len(candidates),
    }
    (target / settings.output.raw_filename).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target
