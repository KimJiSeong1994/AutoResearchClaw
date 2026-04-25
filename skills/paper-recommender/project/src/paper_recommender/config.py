from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
        raw=data,
    )
