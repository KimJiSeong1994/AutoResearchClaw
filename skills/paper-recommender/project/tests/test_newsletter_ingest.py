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

BRIDGE = Path(__file__).resolve().parents[2] / "apps_script_relay_ingest.py"
bridge_spec = importlib.util.spec_from_file_location("apps_script_relay_ingest", BRIDGE)
assert bridge_spec and bridge_spec.loader
apps_script_relay_ingest = importlib.util.module_from_spec(bridge_spec)
sys.modules["apps_script_relay_ingest"] = apps_script_relay_ingest
bridge_spec.loader.exec_module(apps_script_relay_ingest)


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
    assert "집현전-Claw 기술 브리핑 카드뉴스" in briefing
    assert "검색/RAG/지식그래프" in briefing
    assert "## 오늘의 카드뉴스 흐름" in briefing
    assert "- 훅:" in briefing
    assert "- 맥락:" in briefing
    assert "- 핵심 변화:" in briefing
    assert "- 왜 중요한가:" in briefing
    assert "- 근거/출처:" in briefing
    assert "- CTA/저장 포인트:" in briefing
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


def test_topic_classification_detail_exposes_primary_secondary_confidence_and_reasons() -> None:
    item = {
        "title": "GraphRAG retrieval agents for knowledge graph search",
        "kind": "post",
        "url": "https://github.com/example/graphrag",
    }

    result = newsletter_ingest.classify_topic_detail(item)

    assert result.primary == "data_retrieval_knowledge"
    assert result.primary_display == "검색/RAG/지식그래프"
    assert "rag" in result.secondary
    assert "semantic_search" in result.secondary
    assert result.confidence > 0
    assert "retrieval" in result.reasons
    assert result.label == result.primary_display
    assert result.evidence == result.reasons


def test_topic_classifier_paper_and_default_fallback_details() -> None:
    paper = newsletter_ingest.classify_topic_detail(
        {
            "title": "Research roundup",
            "kind": "paper:arxiv",
            "url": "https://arxiv.org/abs/2605.00001",
        }
    )
    weak = newsletter_ingest.classify_topic_detail(
        {
            "title": "Weekly notes",
            "kind": "post",
            "url": "https://example.com/notes",
        }
    )

    assert paper.primary == "research_paper_general"
    assert paper.primary_display == "논문/리서치"
    assert paper.secondary == ("research",)
    assert paper.reasons == ("paper-kind",)
    assert weak.primary == "other_tech_report"
    assert weak.primary_display == "기타 테크 리포트"
    assert weak.secondary == ()


def test_content_evidence_context_schema_omits_private_body() -> None:
    item = {
        "title": "RAG agent report",
        "kind": "post",
        "url": "https://example.com/rag-agent",
        "sender": "digest@example.com",
        "received_at": "Mon, 04 May 2026 07:00:00 +0900",
        "classification_text": "PRIVATE subscriber-only detail with token=secret",
    }

    context = newsletter_ingest.analyze_topic_context(item, mode="shadow")

    assert context["evidence"]["private_context_used"] is True
    assert context["evidence"]["privacy_class"] == "private_context_used_not_persisted"
    dumped = json.dumps(context, ensure_ascii=False)
    assert "PRIVATE subscriber-only" not in dumped
    assert "token=secret" not in dumped
    assert context["topic_candidate"]["canonical_primary"] == "data_retrieval_knowledge"
    assert context["topic_selection"]["selected_topics"][0]["researcher_action"]


def test_publish_items_adds_shadow_topic_context_without_private_text(tmp_path: Path) -> None:
    item = {
        "title": "RAG agent report",
        "kind": "post",
        "url": "https://example.com/rag-agent",
        "sender": "digest@example.com",
        "received_at": "Mon, 04 May 2026 07:00:00 +0900",
        "classification_text": "PRIVATE body that must not persist",
    }

    raw_path, _page_path = newsletter_ingest.publish_items(
        wiki_root=tmp_path / "wiki",
        run_date="2026-05-04",
        source_path=tmp_path / "newsletters.jsonl",
        items=[item],
    )

    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    stored = payload["items"][0]
    assert payload["topic_selection_mode"] == "legacy"
    assert stored["primary_topic"] == "data_retrieval_knowledge"
    assert stored["primary_topic_display"] == "검색/RAG/지식그래프"
    assert "topic_context" in stored
    assert stored["topic_context"]["topic_selection"]["mode"] == "shadow"
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "PRIVATE body" not in dumped
    assert "classification_text" not in dumped


def test_topic_briefing_renders_sanitized_primary_secondary_metadata() -> None:
    briefing = newsletter_ingest.render_topic_briefing(
        run_date="2026-05-04",
        items=[
            {
                "title": "RAG agent report",
                "kind": "post",
                "url": "https://example.com/rag-agent",
                "sender": "digest@example.com",
                "received_at": "Mon, 04 May 2026 07:00:00 +0900",
                "classification_text": "Private body should never render. retrieval agent",
            }
        ],
        source_name="newsletters.jsonl",
    )

    assert "primary=`data_retrieval_knowledge`" in briefing
    assert "tags=`rag" in briefing
    assert "confidence=" in briefing
    assert "- 훅:" in briefing
    assert "- 시사점:" in briefing
    assert "- CTA/저장 포인트:" in briefing
    assert "Private body should never render" not in briefing


def test_topic_briefing_uses_public_article_summary_lines() -> None:
    briefing = newsletter_ingest.render_topic_briefing(
        run_date="2026-05-04",
        items=[
            {
                "title": "Digest subject",
                "article_title": "GraphRAG benchmark release",
                "kind": "post",
                "url": "https://example.com/graphrag",
                "summary_lines": [
                    "The public article introduces a retrieval benchmark for graph-grounded agents.",
                    "It compares GraphRAG indexing, query planning, and answer grounding across datasets.",
                    "The results help researchers track accuracy and latency trade-offs for RAG systems.",
                ],
                "classification_text": "private subscriber context should not render",
            }
        ],
        source_name="Apps Script relay",
    )

    assert "GraphRAG benchmark release" in briefing
    assert "retrieval benchmark for graph-grounded agents" in briefing
    assert "query planning" in briefing
    assert "accuracy and latency trade-offs" in briefing
    assert "카드 1" in briefing
    assert "- 맥락:" in briefing
    assert "private subscriber context" not in briefing


def test_apps_script_relay_ingest_normalizes_public_payload_and_omits_private_context(tmp_path: Path) -> None:
    payload_path = tmp_path / "relay.json"
    payload_path.write_text(
        json.dumps(
            {
                "query": "newer_than:7d",
                "items": [
                    {
                        "title": "Private digest subject",
                        "url": "https://example.com/rag-agent",
                        "kind": "post",
                        "sender": "Digest <digest@example.com>",
                        "receivedAt": "2026-05-04 08:00",
                        "articleTitle": "RAG agent systems",
                        "articleDescription": "Public article describes retrieval agents for knowledge graph search.",
                        "articleText": "Public article describes retrieval agents for knowledge graph search and evaluation.",
                        "summaryLines": [
                            "The article presents retrieval agents over knowledge graphs.",
                            "It details RAG orchestration, search grounding, and evaluation signals.",
                            "The result is useful for tracking agentic retrieval research.",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rc = apps_script_relay_ingest.main(
        [
            "--payload",
            str(payload_path),
            "--wiki-root",
            str(tmp_path / "wiki"),
            "--date",
            "2026-05-04",
            "--briefing-path",
            str(tmp_path / "briefing.md"),
        ]
    )

    assert rc == 0
    raw = json.loads((tmp_path / "wiki" / "raw" / "newsletters" / "2026-05-04" / "items.json").read_text())
    stored = raw["items"][0]
    assert stored["article_title"] == "RAG agent systems"
    assert stored["summary_lines"][0].startswith("The article presents")
    dumped = json.dumps(raw, ensure_ascii=False)
    assert "articleText" not in dumped
    assert "classification_text" not in dumped
    briefing = (tmp_path / "briefing.md").read_text(encoding="utf-8")
    assert "RAG orchestration" in briefing


def test_apps_script_relay_ingest_filters_linkedin_job_posts(tmp_path: Path) -> None:
    payload_path = tmp_path / "relay.json"
    payload_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "LinkedIn Job Alert",
                        "url": "https://www.linkedin.com/jobs/view/123",
                        "kind": "post",
                        "sender": "LinkedIn <jobs-noreply@linkedin.com>",
                        "snippet": "채용공고 추천",
                    },
                    {
                        "title": "AI Native Knowledge Graphs",
                        "url": "https://www.linkedin.com/comm/pulse/ai-native-knowledge-graphs",
                        "kind": "post",
                        "sender": "Newsletters <newsletters-noreply@linkedin.com>",
                        "articleTitle": "AI Native Knowledge Graphs",
                        "articleDescription": "Knowledge graph health assessment for RAG systems.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    _payload, items = apps_script_relay_ingest.load_relay_items(payload_path)

    assert len(items) == 1
    assert items[0]["article_title"] == "AI Native Knowledge Graphs"


def test_apps_script_relay_ingest_filters_linkedin_impression_notifications(tmp_path: Path) -> None:
    payload_path = tmp_path / "relay.json"
    payload_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "jiseong 님 업데이트의 지난 주 노출수",
                        "url": "https://www.linkedin.com/comm/feed/update/urn:li:activity:123",
                        "kind": "post",
                        "sender": "LinkedIn <notifications-noreply@linkedin.com>",
                        "snippet": "회원님의 업데이트가 지난 주 받은 노출수와 반응을 확인하세요.",
                    },
                    {
                        "title": "Issue #12 AI Native Knowledge Graphs",
                        "url": "https://www.linkedin.com/comm/pulse/ai-native-knowledge-graphs-health",
                        "kind": "post",
                        "sender": "Newsletters <newsletters-noreply@linkedin.com>",
                        "articleTitle": "AI Native Knowledge Graphs",
                        "articleDescription": "Knowledge graph health assessment for RAG systems.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    _payload, items = apps_script_relay_ingest.load_relay_items(payload_path)

    assert len(items) == 1
    assert items[0]["article_title"] == "AI Native Knowledge Graphs"


def test_topic_taxonomy_parity_fixture_matches_apps_script_smoke_intent() -> None:
    """Lock Python behavior to the GAS README parity smoke checklist."""
    cases = [
        (
            {
                "title": "Research paper on diffusion models",
                "kind": "paper",
                "url": "https://example.com/paper",
            },
            "논문/리서치",
        ),
        (
            {
                "title": "Inference benchmark for CUDA serving latency",
                "kind": "post",
                "url": "https://example.com/benchmark",
            },
            "인프라/배포",
        ),
        (
            {
                "title": "RAG agent over a knowledge graph",
                "kind": "post",
                "url": "https://example.com/rag-agent",
            },
            "검색/RAG/지식그래프",
        ),
        (
            {
                "title": "LLM agent developer workflow",
                "kind": "code",
                "url": "https://github.com/example/agent-workflow",
            },
            "LLM/에이전트",
        ),
        (
            {
                "title": "Enterprise pricing partnership launch",
                "kind": "post",
                "url": "https://example.com/product-launch",
            },
            "산업/제품 동향",
        ),
    ]

    for item, expected in cases:
        assert newsletter_ingest.classify_topic(item) == expected


def test_topic_taxonomy_parity_fixture_guards_false_positive_terms() -> None:
    assert (
        newsletter_ingest.classify_topic(
            {
                "title": "Research roundup on diffusion models",
                "kind": "post",
                "url": "https://example.com/research-roundup",
            }
        )
        == "기타 테크 리포트"
    )
    assert (
        newsletter_ingest.classify_topic(
            {
                "title": "Benchmarking inference latency",
                "kind": "post",
                "url": "https://example.com/benchmarking",
            }
        )
        != "산업/제품 동향"
    )


def test_apps_script_renderer_keeps_cardnews_contract_labels_in_parity() -> None:
    script = (
        Path(__file__).resolve().parents[3]
        / ".."
        / "integrations"
        / "google-apps-script"
        / "newsletter_archive_to_discord.gs"
    ).resolve()
    text = script.read_text(encoding="utf-8")

    expected_labels = [
        "훅",
        "맥락",
        "핵심 변화",
        "왜 중요한가",
        "근거/출처",
        "시사점",
        "CTA/저장 포인트",
    ]
    for label in expected_labels:
        assert label in text
    assert "메일 본문/비밀값은 게시하지 않고" in text
