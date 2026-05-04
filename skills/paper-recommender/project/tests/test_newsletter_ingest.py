from __future__ import annotations

import importlib.util
import json
import sys
from email.message import EmailMessage
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "newsletter_ingest.py"
spec = importlib.util.spec_from_file_location("newsletter_ingest", SCRIPT)
assert spec and spec.loader
newsletter_ingest = importlib.util.module_from_spec(spec)
sys.modules["newsletter_ingest"] = newsletter_ingest
spec.loader.exec_module(newsletter_ingest)


def test_jsonl_ingest_filters_sender_and_omits_email_body(tmp_path: Path) -> None:
    source = tmp_path / "newsletters.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "subject": "LLM papers this week",
                        "from": "Research Newsletter <news@example.com>",
                        "date": "Mon, 04 May 2026 07:00:00 +0900",
                        "body": "Secret subscriber note. Read https://arxiv.org/abs/2605.00001 and https://example.com/private",
                    }
                ),
                json.dumps(
                    {
                        "subject": "Promo",
                        "from": "sales@example.com",
                        "body": "https://arxiv.org/abs/2605.99999",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    messages = newsletter_ingest.load_messages(source, max_messages=50)
    items = newsletter_ingest.select_items(messages, sender_allowlist=["news@example.com"])
    raw_path, page_path = newsletter_ingest.publish_items(
        wiki_root=tmp_path / "wiki",
        run_date="2026-05-04",
        source_path=source,
        items=items,
    )

    assert [item["url"] for item in items] == ["https://arxiv.org/abs/2605.00001"]
    raw = raw_path.read_text(encoding="utf-8")
    page = page_path.read_text(encoding="utf-8")
    assert "Secret subscriber note" not in raw
    assert "Secret subscriber note" not in page
    assert "metadata-and-extracted-urls-only" in raw
    assert "newsletter-ingest-2026-05-04.md" in str(page_path)


def test_mbox_ingest_extracts_research_links(tmp_path: Path) -> None:
    mbox_path = tmp_path / "takeout.mbox"
    msg = EmailMessage()
    msg["From"] = "AI Digest <digest@example.com>"
    msg["Subject"] = "OpenReview and code"
    msg["Date"] = "Mon, 04 May 2026 07:00:00 +0900"
    msg.set_content(
        "Items: https://openreview.net/forum?id=abc123 and https://github.com/example/repo."
    )
    mbox_path.write_text("From nobody Mon May 04 07:00:00 2026\n" + msg.as_string() + "\n", encoding="utf-8")

    items = newsletter_ingest.select_items(
        newsletter_ingest.load_messages(mbox_path),
        sender_allowlist=["digest@example.com"],
    )

    assert [item["kind"] for item in items] == ["paper", "code"]
    assert items[0]["url"] == "https://openreview.net/forum?id=abc123"
    assert items[1]["url"] == "https://github.com/example/repo"


def test_cli_requires_explicit_sender_boundary(tmp_path: Path, capsys) -> None:
    source = tmp_path / "newsletters.jsonl"
    source.write_text(
        json.dumps({"subject": "private", "from": "person@example.com", "body": "https://arxiv.org/abs/1"})
        + "\n",
        encoding="utf-8",
    )

    rc = newsletter_ingest.main(
        [
            "--source",
            str(source),
            "--wiki-root",
            str(tmp_path / "wiki"),
        ]
    )

    assert rc == 2
    assert "requires --sender-allowlist" in capsys.readouterr().err
    assert not (tmp_path / "wiki").exists()


def test_source_size_cap_blocks_large_exports(tmp_path: Path) -> None:
    source = tmp_path / "newsletters.jsonl"
    source.write_text("{}\n", encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="above --max-source-bytes"):
        newsletter_ingest.enforce_source_size(source, max_source_bytes=1)


def test_include_all_urls_still_filters_private_utility_links(tmp_path: Path) -> None:
    messages = [
        newsletter_ingest.NewsletterMessage(
            subject="Digest links",
            sender="digest@example.com",
            received_at="Mon, 04 May 2026 07:00:00 +0900",
            body=(
                "Read https://example.com/technical-report "
                "and manage https://example.com/unsubscribe?token=secret"
            ),
        )
    ]

    items = newsletter_ingest.select_items(
        messages,
        sender_allowlist=["digest@example.com"],
        include_all_urls=True,
    )

    assert [item["url"] for item in items] == ["https://example.com/technical-report"]


def test_cli_writes_idempotent_raw_and_page(tmp_path: Path, capsys) -> None:
    source = tmp_path / "newsletters.jsonl"
    source.write_text(
        json.dumps(
            {
                "subject": "DeepMind post",
                "from": "newsletter@deepmind.google",
                "body": "https://deepmind.google/discover/blog/test",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rc = newsletter_ingest.main(
        [
            "--source",
            str(source),
            "--wiki-root",
            str(tmp_path / "wiki"),
            "--date",
            "2026-05-04",
            "--sender-allowlist",
            "deepmind.google",
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "items: 1" in out
    raw_path = tmp_path / "wiki" / "raw" / "newsletters" / "2026-05-04" / "items.json"
    page_path = tmp_path / "wiki" / "pages" / "newsletter-ingest-2026-05-04.md"
    assert raw_path.exists()
    assert page_path.exists()
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert payload["source_file"] == "newsletters.jsonl"
    assert payload["items"][0]["kind"] == "research-post"


def test_topic_briefing_groups_items_without_email_body(tmp_path: Path, capsys) -> None:
    source = tmp_path / "newsletters.jsonl"
    briefing_path = tmp_path / "reports" / "newsletter-briefing.md"
    source.write_text(
        json.dumps(
            {
                "subject": "RAG and agents report",
                "from": "AI Digest <digest@example.com>",
                "date": "Mon, 04 May 2026 07:00:00 +0900",
                "body": "Private body. Read https://arxiv.org/abs/2605.00001 about retrieval agents.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rc = newsletter_ingest.main(
        [
            "--source",
            str(source),
            "--wiki-root",
            str(tmp_path / "wiki"),
            "--date",
            "2026-05-04",
            "--sender-allowlist",
            "digest@example.com",
            "--briefing-path",
            str(briefing_path),
        ]
    )

    assert rc == 0
    assert "wrote" in capsys.readouterr().out
    briefing = briefing_path.read_text(encoding="utf-8")
    assert "집현전-Claw 뉴스레터 수집 브리핑" in briefing
    assert "검색/RAG/지식그래프" in briefing
    assert "- 핵심 요약:" in briefing
    assert "- 기술 포인트:" in briefing
    assert "- 출처 링크:" in briefing
    assert "https://arxiv.org/abs/2605.00001" in briefing
    assert "Private body" not in briefing


def test_topic_classifier_uses_token_boundaries_not_substrings() -> None:
    item = {
        "title": "Research roundup on diffusion models",
        "kind": "post",
        "url": "https://example.com/research-roundup",
    }

    assert newsletter_ingest.classify_topic(item) == "기타 테크 리포트"


def test_topic_classifier_prioritizes_safety_eval_over_benchmark_noise() -> None:
    item = {
        "title": "OpenAI eval benchmark for agent safety",
        "kind": "research-post",
        "url": "https://openai.com/research/evals",
    }

    result = newsletter_ingest.classify_topic_result(item)

    assert result.label == "AI 안전/평가"
    assert "eval" in result.evidence
    assert "safety" in result.evidence


def test_topic_classifier_handles_agent_rag_as_primary_retrieval_topic() -> None:
    item = {
        "title": "GraphRAG retrieval agents for knowledge graph search",
        "kind": "post",
        "url": "https://example.com/graphrag",
    }

    result = newsletter_ingest.classify_topic_result(item)

    assert result.label == "검색/RAG/지식그래프"
    assert result.score > 2


def test_topic_classifier_uses_url_and_multimodal_signals() -> None:
    github_item = {
        "title": "New developer framework",
        "kind": "code",
        "url": "https://github.com/example/framework",
    }
    video_item = {
        "title": "Hugging Face VLM video model release",
        "kind": "post",
        "url": "https://huggingface.co/blog/video-vlm",
    }

    assert newsletter_ingest.classify_topic(github_item) == "오픈소스/코드"
    assert newsletter_ingest.classify_topic(video_item) == "멀티모달/비전"
