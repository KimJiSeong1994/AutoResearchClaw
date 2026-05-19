from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "miner_approved_archive_ingest.py"
spec = importlib.util.spec_from_file_location("miner_approved_archive_ingest", SCRIPT)
assert spec and spec.loader
miner_approved_archive_ingest = importlib.util.module_from_spec(spec)
sys.modules["miner_approved_archive_ingest"] = miner_approved_archive_ingest
spec.loader.exec_module(miner_approved_archive_ingest)


def test_miner_approved_archive_ingest_writes_only_approved_technical_video_rows(tmp_path: Path) -> None:
    manual_path = tmp_path / "approved.jsonl"
    manual_path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in [
                {
                    "title": "Technical YouTube Agent Benchmark",
                    "url": "https://www.youtube.com/watch?v=abc123XYZ09",
                    "summary": "LLM agent benchmark and retrieval evaluation.",
                    "source": "discord_miner",
                    "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
                    "review": {"decision": "approved", "source_decision": "approve"},
                    "media": {
                        "type": "video",
                        "platform": "youtube",
                        "video_id": "abc123XYZ09",
                        "canonical_url": "https://www.youtube.com/watch?v=abc123XYZ09",
                        "provider": "youtube_data_api",
                        "metadata_provenance": "youtube_data_api_v3_videos_list",
                        "analysis_provenance": "metadata_only",
                        "analysis_status": "metadata_ready",
                        "quota_units": 1,
                        "raw_provider_payload": {"secret": "x"},
                    },
                    "transcript": "must not persist",
                },
                {
                    "title": "Pending YouTube",
                    "url": "https://www.youtube.com/watch?v=pending123",
                    "source": "discord_miner",
                    "tags": ["pending_claw_review"],
                    "review": {"decision": "pending"},
                },
                {
                    "title": "Music vlog",
                    "url": "https://www.youtube.com/watch?v=junk1234567",
                    "summary": "travel music daily vlog",
                    "source": "discord_miner",
                    "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
                    "review": {"decision": "approved", "source_decision": "approve"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    briefing_path = tmp_path / "reports" / "briefing.md"

    rc = miner_approved_archive_ingest.main(
        [
            "--manual-links-path",
            str(manual_path),
            "--wiki-root",
            str(tmp_path / "wiki"),
            "--date",
            "2026-05-19",
            "--briefing-path",
            str(briefing_path),
        ]
    )

    assert rc == 0
    raw_path = tmp_path / "wiki" / "raw" / "newsletters" / "2026-05-19" / "items.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert [item["title"] for item in payload["items"]] == ["Technical YouTube Agent Benchmark"]
    stored = payload["items"][0]
    assert stored["media"]["video_id"] == "abc123XYZ09"
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "raw_provider_payload" not in dumped
    assert "must not persist" not in dumped
    briefing = briefing_path.read_text(encoding="utf-8")
    assert "집현전-광부 승인 큐" in briefing
    assert "YouTube Data API 메타데이터 기반" in briefing


def test_ingest_allows_no_transcript_label_without_raw_transcript_key(tmp_path: Path) -> None:
    item = miner_approved_archive_ingest._row_to_item(
        {
            "title": "Technical YouTube Agent Benchmark",
            "url": "https://www.youtube.com/watch?v=abc123XYZ09",
            "summary": "LLM agent benchmark and retrieval evaluation.",
            "source": "discord_miner",
            "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
            "review": {"decision": "approved", "source_decision": "approve"},
            "media": {
                "type": "video",
                "platform": "youtube",
                "video_id": "abc123XYZ09",
                "canonical_url": "https://www.youtube.com/watch?v=abc123XYZ09",
                "provider": "gemini",
                "analysis_provenance": "gemini_youtube_uri_no_transcript",
                "analysis_status": "metadata_ready",
            },
        },
        source_label="집현전-광부 승인 큐",
    )

    assert item is not None
    assert item["media"]["analysis_provenance"] == "model_public_youtube_av_no_raw"


def test_dsba_sample_video_flows_from_approved_miner_row_to_archive_and_briefing(tmp_path: Path) -> None:
    manual_path = tmp_path / "dsba-approved.jsonl"
    manual_path.write_text(
        json.dumps(
            {
                "title": "DSBA data science LLM agent benchmark lecture",
                "url": "https://www.youtube.com/watch?v=rN5UpLmt2HM",
                "summary": "machine learning research benchmark retrieval agent",
                "source": "discord_miner",
                "tags": ["manual-link", "approved-by-jiphyeonjeon-claw", "youtube-video"],
                "review": {"decision": "approved", "source_decision": "approve"},
                "media": {
                    "type": "video",
                    "platform": "youtube",
                    "video_id": "rN5UpLmt2HM",
                    "canonical_url": "https://www.youtube.com/watch?v=rN5UpLmt2HM",
                    "provider": "none",
                    "analysis_status": "metadata_unavailable",
                    "analysis_provenance": "none",
                    "metadata_provenance": "none",
                    "fetched_at": "2026-05-19T12:21:06Z",
                    "expires_at": "2026-06-18T12:21:06Z",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    briefing_path = tmp_path / "briefing.md"

    rc = miner_approved_archive_ingest.main(
        [
            "--manual-links-path",
            str(manual_path),
            "--wiki-root",
            str(tmp_path / "wiki"),
            "--date",
            "2026-05-19",
            "--briefing-path",
            str(briefing_path),
        ]
    )

    assert rc == 0
    payload = json.loads((tmp_path / "wiki" / "raw" / "newsletters" / "2026-05-19" / "items.json").read_text())
    assert payload["items"][0]["media"]["video_id"] == "rN5UpLmt2HM"
    assert payload["items"][0]["primary_topic"] == "agents_automation"
    briefing = briefing_path.read_text(encoding="utf-8")
    assert "DSBA data science LLM agent benchmark lecture" in briefing
    assert "rN5UpLmt2HM" in briefing


def test_ingest_propagates_sanitized_content_analysis_and_normalizes_legacy_tier(tmp_path: Path) -> None:
    item = miner_approved_archive_ingest._row_to_item(
        {
            "title": "Technical YouTube Agent Benchmark",
            "url": "https://www.youtube.com/watch?v=abc123XYZ09",
            "summary": "LLM agent benchmark and retrieval evaluation.",
            "source": "discord_miner",
            "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
            "review": {"decision": "approved", "source_decision": "approve"},
            "media": {"type": "video", "platform": "youtube", "video_id": "abc123XYZ09"},
            "content_analysis": {
                "version": "youtube_content_analysis_v1",
                "analysis_status": "ready",
                "evidence_tier": "gemini_youtube_uri_no_transcript",
                "analysis_provenance": "gemini_youtube_uri_no_transcript",
                "provider": "gemini",
                "summary_lines": ["provider-derived audiovisual inference", "자막/transcript 근거 아님"],
                "limitations": ["raw transcript/audio/video는 저장하지 않음"],
                "claims": [
                    {"text": "Agent benchmark overview", "basis": "provider_model", "confidence": 0.4},
                    {"text": "leak", "raw_transcript": "forbidden"},
                ],
                "raw_provider_payload": {"secret": "x"},
                "fallback_reason": "safe label only",
            },
        },
        source_label="집현전-광부 승인 큐",
    )

    assert item is not None
    analysis = item["content_analysis"]
    assert analysis["evidence_tier"] == "model_public_youtube_av_no_raw"
    assert analysis["analysis_provenance"] == "model_public_youtube_av_no_raw"
    dumped = json.dumps(item, ensure_ascii=False)
    assert "raw_provider_payload" not in dumped
    assert "raw_transcript" not in dumped
    assert "provider-derived audiovisual inference" in dumped
    assert "자막/transcript 근거 아님" in dumped
