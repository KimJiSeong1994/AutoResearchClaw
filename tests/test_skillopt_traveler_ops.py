from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "skillopt_traveler_ops.py"


def load_ops() -> Any:
    spec = importlib.util.spec_from_file_location("skillopt_traveler_ops", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def base_snapshots() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    workspace = "/home/ubuntu/.hermes/workspace"
    scout = {
        "run_at": "2026-08-05T00:00:00Z",
        "requests_created": 1,
        "research_queue_path": f"{workspace}/review/jiphyeonjeon-traveler/research-requests.jsonl",
        "scout_queue_path": f"{workspace}/review/jiphyeonjeon-traveler/source-candidates.jsonl",
        "stale_pending_topics": [],
    }
    discovery = {
        "run_at": "2026-08-05T00:02:00Z",
        "requests_processed": 1,
        "providers_used": ["arxiv", "semantic_scholar", "static"],
        "reviewed_count": 12,
        "accepted_count": 1,
        "duplicate_count": 0,
        "rejected_count": 3,
        "error_count": 0,
        "candidate_queue_path": f"{workspace}/review/jiphyeonjeon-traveler/source-candidates.jsonl",
        "evidence_path": f"{workspace}/review/jiphyeonjeon-traveler/evidence.jsonl",
        "provider_results": [
            {"provider": "arxiv", "reviewed_count": 8, "candidate_count": 1, "rejected_count": 2},
            {"provider": "semantic_scholar", "reviewed_count": 4, "candidate_count": 0, "rejected_count": 1},
        ],
    }
    report = {
        "run_at": "2026-08-05T00:04:00Z",
        "item_count": 1,
        "miner_request_state": "sent",
    }
    return scout, discovery, report


def test_accepts_successful_hermes_run_without_automatic_apply() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    actual = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert actual["schema_version"] == "skillopt-traveler-ops.v1"
    assert actual["status"] == "ok"
    assert actual["automatic_apply"] is False
    assert actual["metrics"]["accepted_count"] == 1
    assert actual["metrics"]["report_candidate_count"] == 1
    assert actual["freshness"]["artifacts"]["report"]["status"] == "fresh"
    assert actual["path_policy"]["legacy_openclaw_workspace_observed"] is False
    serialized = json.dumps(actual, ensure_ascii=False)
    assert "/home/ubuntu/.hermes/workspace" not in serialized
    assert "/Users/" not in serialized


def test_zero_accepted_with_provider_error_fails() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    discovery["accepted_count"] = 0
    discovery["error_count"] = 1
    discovery["provider_results"][1]["error_kind"] = "http_429"
    discovery["provider_results"][1]["error"] = "Semantic Scholar returned 429"
    report["item_count"] = 0
    actual = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert actual["status"] == "failed"
    assert "zero_accepted_with_provider_errors" in actual["reasons"]
    assert actual["metrics"]["provider_error_count"] == 1


def test_duplicate_only_and_stale_pending_are_degraded() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    discovery.update({"accepted_count": 0, "duplicate_count": 3, "rejected_count": 0, "reviewed_count": 3})
    report["item_count"] = 0
    duplicate_only = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert duplicate_only["status"] == "degraded"
    assert "duplicates_only" in duplicate_only["reasons"]

    scout["stale_pending_topics"] = ["stale-topic"]
    discovery.update({"duplicate_count": 0, "rejected_count": 0, "reviewed_count": 0, "requests_processed": 0})
    stale = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert stale["status"] == "degraded"
    assert "stale_pending_blocked" in stale["reasons"]


def test_legacy_openclaw_production_paths_fail_even_with_acceptance() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    scout["research_queue_path"] = "/home/ubuntu/.openclaw/workspace/review/jiphyeonjeon-traveler/research-requests.jsonl"
    actual = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert actual["status"] == "failed"
    assert "legacy_openclaw_production_path" in actual["reasons"]
    assert "[legacy-openclaw-workspace]" in json.dumps(actual, ensure_ascii=False)


def test_cli_missing_or_malformed_snapshot_exits_failed(tmp_path: Path) -> None:
    scout, _discovery, report = base_snapshots()
    scout_path = tmp_path / "scout.json"
    discovery_path = tmp_path / "discovery.json"
    report_path = tmp_path / "report.json"
    out_path = tmp_path / "out.json"
    scout_path.write_text(json.dumps(scout), encoding="utf-8")
    discovery_path.write_text("{malformed", encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--scout",
            str(scout_path),
            "--discovery",
            str(discovery_path),
            "--report",
            str(report_path),
            "--workspace",
            "/home/ubuntu/.hermes/workspace",
            "--out",
            str(out_path),
            "--as-of",
            "2026-08-05T01:00:00Z",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["reasons"] == ["missing_or_malformed_snapshot"]


def test_report_candidate_count_falls_back_to_real_item_count_schema() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    report.pop("candidate_count", None)
    report["item_count"] = 7
    actual = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert actual["metrics"]["report_candidate_count"] == 7


def test_stale_missing_invalid_and_excess_future_run_at_fail() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    stale = mod.classify_operation(
        scout={**scout, "run_at": "2026-08-03T10:00:00Z"},
        discovery=discovery,
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert stale["status"] == "failed"
    assert "stale_scout_snapshot" in stale["reasons"]
    assert stale["freshness"]["artifacts"]["scout"]["status"] == "stale"

    missing = mod.classify_operation(
        scout=scout,
        discovery={k: v for k, v in discovery.items() if k != "run_at"},
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert missing["status"] == "failed"
    assert "missing_discovery_run_at" in missing["reasons"]

    invalid = mod.classify_operation(
        scout=scout,
        discovery={**discovery, "run_at": "not-a-time"},
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert invalid["status"] == "failed"
    assert "invalid_discovery_run_at" in invalid["reasons"]

    future = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report={**report, "run_at": "2026-08-05T01:06:00Z"},
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert future["status"] == "failed"
    assert "future_report_run_at" in future["reasons"]


def test_small_future_clock_skew_is_tolerated() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    actual = mod.classify_operation(
        scout=scout,
        discovery={**discovery, "run_at": "2026-08-05T01:04:59Z"},
        report=report,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )
    assert actual["status"] == "ok"
    assert actual["freshness"]["artifacts"]["discovery"]["status"] == "fresh"


def test_operational_report_exposes_miner_approval_and_diversity_reward() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    reward = {
        "schema_version": "traveler-skillopt-reward.v1",
        "approval_window_days": 7,
        "eligible_sample_count": 10,
        "censored_recent_unapproved_count": 2,
        "approval": {"approved_count": 6, "denominator_count": 10, "approval_rate_pct": 60.0},
        "diversity": {
            "normalized_shannon": 0.75,
            "components": {"provider": 0.8, "domain": 0.7, "topic": 0.75},
        },
        "reward_score": 64.5,
    }
    actual = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report=report,
        reward=reward,
        autotune={"status": "applied"},
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )

    assert actual["metrics"]["miner_approved_count"] == 6
    assert actual["metrics"]["miner_approval_rate_pct"] == 60.0
    assert actual["metrics"]["approved_diversity_score"] == 0.75
    assert actual["optimization"]["primary_weight"] == 0.7
    assert actual["optimization"]["secondary_weight"] == 0.3
    assert actual["bounded_autotune"]["eligible"] is True
    assert actual["bounded_autotune"]["latest_status"] == "applied"
    assert actual["automatic_apply"] is False, "the evaluator itself remains read-only"


def test_failed_current_ops_blocks_bounded_autotune_eligibility() -> None:
    mod = load_ops()
    scout, discovery, report = base_snapshots()
    discovery["accepted_count"] = 0
    discovery["error_count"] = 1
    discovery["provider_results"][0]["error"] = "rate limited"
    reward = {
        "eligible_sample_count": 100,
        "approval": {"approved_count": 90, "denominator_count": 100, "approval_rate_pct": 90.0},
        "diversity": {"normalized_shannon": 0.9, "components": {}},
    }
    actual = mod.classify_operation(
        scout=scout,
        discovery=discovery,
        report=report,
        reward=reward,
        workspace="/home/ubuntu/.hermes/workspace",
        as_of="2026-08-05T01:00:00Z",
    )

    assert actual["status"] == "failed"
    assert actual["bounded_autotune"]["eligible"] is False
