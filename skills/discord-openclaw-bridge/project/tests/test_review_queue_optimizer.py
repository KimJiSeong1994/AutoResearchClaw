from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from discord_openclaw_bridge.review_queue_optimizer import build_optimizer_report, main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_review_queue_optimizer_reports_duplicates_stale_and_priority_without_mutation(tmp_path: Path) -> None:
    now = datetime(2026, 5, 16, tzinfo=timezone.utc)
    old = (now - timedelta(days=8)).isoformat().replace("+00:00", "Z")
    queue_rows = [
        {"intake_id": "miner_a", "url": "https://example.com/research?utm_source=x", "title": "AI Research", "created_at": old},
        {"intake_id": "miner_b", "url": "https://example.com/research", "title": "AI Research duplicate", "created_at": old},
        {"intake_id": "miner_c", "url": "https://example.com/approved", "title": "Approved", "created_at": old},
    ]
    decisions_rows = [{"intake_id": "miner_c", "decision": "approve"}]

    report = build_optimizer_report(
        queue_rows=queue_rows,
        decisions_rows=decisions_rows,
        queue_path="/tmp/queue.jsonl",
        decisions_path="/tmp/decisions.jsonl",
        now=now,
        max_age_days=7,
    )

    assert report["no_mutation"] is True
    assert report["queue_snapshot"]["pending_rows"] == 2
    assert report["duplicate_candidates"][0]["intake_ids"] == ["miner_a", "miner_b"]
    assert {item["intake_id"] for item in report["stale_items"]} == {"miner_a", "miner_b"}
    assert report["priority_recommendations"][0]["score"] >= 3


def test_review_queue_optimizer_cli_does_not_modify_files(tmp_path: Path, capsys) -> None:
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    rows = [{"intake_id": "miner_a", "url": "https://example.com/research", "created_at": "2026-05-01T00:00:00Z"}]
    _write_jsonl(queue, rows)
    _write_jsonl(decisions, [])
    before = queue.read_text(encoding="utf-8"), decisions.read_text(encoding="utf-8")

    assert main(["--queue", str(queue), "--decisions", str(decisions)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["no_mutation"] is True
    assert (queue.read_text(encoding="utf-8"), decisions.read_text(encoding="utf-8")) == before
    assert not (tmp_path / ".jiphyeonjeon-miner-jsonl.lock").exists()
