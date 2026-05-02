from __future__ import annotations

import sys
import types

# Match the existing test pattern: stub yaml so importing config.py does not
# require pyyaml to be installed in the test environment.
sys.modules.setdefault("yaml", types.SimpleNamespace(safe_load=lambda *_a, **_k: {}))

from paper_recommender.config import (  # noqa: E402
    ClusterSettings,
    DailyResearchSettings,
    DeepBridgeSettings,
    JiphyAuthSettings,
    SourceSettings,
    _parse_daily_research,
)
from paper_recommender.sources import SourceLimits  # noqa: E402


def test_parse_returns_none_when_section_absent() -> None:
    assert _parse_daily_research(None) is None


def test_parse_returns_none_when_section_empty_dict() -> None:
    assert _parse_daily_research({}) is None


def test_parse_full_block_produces_correct_settings() -> None:
    raw = {
        "sources": {
            "enabled": ["arxiv", "hackernews"],
            "max_per_source": 30,
            "year_from": 2024,
            "timeout_sec": 20.0,
            "rss_feeds": ["https://a.test/rss", "https://b.test/atom"],
        },
        "cluster": {
            "max_clusters": 5,
            "embedding_model": "custom-embed",
            "embedding_endpoint": "/v2/embeddings",
        },
        "deep": {
            "enabled": False,
            "concurrency": 2,
            "timeout_sec": 600,
            "mode": "express",
            "run_topic_script": "/tmp/run-topic.sh",
            "artifacts_root": "/tmp/artifacts",
        },
        "auth": {
            "base_url": "https://test.kr",
            "username_env": "U",
            "password_env": "P",
            "timeout_sec": 10.0,
        },
    }
    s = _parse_daily_research(raw)
    assert isinstance(s, DailyResearchSettings)

    # sources
    assert isinstance(s.sources, SourceSettings)
    assert s.sources.enabled == ["arxiv", "hackernews"]
    assert s.sources.limits == SourceLimits(
        max_per_source=30, year_from=2024, timeout_sec=20.0
    )
    assert s.sources.rss_feeds == ["https://a.test/rss", "https://b.test/atom"]

    # cluster
    assert isinstance(s.cluster, ClusterSettings)
    assert s.cluster.max_clusters == 5
    assert s.cluster.embedding_model == "custom-embed"
    assert s.cluster.embedding_endpoint == "/v2/embeddings"

    # deep
    assert isinstance(s.deep, DeepBridgeSettings)
    assert s.deep.enabled is False
    assert s.deep.concurrency == 2
    assert s.deep.timeout_sec == 600
    assert s.deep.mode == "express"
    assert s.deep.run_topic_script == "/tmp/run-topic.sh"
    assert s.deep.artifacts_root == "/tmp/artifacts"

    # auth
    assert isinstance(s.auth, JiphyAuthSettings)
    assert s.auth.base_url == "https://test.kr"
    assert s.auth.username_env == "U"
    assert s.auth.password_env == "P"
    assert s.auth.timeout_sec == 10.0


def test_parse_uses_documented_defaults_when_subsections_missing() -> None:
    s = _parse_daily_research({"sources": {"enabled": ["arxiv"]}})
    assert s is not None

    # cluster defaults
    assert s.cluster.max_clusters == 3
    assert s.cluster.embedding_model == "openclaw/clawbridge"
    assert s.cluster.embedding_endpoint == "/v1/embeddings"

    # deep defaults
    assert s.deep.enabled is True
    assert s.deep.concurrency == 1
    # Empirical: a deep run is 25-35 min. Default must accommodate the upper end.
    assert s.deep.timeout_sec == 2400
    assert s.deep.mode == "full-auto"

    # auth defaults
    assert s.auth.base_url == "https://jiphyeonjeon.kr"
    assert s.auth.username_env == "JIPHYEONJEON_USERNAME"
    assert s.auth.password_env == "JIPHYEONJEON_PASSWORD"
    assert s.auth.timeout_sec == 30.0

    # sources defaults
    assert s.sources.enabled == ["arxiv"]
    assert s.sources.limits == SourceLimits()
    assert s.sources.rss_feeds == []


def test_parse_with_only_unrecognized_keys_returns_full_defaults() -> None:
    s = _parse_daily_research({"foo": "bar"})
    assert s is not None
    assert s.sources.enabled == []
    assert s.cluster.max_clusters == 3
    assert s.deep.timeout_sec == 2400
    assert s.deep_seen_cooldown_days == 7


def test_parse_deep_seen_cooldown_override() -> None:
    s = _parse_daily_research({"sources": {"enabled": ["arxiv"]}, "deep_seen_cooldown_days": 14})
    assert s is not None
    assert s.deep_seen_cooldown_days == 14


def test_jiphy_auth_settings_username_property_reads_env(monkeypatch) -> None:
    auth = JiphyAuthSettings(base_url="https://x.kr", username_env="JH_TEST_USER", password_env="JH_TEST_PW")
    monkeypatch.setenv("JH_TEST_USER", "alice")
    monkeypatch.setenv("JH_TEST_PW", "hunter2")
    assert auth.username == "alice"
    assert auth.password == "hunter2"


def test_jiphy_auth_settings_missing_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("JH_NOT_SET_USER", raising=False)
    auth = JiphyAuthSettings(base_url="https://x.kr", username_env="JH_NOT_SET_USER", password_env="JH_NOT_SET_PW")
    import pytest
    with pytest.raises(RuntimeError):
        _ = auth.username
