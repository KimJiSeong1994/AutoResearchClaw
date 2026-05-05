from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from discord_openclaw_bridge.miner import DiscordLinkMetadata, record_miner_link, sanitize_url
from discord_openclaw_bridge.review import export_approved_manual_links, record_decision
from discord_openclaw_bridge.review_cli import main


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _queue(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    queue_path = tmp_path / "review" / "link-review-queue.jsonl"
    intake_path = tmp_path / "intake" / "links.jsonl"
    decisions_path = tmp_path / "review" / "link-review-decisions.jsonl"
    result = record_miner_link(
        url="https://example.com/research?utm_source=x&ok=1",
        title="Approved Research",
        note="operator supplied summary",
        intake_path=intake_path,
        review_queue_path=queue_path,
        discord=DiscordLinkMetadata(guild_id=1, channel_id=2, message_id=3, user_id=4),
        created_at=datetime(2026, 5, 5, 1, 2, 3, tzinfo=timezone.utc),
    )
    return intake_path, queue_path, decisions_path, result.intake_id


def test_sanitize_url_rejects_userinfo_and_private_hosts() -> None:
    assert sanitize_url("https://user:pass@example.com/paper") == ""
    assert sanitize_url("http://127.0.0.1:8000/private") == ""
    assert sanitize_url("https://localhost/private") == ""
    assert sanitize_url("https://10.0.0.5/private") == ""
    assert sanitize_url("https://172.16.0.1/private") == ""
    assert sanitize_url("https://192.168.1.5/private") == ""
    assert sanitize_url("https://service.internal/private") == ""


def test_record_miner_link_repairs_missing_review_queue_row(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    first = record_miner_link(url="https://example.com/a", intake_path=intake_path, review_queue_path=review_path)
    review_path.unlink()
    repaired = record_miner_link(url="https://example.com/a", intake_path=intake_path, review_queue_path=review_path)

    assert first.accepted
    assert repaired.accepted
    assert len(_read_jsonl(intake_path)) == 1
    assert len(_read_jsonl(review_path)) == 1
    assert _read_jsonl(review_path)[0]["intake_id"] == first.intake_id


def test_record_decision_appends_audit_row(tmp_path: Path) -> None:
    _, queue_path, decisions_path, intake_id = _queue(tmp_path)

    row = record_decision(
        queue_path=queue_path,
        decisions_path=decisions_path,
        intake_id=intake_id,
        decision="approve",
        reviewer="operator-a",
        reason="good evidence",
        decided_at=datetime(2026, 5, 5, 4, 5, 6, tzinfo=timezone.utc),
    )

    assert row["decision"] == "approve"
    assert row["reviewer"] == "operator-a"
    assert row["decided_at"] == "2026-05-05T04:05:06Z"
    assert row["audit_source"] == "jiphyeonjeon_miner_review_cli"
    assert _read_jsonl(decisions_path) == [row]


def test_record_decision_rejects_unknown_id_and_invalid_decision(tmp_path: Path) -> None:
    _, queue_path, decisions_path, intake_id = _queue(tmp_path)

    with pytest.raises(ValueError, match="invalid decision"):
        record_decision(queue_path=queue_path, decisions_path=decisions_path, intake_id=intake_id, decision="maybe")
    with pytest.raises(KeyError, match="unknown intake_id"):
        record_decision(queue_path=queue_path, decisions_path=decisions_path, intake_id="miner_missing", decision="approve")


def test_approved_manual_link_export_excludes_pending_rejected_and_held(tmp_path: Path) -> None:
    intake_path, queue_path, decisions_path, approved_id = _queue(tmp_path)
    rejected = record_miner_link(url="https://example.com/rejected", intake_path=intake_path, review_queue_path=queue_path)
    held = record_miner_link(url="https://example.com/held", intake_path=intake_path, review_queue_path=queue_path)
    pending = record_miner_link(url="https://example.com/pending", intake_path=intake_path, review_queue_path=queue_path)
    record_decision(queue_path=queue_path, decisions_path=decisions_path, intake_id=approved_id, decision="approve")
    record_decision(queue_path=queue_path, decisions_path=decisions_path, intake_id=rejected.intake_id, decision="reject")
    record_decision(queue_path=queue_path, decisions_path=decisions_path, intake_id=held.intake_id, decision="hold")

    rows = export_approved_manual_links(
        queue_path=queue_path,
        decisions_path=decisions_path,
        output_path=tmp_path / "approved-manual-links.jsonl",
    )

    assert [row["url"] for row in rows] == ["https://example.com/research?ok=1"]
    assert rows[0]["title"] == "Approved Research"
    assert rows[0]["source"] == "discord_miner"
    assert rows[0]["review"]["decision"] == "approved"
    assert pending.intake_id not in json.dumps(rows)


def test_export_writes_approved_only_manual_links_jsonl(tmp_path: Path) -> None:
    _, queue_path, decisions_path, intake_id = _queue(tmp_path)
    export_path = tmp_path / "approved-manual-links.jsonl"
    record_decision(queue_path=queue_path, decisions_path=decisions_path, intake_id=intake_id, decision="approve")

    rows = export_approved_manual_links(queue_path=queue_path, decisions_path=decisions_path, output_path=export_path)

    assert len(rows) == 1
    [row] = _read_jsonl(export_path)
    assert set(row) >= {"title", "url", "summary", "published_at", "source", "tags", "review"}
    assert row["review"]["decision"] == "approved"
    assert "approved-by-jiphyeonjeon-claw" in row["tags"]
    assert "pending_claw_review" not in row["tags"]


def test_review_cli_list_show_decide_and_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, queue_path, decisions_path, intake_id = _queue(tmp_path)
    export_path = tmp_path / "approved.jsonl"

    assert main(["--queue", str(queue_path), "--decisions", str(decisions_path), "list"]) == 0
    assert intake_id in capsys.readouterr().out

    assert main(["--queue", str(queue_path), "--decisions", str(decisions_path), "approve", intake_id, "--reason", "ok"]) == 0
    assert '"decision": "approve"' in capsys.readouterr().out

    assert main(["--queue", str(queue_path), "--decisions", str(decisions_path), "show", intake_id]) == 0
    assert '"latest_decision"' in capsys.readouterr().out

    assert main(["--queue", str(queue_path), "--decisions", str(decisions_path), "export", "--output", str(export_path)]) == 0
    assert "exported 1 approved" in capsys.readouterr().out
    assert _read_jsonl(export_path)[0]["url"] == "https://example.com/research?ok=1"


def test_review_cli_missing_id_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, queue_path, decisions_path, _ = _queue(tmp_path)

    assert main(["--queue", str(queue_path), "--decisions", str(decisions_path), "show", "miner_missing"]) == 1
    assert "unknown intake_id" in capsys.readouterr().err


def test_record_decision_rejects_unsafe_queue_url(tmp_path: Path) -> None:
    queue_path = tmp_path / "queue.jsonl"
    decisions_path = tmp_path / "decisions.jsonl"
    queue_path.write_text(
        json.dumps(
            {
                "intake_id": "miner_bad",
                "title": "Bad",
                "url": "http://127.0.0.1:8080/private",
                "status": "pending_claw_review",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsafe url"):
        record_decision(queue_path=queue_path, decisions_path=decisions_path, intake_id="miner_bad", decision="approve")
