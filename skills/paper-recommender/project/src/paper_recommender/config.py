from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from paper_recommender.sources import SourceLimits
from paper_recommender.sources.google_newsletters import GoogleNewsletterSettings
from paper_recommender.sources.manual_links import ManualLinkSettings
from paper_recommender.sources.rss import RssFeedSettings


@dataclass
class JiphySettings:
    base_url: str
    token_env: str
    timeout_sec: int

    @property
    def token(self) -> str:
        val = os.environ.get(self.token_env)
        if not val:
            raise RuntimeError(f"missing env {self.token_env}")
        return val.strip()


@dataclass
class OpenClawSettings:
    base_url: str
    token_env: str
    primary_model: str
    fallback_model: str
    timeout_sec: int

    @property
    def token(self) -> str:
        return (os.environ.get(self.token_env) or "").strip()


@dataclass
class ProfileSettings:
    cache_ttl_days: int
    seed_topics: list[str]
    max_bookmarks_for_profile: int
    narrative_enabled: bool = True


@dataclass
class CandidateSettings:
    per_keyword: int
    related_per_bookmark: int
    related_from_top_n_bookmarks: int
    year_start: int | None
    year_end: int | None
    total_cap: int


@dataclass
class RerankSettings:
    batch_size: int
    top_k: int
    min_score: float
    temperature: float
    mode: str = "ab"  # "keywords" | "narrative" | "ab"
    # Scoring mode: "listwise" (forced rank 1..N per batch, prevents 5/5 collapse)
    # or "pointwise" (legacy: per-item 1-5 score, prone to collapse on pre-filtered
    # candidates). Listwise is the default after observing collapse on first run.
    scoring_mode: str = "listwise"
    # When the search backend returns a cross-encoder relevance score per candidate
    # (e.g. jiphyeonjeon's `_cross_encoder_score` in 0..1), expose it inline as an
    # anchor in the rerank prompt. Helps the LLM differentiate within tight batches.
    use_relevance_anchor: bool = True


@dataclass
class SeenSettings:
    cooldown_days: int


@dataclass
class SoulSettings:
    enabled: bool = True
    update_cadence_days: int = 1
    max_bytes: int = 3072
    compact_at_bytes: int = 2560
    include_recent_picks_days: int = 5


@dataclass
class DecaySettings:
    enabled: bool = True
    half_life_days: int = 60


@dataclass
class FeedbackSettings:
    enabled: bool = True
    lookback_days: int = 7
    max_file_kb: int = 512
    inbox_subdir: str = "feedback_inbox"


@dataclass
class OutputSettings:
    artifacts_dir: str
    daily_subdir_fmt: str
    note_filename: str
    raw_filename: str


@dataclass
class WeeklyReportSettings:
    enabled: bool = True
    cadence_days: int = 7
    max_queries: int = 10
    per_query: int = 20
    candidate_cap: int = 120
    top_papers: int = 20
    weekly_seen_cooldown_days: int = 60
    output_subdir_fmt: str = "weekly/%G-W%V"
    note_filename: str = "research-trends.md"
    raw_filename: str = "raw.json"
    min_evidence_per_cluster: int = 2
    year_start: int | None = None
    year_end: int | None = None


@dataclass
class JiphyAuthSettings:
    """Login-based jiphyeonjeon auth.

    Source-of-truth env vars are ``JIPHYEONJEON_USERNAME`` /
    ``JIPHYEONJEON_PASSWORD``. The dataclass itself stores only the env-var
    names so it never holds secrets.
    """

    base_url: str = "https://jiphyeonjeon.kr"
    username_env: str = "JIPHYEONJEON_USERNAME"
    password_env: str = "JIPHYEONJEON_PASSWORD"
    timeout_sec: float = 30.0

    @property
    def username(self) -> str:
        val = os.environ.get(self.username_env)
        if not val:
            raise RuntimeError(f"missing env {self.username_env}")
        return val.strip()

    @property
    def password(self) -> str:
        val = os.environ.get(self.password_env)
        if not val:
            raise RuntimeError(f"missing env {self.password_env}")
        return val


@dataclass
class SourceSettings:
    enabled: list[str] = field(default_factory=list)
    limits: SourceLimits = field(default_factory=SourceLimits)
    rss_feeds: list[str] = field(default_factory=list)
    rss: RssFeedSettings = field(default_factory=RssFeedSettings)
    manual_links: ManualLinkSettings = field(default_factory=ManualLinkSettings)
    google_newsletters: GoogleNewsletterSettings = field(
        default_factory=GoogleNewsletterSettings
    )


@dataclass
class ClusterSettings:
    max_clusters: int = 3
    embedding_model: str = "openclaw/clawbridge"
    embedding_endpoint: str = "/v1/embeddings"


@dataclass
class DeepBridgeSettings:
    enabled: bool = True
    concurrency: int = 1
    # Empirical 2026-05-02: Real runs hit the 2400s (40 min) cap. researchclaw
    # is agentic and proceeds to stage 9-10 (EXPERIMENT_DESIGN/CODE_RUN) even
    # after stage-7 SYNTHESIS is on disk. With deep_bridge now harvesting
    # artifacts after timeout (when stage >= 7), the cap is a soft cutoff,
    # not a fail point — but raising it lets researchclaw finish more often.
    timeout_sec: int = 3600
    # `researchclaw run --mode` knob. `full-auto` = current default behavior.
    # `express` is faster but shallower (untested in this project as of Phase C).
    mode: str = "full-auto"
    # Verified path on EC2 (2026-05-02): script lives directly under workspace/skills,
    # NOT under projects/AutoResearchClaw/skills. The script itself cd's into the
    # AutoResearchClaw project dir before invoking researchclaw.
    run_topic_script: str = "~/.openclaw/workspace/skills/researchclaw/run-topic.sh"
    artifacts_root: str = "~/.openclaw/workspace/projects/AutoResearchClaw/artifacts"


@dataclass
class DailyResearchSettings:
    """Top-level container for the multi-source daily-research pipeline.

    Optional in YAML. When the section is absent the legacy daily/weekly
    pipelines run unchanged.
    """

    sources: SourceSettings = field(default_factory=SourceSettings)
    cluster: ClusterSettings = field(default_factory=ClusterSettings)
    deep: DeepBridgeSettings = field(default_factory=DeepBridgeSettings)
    auth: JiphyAuthSettings = field(default_factory=JiphyAuthSettings)
    # Days a cluster is suppressed from a deep-run after it last got one.
    # Prevents the same dominant topic eating a deep slot every day.
    deep_seen_cooldown_days: int = 7


def _parse_daily_research(raw: dict[str, Any] | None) -> DailyResearchSettings | None:
    if not raw:
        return None

    sources_raw = raw.get("sources") or {}
    cluster_raw = raw.get("cluster") or {}
    deep_raw = raw.get("deep") or {}
    auth_raw = raw.get("auth") or {}

    google_newsletters_raw = sources_raw.get("google_newsletters") or {}
    rss_raw = sources_raw.get("rss") or {}
    manual_links_raw = sources_raw.get("manual_links") or {}
    sources = SourceSettings(
        enabled=list(sources_raw.get("enabled", [])),
        limits=SourceLimits(
            max_per_source=int(sources_raw.get("max_per_source", 50)),
            year_from=sources_raw.get("year_from"),
            timeout_sec=float(sources_raw.get("timeout_sec", 30.0)),
        ),
        rss_feeds=list(sources_raw.get("rss_feeds", [])),
        rss=RssFeedSettings(
            feed_urls=list(rss_raw.get("feed_urls", sources_raw.get("rss_feeds", []))),
            max_summary_chars=int(rss_raw.get("max_summary_chars", 700)),
        ),
        manual_links=ManualLinkSettings(
            paths=list(manual_links_raw.get("paths", [])),
            max_file_kb=int(manual_links_raw.get("max_file_kb", 512)),
            max_summary_chars=int(manual_links_raw.get("max_summary_chars", 700)),
        ),
        google_newsletters=GoogleNewsletterSettings(
            mbox_paths=list(google_newsletters_raw.get("mbox_paths", [])),
            sender_allowlist=list(google_newsletters_raw.get("sender_allowlist", [])),
            subject_allowlist=list(google_newsletters_raw.get("subject_allowlist", [])),
            max_messages=int(google_newsletters_raw.get("max_messages", 200)),
            max_mbox_bytes=int(
                google_newsletters_raw.get("max_mbox_bytes", 50 * 1024 * 1024)
            ),
        ),
    )
    cluster = ClusterSettings(
        max_clusters=int(cluster_raw.get("max_clusters", 3)),
        embedding_model=str(cluster_raw.get("embedding_model", "openclaw/clawbridge")),
        embedding_endpoint=str(cluster_raw.get("embedding_endpoint", "/v1/embeddings")),
    )
    deep_default = DeepBridgeSettings()
    deep = DeepBridgeSettings(
        enabled=bool(deep_raw.get("enabled", deep_default.enabled)),
        concurrency=int(deep_raw.get("concurrency", deep_default.concurrency)),
        timeout_sec=int(deep_raw.get("timeout_sec", deep_default.timeout_sec)),
        mode=str(deep_raw.get("mode", deep_default.mode)),
        run_topic_script=str(
            deep_raw.get("run_topic_script", deep_default.run_topic_script)
        ),
        artifacts_root=str(deep_raw.get("artifacts_root", deep_default.artifacts_root)),
    )
    auth_default = JiphyAuthSettings()
    auth = JiphyAuthSettings(
        base_url=str(auth_raw.get("base_url", auth_default.base_url)),
        username_env=str(auth_raw.get("username_env", auth_default.username_env)),
        password_env=str(auth_raw.get("password_env", auth_default.password_env)),
        timeout_sec=float(auth_raw.get("timeout_sec", auth_default.timeout_sec)),
    )
    return DailyResearchSettings(
        sources=sources,
        cluster=cluster,
        deep=deep,
        auth=auth,
        deep_seen_cooldown_days=int(raw.get("deep_seen_cooldown_days", 7)),
    )


@dataclass
class Settings:
    project_dir: Path
    jiphyeonjeon: JiphySettings
    openclaw: OpenClawSettings
    profile: ProfileSettings
    candidates: CandidateSettings
    rerank: RerankSettings
    seen: SeenSettings
    soul: SoulSettings
    decay: DecaySettings
    feedback: FeedbackSettings
    output: OutputSettings
    weekly_report: WeeklyReportSettings = field(default_factory=WeeklyReportSettings)
    daily_research: DailyResearchSettings | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return self.project_dir / "state"

    @property
    def artifacts_root(self) -> Path:
        return self.project_dir / self.output.artifacts_dir


def load_settings(config_path: Path) -> Settings:
    project_dir = config_path.resolve().parent
    data = yaml.safe_load(config_path.read_text())

    soul_raw = data.get("soul") or {}
    decay_raw = data.get("decay") or {}
    feedback_raw = data.get("feedback") or {}
    weekly_raw = data.get("weekly_report") or {}
    return Settings(
        project_dir=project_dir,
        jiphyeonjeon=JiphySettings(**data["jiphyeonjeon"]),
        openclaw=OpenClawSettings(**data["openclaw"]),
        profile=ProfileSettings(**data["profile"]),
        candidates=CandidateSettings(**data["candidates"]),
        rerank=RerankSettings(**data["rerank"]),
        seen=SeenSettings(**data["seen"]),
        soul=SoulSettings(**soul_raw),
        decay=DecaySettings(**decay_raw),
        feedback=FeedbackSettings(**feedback_raw),
        output=OutputSettings(**data["output"]),
        weekly_report=WeeklyReportSettings(**weekly_raw),
        daily_research=_parse_daily_research(data.get("daily_research")),
        raw=data,
    )
