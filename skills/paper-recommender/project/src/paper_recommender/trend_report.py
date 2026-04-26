from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from paper_recommender.candidates import paper_key
from paper_recommender.config import Settings
from paper_recommender.llm import OpenClawLLM


def _safe_text(value: Any, *, limit: int = 1000) -> str:
    text = str(value or "").replace("<", "&lt;").replace(">", "&gt;")
    return " ".join(text.split())[:limit]


def _paper_digest(p: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "paper_id": paper_key(p),
        "title": _safe_text(p.get("title"), limit=220),
        "year": p.get("year"),
        "venue": p.get("venue") or p.get("source"),
        "abstract": _safe_text(p.get("abstract") or p.get("summary"), limit=900),
        "trend_axis": p.get("_trend_axis"),
        "trend_query": p.get("_trend_query"),
        "rank_hint": index + 1,
    }


def validate_trend_report(report: dict[str, Any], valid_ids: set[str], min_evidence: int) -> dict[str, Any]:
    clusters_out: list[dict[str, Any]] = []
    raw_clusters = report.get("clusters") if isinstance(report, dict) else None
    if isinstance(raw_clusters, list):
        for cluster in raw_clusters:
            if not isinstance(cluster, dict):
                continue
            ids: list[str] = []
            for pid in cluster.get("paper_ids") or []:
                pid_s = str(pid)
                if pid_s in valid_ids and pid_s not in ids:
                    ids.append(pid_s)
            if not ids:
                continue
            if len(ids) < min_evidence and len(valid_ids) >= min_evidence:
                continue
            clusters_out.append({
                "title": _safe_text(cluster.get("title") or "Trend cluster", limit=120),
                "summary": _safe_text(cluster.get("summary") or "", limit=900),
                "why_it_matters": _safe_text(cluster.get("why_it_matters") or "", limit=700),
                "paper_ids": ids,
            })
    return {
        "generated_at": report.get("generated_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "at_a_glance": _safe_text(report.get("at_a_glance") or "", limit=1000),
        "clusters": clusters_out,
        "weak_signals": [_safe_text(x, limit=300) for x in (report.get("weak_signals") or [])[:6] if x],
        "coverage_caveat": _safe_text(
            report.get("coverage_caveat")
            or "Source coverage is limited to Jiphyeonjeon search results and is not an exhaustive web crawl.",
            limit=500,
        ),
    }


def fallback_trend_report(settings: Settings, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    by_axis: dict[str, list[str]] = {}
    for p in candidates[: settings.weekly_report.top_papers]:
        axis = _safe_text(p.get("_trend_axis") or p.get("_trend_query") or "retrieved evidence", limit=80)
        by_axis.setdefault(axis, [])
        pid = paper_key(p)
        if pid not in by_axis[axis]:
            by_axis[axis].append(pid)
    clusters = [
        {
            "title": axis,
            "summary": "Retrieved papers cluster around this SOUL/profile-derived search axis.",
            "why_it_matters": "Use this as an evidence queue for manual weekly review; synthesis fallback was used.",
            "paper_ids": ids[: max(settings.weekly_report.min_evidence_per_cluster, 3)],
        }
        for axis, ids in by_axis.items()
        if ids
    ]
    return validate_trend_report(
        {
            "at_a_glance": "OpenClaw synthesis was unavailable, so clusters were grouped deterministically by search axis.",
            "clusters": clusters,
            "weak_signals": [],
            "coverage_caveat": "Source coverage is limited to Jiphyeonjeon search results and is not an exhaustive web crawl.",
        },
        {paper_key(p) for p in candidates},
        1,
    )


async def synthesize_trend_report(
    settings: Settings,
    soul_md: str | None,
    profile: dict[str, Any],
    queries: list[dict[str, str]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not candidates:
        return validate_trend_report(
            {
                "at_a_glance": "No eligible papers were retrieved for this weekly window.",
                "clusters": [],
                "weak_signals": [],
                "coverage_caveat": "No source evidence was available from Jiphyeonjeon for the generated queries.",
            },
            set(),
            1,
        )
    fallback = fallback_trend_report(settings, candidates)
    digests = [_paper_digest(p, i) for i, p in enumerate(candidates[: settings.weekly_report.top_papers])]
    valid_ids = {paper_key(p) for p in candidates}
    system = (
        "You write a weekly research trend report from provided evidence only. "
        "Do not cite paper IDs that are not in the evidence. Return strict JSON with keys: "
        "generated_at, at_a_glance, clusters[{title,summary,why_it_matters,paper_ids}], "
        "weak_signals, coverage_caveat."
    )
    user = {
        "profile": profile,
        "soul_md": str(soul_md or "")[: settings.soul.max_bytes],
        "queries": queries,
        "evidence_papers": digests,
        "coverage_rule": "Only Jiphyeonjeon search evidence is available; mention that limitation.",
    }
    try:
        async with OpenClawLLM(settings.openclaw) as llm:
            parsed = await llm.chat_json(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
    except Exception:
        return fallback
    if not isinstance(parsed, dict):
        return fallback
    report = validate_trend_report(parsed, valid_ids, settings.weekly_report.min_evidence_per_cluster)
    return report if report["clusters"] or not candidates else fallback
