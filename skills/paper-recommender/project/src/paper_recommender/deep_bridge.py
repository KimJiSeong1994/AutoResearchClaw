"""Bridge from selected clusters to AutoResearchClaw deep-research runs.

For each surviving cluster we shell out to the existing ``run-topic.sh`` on
EC2 (the verbatim CLI used in interactive mode), wait for it to finish, and
mine the resulting ``rc-YYYYMMDD-HHMMSS-<6hex>/`` artifact dir for the
synthesized markdown.

Empirical facts that shape this module (verified on EC2, 2026-05-02):

* A single deep run takes 25–35 minutes (sample = 2). ``timeout_sec``
  defaults to 2400s (40 min) — a previous default of 480s would have
  killed every run before stage-7.
* The CLI exit code is **not** a reliable success signal: a config-not-found
  error printed to stdout still exits 0. We instead validate completion via
  ``checkpoint.json`` and require ``last_completed_stage >= 7`` (SYNTHESIS).
* The "main report" file is whichever of these exists first:
  ``stage-07/synthesis.md`` → ``stage-08/hypotheses.md`` → other ``*.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from paper_recommender.clustering import Cluster
from paper_recommender.config import DeepBridgeSettings
from paper_recommender.sources._util import normalize_title_for_dedup

log = logging.getLogger(__name__)

# Stage at which the deep run is considered to have produced usable content.
# Stage-07 = SYNTHESIS in the researchclaw pipeline; lower stages are inputs
# (goal, problem-tree, decomposition, etc.).
_MIN_USABLE_STAGE = 7

# Files to try in priority order when extracting the main markdown excerpt.
_MAIN_REPORT_CANDIDATES: tuple[str, ...] = (
    "stage-07/synthesis.md",
    "stage-08/hypotheses.md",
    "stage-08/perspectives/contrarian.md",
    "stage-08/perspectives/innovator.md",
    "stage-09/exp_plan.md",
)

_EXCERPT_MAX_CHARS = 3000


# ─────────────────── data shapes ───────────────────


@dataclass
class DeepReport:
    cluster_id: int
    topic: str
    success: bool
    exit_code: int
    artifact_path: Path | None
    last_completed_stage: int
    last_completed_name: str
    main_report_path: Path | None
    markdown_excerpt: str
    wall_clock_sec: float
    error: str = ""


# Async runner contract: (args, timeout_sec) -> (exit_code, stdout, stderr).
# Tests inject a stub; production uses ``_default_subprocess_runner``.
SubprocessRunner = Callable[[list[str], float], Awaitable[tuple[int, bytes, bytes]]]


# ─────────────────── helpers shared with caller ───────────────────


_TOPIC_MAX_CHARS = 200


def cluster_topic_for_deep(cluster: Cluster) -> str:
    """Build the topic string passed to ``run-topic.sh``.

    Defense-in-depth sanitization: even though ``asyncio.create_subprocess_exec``
    + the ``"$*"`` quoting in ``run-topic.sh`` already prevent shell injection,
    we additionally strip control characters and a leading ``-`` so the topic
    can never be mistaken for a CLI flag if a future shell-script edit drops
    the quotes. Inputs include LLM-generated cluster labels and public paper
    titles — both attacker-influenced.
    """

    if cluster.label and cluster.label.strip():
        raw = cluster.label.strip()
    elif cluster.centroid_keywords:
        raw = ", ".join(cluster.centroid_keywords[:5])
    else:
        return f"cluster-{cluster.id}"

    sanitized = (
        raw.replace("\x00", "")
            .replace("\n", " ")
            .replace("\r", " ")
            .replace("\t", " ")
    )
    sanitized = sanitized.lstrip("-").strip()
    sanitized = sanitized[:_TOPIC_MAX_CHARS]
    return sanitized or f"cluster-{cluster.id}"


def cluster_dedup_key(cluster: Cluster) -> str:
    """Normalized key for cross-day deep-seen dedup in StateStore."""
    return normalize_title_for_dedup(cluster_topic_for_deep(cluster))


# ─────────────────── public entry point ───────────────────


async def run_deep_for_clusters(
    clusters: list[Cluster],
    settings: DeepBridgeSettings,
    *,
    on_progress: Callable[[int, "DeepReport"], None] | None = None,
    runner: SubprocessRunner | None = None,
) -> list[DeepReport]:
    """Run each cluster through the deep pipeline, sequentially by default.

    Continues on failure: a failed topic does not prevent later topics from
    running. ``on_progress`` is called once per topic the moment its report
    is finalized — used by the caller to incrementally append to the daily
    note so a partial failure still yields partial output.
    """

    if not settings.enabled:
        log.info("deep bridge disabled (settings.enabled=False)")
        return []
    if not clusters:
        return []

    runner = runner or _default_subprocess_runner
    semaphore = asyncio.Semaphore(max(1, settings.concurrency))

    async def _wrapped(idx: int, cluster: Cluster) -> DeepReport:
        async with semaphore:
            report = await _run_one(cluster, settings, runner=runner)
        if on_progress is not None:
            try:
                on_progress(idx, report)
            except Exception:
                log.exception("on_progress callback raised; continuing")
        return report

    reports: list[DeepReport] = []
    for idx, cluster in enumerate(clusters):
        reports.append(await _wrapped(idx, cluster))
    return reports


# ─────────────────── per-cluster runner ───────────────────


async def _run_one(
    cluster: Cluster,
    settings: DeepBridgeSettings,
    *,
    runner: SubprocessRunner,
) -> DeepReport:
    topic = cluster_topic_for_deep(cluster)
    script = Path(settings.run_topic_script).expanduser()
    artifacts_root = Path(settings.artifacts_root).expanduser()

    if not script.exists():
        return _fail(
            cluster, topic, exit_code=-1, wall=0.0,
            error=f"run_topic_script not found: {script}",
        )

    artifacts_root.mkdir(parents=True, exist_ok=True)

    # Snapshot existing rc-* dirs so we can detect the new one post-run.
    before: set[Path] = set(_iter_rc_dirs(artifacts_root))

    args = ["bash", str(script), topic]
    t0 = time.monotonic()
    timed_out = False
    exit_code = -4
    stdout: bytes = b""
    stderr: bytes = b""
    try:
        exit_code, stdout, stderr = await runner(args, settings.timeout_sec)
    except asyncio.TimeoutError:
        # Don't bail yet — researchclaw is agentic and often reaches stage-7
        # SYNTHESIS well before our hard cap. The subprocess was killed but
        # the artifact dir on disk may already contain a usable synthesis.md.
        # We fall through to the artifact-discovery path below; if the dir
        # has stage >= _MIN_USABLE_STAGE we treat the run as a soft success.
        timed_out = True
        exit_code = -3
    except OSError as e:
        return _fail(
            cluster, topic, exit_code=-2, wall=0.0,
            error=f"subprocess spawn failed: {e}",
        )

    wall = time.monotonic() - t0

    after = list(_iter_rc_dirs(artifacts_root))
    new_dirs = [d for d in after if d not in before]
    artifact = max(new_dirs, key=lambda d: d.stat().st_mtime) if new_dirs else None

    if artifact is None:
        # No new dir = the run produced nothing.
        if timed_out:
            return _fail(
                cluster, topic, exit_code=-3, wall=wall,
                error=f"timeout after {settings.timeout_sec}s; no artifact dir produced",
            )
        return _fail(
            cluster, topic, exit_code=exit_code, wall=wall,
            error=(
                f"no artifact dir produced (exit={exit_code}). "
                f"stderr: {_safe_decode(stderr)[:500]}"
            ),
        )

    cp = _read_checkpoint(artifact)
    last_stage = cp.get("last_completed_stage", 0) if isinstance(cp, dict) else 0
    last_name = cp.get("last_completed_name", "") if isinstance(cp, dict) else ""
    if not isinstance(last_stage, int):
        last_stage = 0

    if last_stage < _MIN_USABLE_STAGE:
        prefix = f"timeout after {settings.timeout_sec}s; " if timed_out else ""
        return DeepReport(
            cluster_id=cluster.id, topic=topic, success=False, exit_code=exit_code,
            artifact_path=artifact, last_completed_stage=last_stage,
            last_completed_name=last_name if isinstance(last_name, str) else "",
            main_report_path=None, markdown_excerpt="",
            wall_clock_sec=wall,
            error=(
                f"{prefix}completed stage {last_stage} below required {_MIN_USABLE_STAGE}; "
                f"stderr: {_safe_decode(stderr)[:500]}"
            ),
        )

    main_path, excerpt = _extract_excerpt(artifact)
    # When timed out but the artifact has a usable synthesis on disk, keep
    # success=True (the user gets the synthesized content) but record a soft
    # error string so the daily note's footer can flag the timing issue.
    soft_error = (
        f"timed out after {settings.timeout_sec}s but stage {last_stage} reached on disk"
        if timed_out
        else ""
    )
    return DeepReport(
        cluster_id=cluster.id, topic=topic, success=True, exit_code=exit_code,
        artifact_path=artifact, last_completed_stage=last_stage,
        last_completed_name=last_name if isinstance(last_name, str) else "",
        main_report_path=main_path, markdown_excerpt=excerpt,
        wall_clock_sec=wall,
        error=soft_error,
    )


# ─────────────────── helpers ───────────────────


def _fail(cluster: Cluster, topic: str, *, exit_code: int, wall: float, error: str) -> DeepReport:
    return DeepReport(
        cluster_id=cluster.id, topic=topic, success=False, exit_code=exit_code,
        artifact_path=None, last_completed_stage=0, last_completed_name="",
        main_report_path=None, markdown_excerpt="",
        wall_clock_sec=wall, error=error,
    )


def _iter_rc_dirs(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return [p for p in root.iterdir() if p.is_dir() and p.name.startswith("rc-")]


def _read_checkpoint(rc_dir: Path) -> dict | None:
    p = rc_dir / "checkpoint.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _extract_excerpt(rc_dir: Path) -> tuple[Path | None, str]:
    for candidate in _MAIN_REPORT_CANDIDATES:
        p = rc_dir / candidate
        if p.exists() and p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                if content.strip():
                    return p, content[:_EXCERPT_MAX_CHARS]
            except (OSError, UnicodeError):
                continue
    # Fallback: first non-empty *.md anywhere under the run dir.
    for md_path in sorted(rc_dir.rglob("*.md")):
        try:
            content = md_path.read_text(encoding="utf-8")
            if content.strip():
                return md_path, content[:_EXCERPT_MAX_CHARS]
        except (OSError, UnicodeError):
            continue
    return None, ""


def _safe_decode(b: bytes | None) -> str:
    if not b:
        return ""
    return b.decode("utf-8", errors="replace")


async def _default_subprocess_runner(
    args: list[str],
    timeout_sec: float,
) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pass
        raise
    return proc.returncode if proc.returncode is not None else -4, stdout, stderr


__all__ = [
    "DeepReport",
    "SubprocessRunner",
    "cluster_dedup_key",
    "cluster_topic_for_deep",
    "run_deep_for_clusters",
]
