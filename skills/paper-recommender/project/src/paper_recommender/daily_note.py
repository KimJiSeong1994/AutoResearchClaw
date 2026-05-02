"""Compose the unified Obsidian daily-research markdown note.

The note is a single file at ``{artifacts_root}/{date}/daily-research.md``
with five sections: frontmatter, sources table, clusters overview, skipped
clusters, deep reports. The deep-report sections inline the first
~3000 chars of the synthesized stage-7 markdown plus a footer linking to
the full ``rc-*`` artifact tree on EC2.

Lives next to the legacy ``recommendations.md`` written by ``obsidian.py``;
filename is intentionally distinct so the legacy daily/weekly modes are
unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass

from paper_recommender.clustering import Cluster
from paper_recommender.deep_bridge import DeepReport
from paper_recommender.sources._util import normalize_title_for_dedup


@dataclass(frozen=True)
class SkippedCluster:
    cluster: Cluster
    reason: str


def _safe(text: str | None, *, limit: int = 400) -> str:
    """Defang Obsidian/markdown-injection-prone characters in metadata fields."""
    if not text:
        return ""
    s = (
        text.replace("\n", " ")
            .replace("|", "\\|")
            .replace("[[", "[ [")
            .replace("]]", "] ]")
    )
    return s.strip()[:limit]


def _slug(text: str) -> str:
    return normalize_title_for_dedup(text).replace(" ", "-")[:48] or "cluster"


def compose_daily_note(
    *,
    run_iso: str,
    source_stats: dict[str, int],
    candidate_count: int,
    clusters: list[Cluster],
    deep_reports: list[DeepReport],
    skipped: list[SkippedCluster] | None = None,
    used_fallback: bool = False,
    wall_clock_sec: float = 0.0,
) -> str:
    skipped = skipped or []
    today = run_iso[:10]

    lines: list[str] = []

    # ── Frontmatter ──
    lines.extend([
        "---",
        f'date: "{today}"',
        "source: daily-research",
        "tags:",
        "  - daily-research",
        "---",
        "",
        f"# Daily Research — {today}",
        "",
        f"_Run started: {run_iso}_",
        "",
    ])

    # ── Sources ──
    lines.append("## Sources")
    lines.append("")
    if source_stats:
        lines.append("| Source | Candidates |")
        lines.append("|---|---|")
        for src in sorted(source_stats):
            lines.append(f"| {_safe(src)} | {source_stats[src]} |")
        lines.append(f"| **Total** | **{candidate_count}** |")
    else:
        lines.append("_No source data this run._")
    lines.append("")

    # ── Fallback warning ──
    if used_fallback:
        lines.extend([
            "> ⚠️ **Embedding fallback active** — clusters are flat-bucket only "
            "(no semantic clustering this run). Deep reports are skipped.",
            "",
        ])

    # ── Clusters overview ──
    lines.append(f"## Clusters ({len(clusters)})")
    lines.append("")
    if not clusters:
        lines.append("_No clusters formed._")
    else:
        for c in clusters:
            label = _safe(c.label) if c.label else f"Cluster {c.id}"
            kw_str = ", ".join(c.centroid_keywords[:5])
            summary = _safe(c.summary, limit=160)
            head = f"- **{label}** ({len(c.items)} items)"
            if summary:
                head += f" — _{summary}_"
            lines.append(head)
            if kw_str:
                lines.append(f"  - keywords: `{_safe(kw_str)}`")
    lines.append("")

    # ── Skipped clusters ──
    if skipped:
        lines.append("## Skipped (deep-seen recently)")
        lines.append("")
        for sc in skipped:
            lbl = _safe(sc.cluster.label) if sc.cluster.label else f"Cluster {sc.cluster.id}"
            lines.append(f"- ~~{lbl}~~ — {_safe(sc.reason)}")
        lines.append("")

    # ── Deep reports ──
    if deep_reports:
        lines.append("## Deep Reports")
        lines.append("")
        cluster_by_id = {c.id: c for c in clusters}
        for r in deep_reports:
            lines.append(f"### {_safe(r.topic)} #daily-research/{_slug(r.topic)}")
            lines.append("")
            if r.success:
                co = cluster_by_id.get(r.cluster_id)
                if co is not None and co.summary:
                    lines.append(f"_{_safe(co.summary, limit=400)}_")
                    lines.append("")
                if r.markdown_excerpt:
                    lines.append(r.markdown_excerpt)
                    lines.append("")
                meta = [
                    f"wall-clock: {r.wall_clock_sec:.0f}s",
                    f"stage: {r.last_completed_stage} ({_safe(r.last_completed_name) or 'unknown'})",
                ]
                lines.append("_" + " · ".join(meta) + "_")
                if r.artifact_path is not None:
                    lines.append(f"_Full artifacts: `{r.artifact_path}`_")
            else:
                err = _safe(r.error, limit=500) or "(no error message)"
                lines.append(f"❌ **Failed:** {err}")
                lines.append(f"_wall-clock: {r.wall_clock_sec:.0f}s · exit={r.exit_code}_")
            lines.append("")

    # ── Footer ──
    deep_ok = sum(1 for r in deep_reports if r.success)
    lines.append("---")
    lines.append("")
    lines.append(
        f"_Pipeline wall-clock: {wall_clock_sec:.0f}s · "
        f"deep success: {deep_ok}/{len(deep_reports)} · "
        f"clusters: {len(clusters)} · candidates: {candidate_count}_"
    )
    lines.append("")

    return "\n".join(lines)


__all__ = ["SkippedCluster", "compose_daily_note"]
