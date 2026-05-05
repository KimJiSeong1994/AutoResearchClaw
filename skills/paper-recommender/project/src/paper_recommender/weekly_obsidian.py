from __future__ import annotations

import ast
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
_SENSITIVE_REPLACEMENTS = (
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^\s,'\")]+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 [REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
)


def _safe_md(value: Any) -> str:
    if value is None:
        return ""
    out = str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    out = re.sub(r"[<>\[\]]", "", out)
    return out.strip()




def _safe_md_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith(('[', '(')) and text.endswith((']', ')')):
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, (list, tuple)):
                return _safe_md_list(parsed)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            return [_safe_md(_strip_list_marker(line)) for line in lines]
        return [_safe_md(text)]
    if isinstance(value, dict):
        title = value.get("title") or value.get("label") or value.get("summary") or value.get("text")
        detail = value.get("detail") or value.get("rationale") or value.get("why")
        if title and detail:
            return [_safe_md(f"{title}: {detail}")]
        if title:
            return [_safe_md(title)]
        return [_safe_md(json.dumps(value, ensure_ascii=False, sort_keys=True))]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_safe_md_list(item))
        return [item for item in out if item]
    return [_safe_md(value)]


def _strip_list_marker(line: str) -> str:
    return re.sub(r"^\s*(?:[-*]\s+|\d+[.)]\s+)", "", line).strip()


def _render_at_a_glance(value: Any) -> list[str]:
    bullets = _safe_md_list(value)
    if not bullets:
        return []
    return [f"- {bullet}" for bullet in bullets]


def _first_text(*values: Any) -> str:
    for value in values:
        parts = _safe_md_list(value)
        if parts:
            return parts[0]
    return ""


def _render_cluster_role_bullets(cluster: dict[str, Any]) -> list[str]:
    summary = _first_text(cluster.get("summary"), cluster.get("description"))
    technical = _first_text(
        cluster.get("technical_point"),
        cluster.get("technical_points"),
        cluster.get("method"),
    )
    why_it_matters = _first_text(cluster.get("why_it_matters"))
    action = _first_text(cluster.get("researcher_action"), cluster.get("next_action"), cluster.get("action"))
    if not action:
        title = _safe_md(cluster.get("title")) or "this cluster"
        action = f"아래 근거 논문을 먼저 대조해 `{title}` 축의 후속 읽기 우선순위를 정한다."
        if why_it_matters:
            action = f"{action} 중요성 단서: {why_it_matters}"
    if not summary:
        summary = "근거 논문으로 확인 가능한 공통 주제를 묶은 클러스터다."
    if not technical:
        technical = "근거 논문 제목·연도·출처 수준에서 확인되는 방법론 단서만 사용한다."
    return [
        f"- **핵심 요약:** {summary}",
        f"- **기술 포인트:** {technical}",
        f"- **연구자 액션:** {action}",
    ]

def _sanitize_snapshot(value: str) -> str:
    sanitized = value
    for pattern, replacement in _SENSITIVE_REPLACEMENTS:
        sanitized = pattern.sub(replacement, sanitized)
    return _safe_md(sanitized)


def _snapshot_policy(settings: Settings) -> tuple[str, int]:
    soul_settings = getattr(settings, "soul", None)
    mode = str(getattr(soul_settings, "weekly_snapshot_mode", "redacted") or "redacted").lower()
    if mode not in {"redacted", "truncated", "full"}:
        mode = "redacted"
    max_chars = int(getattr(soul_settings, "weekly_snapshot_max_chars", 1200) or 1200)
    return mode, max(0, max_chars)




def _render_soul_snapshot(settings: Settings, soul_md: str | None) -> tuple[str | None, dict[str, Any]]:
    if not soul_md:
        return None, {"mode": "absent", "included": False, "chars": 0, "truncated": False}

    mode, max_chars = _snapshot_policy(settings)
    if mode == "redacted":
        return None, {"mode": mode, "included": False, "chars": 0, "truncated": False}

    sanitized = _sanitize_snapshot(soul_md)
    had_redaction = "REDACTED" in sanitized
    truncated = mode == "truncated" and len(sanitized) > max_chars
    if truncated:
        suffix = " … [truncated]"
        if had_redaction and "REDACTED" not in sanitized[:max_chars]:
            suffix = " REDACTED" + suffix
        sanitized = sanitized[:max_chars].rstrip() + suffix
    return sanitized, {
        "mode": mode,
        "included": True,
        "chars": len(sanitized),
        "truncated": truncated,
    }



def _axis_key(value: Any) -> str:
    axis = _safe_md(value)
    return axis or "unspecified"


def _axis_lookup_key(value: Any) -> str:
    return _axis_key(value).lower()


def _build_soul_axis_coverage(
    queries: list[dict[str, str]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize candidate evidence coverage for generated SOUL/profile axes.

    Query generation already projects the compact SOUL/profile into named search
    axes. This telemetry keeps that contract visible in the rendered note and raw
    artifact: every generated axis is shown, and axes with no candidate evidence
    are explicitly marked missing instead of silently disappearing. Candidate-only
    axes are ignored because coverage is measured against configured/derived axes.
    """

    ordered_keys: list[str] = []
    labels: dict[str, str] = {}
    query_by_key: dict[str, str] = {}
    for q in queries:
        axis = _axis_key(q.get("axis"))
        key = axis.lower()
        if key not in labels:
            ordered_keys.append(key)
            labels[key] = axis
            query_by_key[key] = _safe_md(q.get("query"))

    counts: dict[str, int] = {key: 0 for key in ordered_keys}
    examples: dict[str, str] = {}
    for p in candidates:
        key = _axis_lookup_key(p.get("_trend_axis") or p.get("trend_axis"))
        if key not in counts:
            continue
        counts[key] += 1
        examples.setdefault(key, _safe_md(p.get("title")))

    return [
        {
            "axis": labels[key],
            "candidate_count": counts.get(key, 0),
            "status": "covered" if counts.get(key, 0) > 0 else "missing",
            "query": query_by_key.get(key, ""),
            "example_title": examples.get(key, ""),
        }
        for key in ordered_keys
    ]


def _render_soul_axis_coverage(coverage: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ["## SOUL-axis coverage", ""]
    if not coverage:
        lines.append("- No SOUL/profile search axes were generated, so axis coverage could not be measured.")
        lines.append("")
        return lines

    missing = [item for item in coverage if item.get("status") == "missing"]
    for item in coverage:
        axis = _safe_md(item.get("axis")) or "unspecified"
        count = int(item.get("candidate_count") or 0)
        query = _safe_md(item.get("query"))
        example = _safe_md(item.get("example_title"))
        if count > 0:
            suffix = f"; example: {example}" if example else ""
            lines.append(f"- ✅ **{axis}** — covered by {count} candidate(s){suffix}.")
        else:
            suffix = f"; query: `{query}`" if query else ""
            lines.append(f"- ⚠️ **{axis}** — missing candidate evidence{suffix}.")
    if missing:
        axes = ", ".join(_safe_md(item.get("axis")) or "unspecified" for item in missing)
        lines.append(f"- Missing axes to revisit: {axes}.")
    lines.append("")
    return lines

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
    soul_card: str | None = None,
    soul_provenance: dict[str, Any] | None = None,
    queries: list[dict[str, str]],
    candidates: list[dict[str, Any]],
    report: dict[str, Any],
    run_iso: str,
) -> str:
    by_id = {paper_key(p): p for p in candidates}
    week = datetime.fromisoformat(run_iso.replace("Z", "+00:00")).strftime("%G-W%V")
    soul_provenance = dict(soul_provenance or {})
    soul_source = str(soul_provenance.get("source") or ("soul" if soul_md else "absent"))
    fallback_used = bool(soul_provenance.get("fallback_used"))
    soul_snapshot, snapshot_policy = _render_soul_snapshot(settings, soul_md)
    soul_provenance.setdefault("snapshot_policy", snapshot_policy)
    report_type = "weekly-soul-trends" if soul_source == "soul" else "weekly-profile-trends"
    lines: list[str] = [
        "---",
        f'date: "{run_iso[:10]}"',
        f'week: "{week}"',
        'source: paper-recommender',
        f"report_type: {report_type}",
        f'soul_source: "{_safe_md(soul_source)}"',
        f"soul_fallback_used: {str(fallback_used).lower()}",
        "tags:",
        "  - paper-recommender",
        "  - weekly",
        "  - research-trends",
    ]
    if soul_provenance.get("active_sha256"):
        lines.append(f'soul_sha256: "{_safe_md(soul_provenance.get("active_sha256"))}"')
    if soul_provenance.get("soul_last_updated"):
        lines.append(f'soul_last_updated: "{_safe_md(soul_provenance.get("soul_last_updated"))}"')
    if user_id:
        lines.append(f'user_id: "{_safe_md(user_id)}"')
    lines.extend(["---", "", f"# Weekly research trends — {week}", ""])
    caveat = _safe_md(report.get("coverage_caveat"))
    if fallback_used:
        caveat = (caveat + " " if caveat else "") + (
            f"SOUL source is `{_safe_md(soul_source)}`; profile/narrative fallback was used."
        )
    lines.append("> Coverage caveat: " + caveat)
    lines.append("")

    lines.extend(["## SOUL basis", ""])
    if soul_source == "soul":
        lines.append(
            "- Active SOUL loaded"
            + (f" for `{_safe_md(user_id)}`" if user_id else "")
            + (f"; last updated `{_safe_md(soul_provenance.get('soul_last_updated'))}`" if soul_provenance.get("soul_last_updated") else "")
            + "."
        )
    elif fallback_used:
        lines.append(f"- ⚠️ SOUL unavailable; `{_safe_md(soul_source)}` was used for this run.")
    else:
        lines.append("- SOUL was not available for this run.")
    if soul_provenance.get("active_bytes") is not None:
        lines.append(f"- Active context bytes: `{_safe_md(soul_provenance.get('active_bytes'))}`; compact card bytes: `{_safe_md(soul_provenance.get('compact_card_bytes'))}`.")
    lines.append("")

    glance_lines = _render_at_a_glance(report.get("at_a_glance"))
    if glance_lines:
        lines.extend(["## At a glance", "", *glance_lines, ""])

    lines.extend(["## Search coverage", ""])
    if queries:
        for q in queries:
            lines.append(f"- **{_safe_md(q.get('axis'))}:** `{_safe_md(q.get('query'))}` — {_safe_md(q.get('rationale'))}")
    else:
        lines.append("- No generated queries were available.")
    lines.append("")

    soul_axis_coverage = _build_soul_axis_coverage(queries, candidates)
    lines.extend(_render_soul_axis_coverage(soul_axis_coverage))

    lines.extend(["## Trend clusters", ""])
    clusters = report.get("clusters") or []
    if not clusters:
        lines.append("_No evidence-backed clusters were available this week._")
        lines.append("")
    for i, cluster in enumerate(clusters, 1):
        lines.append(f"### {i}. {_safe_md(cluster.get('title'))}")
        lines.append("")
        lines.extend(_render_cluster_role_bullets(cluster))
        lines.append("")
        lines.append("**근거 논문**")
        evidence_count = 0
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
            evidence_count += 1
        if evidence_count == 0:
            lines.append("- 근거 논문 링크를 후보 목록에서 확인하지 못했습니다.")
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

    if soul_card:
        lines.extend([
            "<details><summary>Briefing SOUL card</summary>",
            "",
            _safe_md(soul_card),
            "",
            "</details>",
            "",
        ])

    if soul_snapshot:
        lines.extend([
            "<details><summary>SOUL context snapshot</summary>",
            "",
            soul_snapshot,
            "",
            "</details>",
            "",
        ])
    elif soul_md:
        lines.extend([
            "<!-- SOUL context snapshot omitted by soul.weekly_snapshot_mode; soul_sha256/provenance retained. -->",
            "",
        ])
    return "\n".join(lines)


def write_weekly_artifacts(
    settings: Settings,
    *,
    profile: dict[str, Any],
    soul_md: str | None,
    user_id: str | None,
    soul_card: str | None = None,
    soul_provenance: dict[str, Any] | None = None,
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
        soul_card=soul_card,
        soul_provenance=soul_provenance,
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
        "soul_present": bool((soul_provenance or {}).get("present", bool(soul_md))),
        "soul_source": (soul_provenance or {}).get("source") or ("soul" if soul_md else "absent"),
        "soul_fallback_used": bool((soul_provenance or {}).get("fallback_used")),
        "soul_provenance": soul_provenance or {},
        "soul_card": soul_card,
        "queries": queries,
        "soul_axis_coverage": _build_soul_axis_coverage(queries, candidates),
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
