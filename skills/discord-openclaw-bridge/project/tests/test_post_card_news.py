from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import discord_openclaw_bridge.post_card_news as post_card_news  # noqa: E402
from discord_openclaw_bridge.post_card_news import (  # noqa: E402
    CARD_NEWS_TITLE,
    CARD_SEPARATOR,
    DISCORD_SUPPRESS_EMBEDS_FLAG,
    LEAN_DISCLAIMER_WITH_EXCERPT,
    LEAN_DISCLAIMER_WITHOUT_EXCERPT,
    CardNewsQualityGateConfig,
    _append_card_news_audit,
    _build_card_news_audit_record,
    _card_content_fingerprint,
    _card_identity_fingerprint,
    _card_news_quality_gate_config_from_env,
    _card_news_skip_is_duplicate_related,
    _agent_context,
    _agent_duplicate_indices,
    _content_signature_hashes,
    _create_forum_card_news_thread,
    _evaluate_card_news_quality,
    _format_card_news_skip_ops_body,
    _format_card_news_skip_ops_title,
    _is_card_news_bot_message,
    _load_recent_published_card_history,
    _load_recent_published_agent_contexts,
    _rank_agent_contexts_for_cards,
    _load_recent_published_identities,
    _purge_previous_card_news_messages,
    _purge_previous_card_news_threads,
    _post_card_news_skip_ops_report,
    _sanitize_public_url,
    _select_cards,
    _split_discord_content,
    enrich_public_metadata,
    render_card_news_messages,
    run,
)


FORBIDDEN_LABELS = (
    "**제목**",
    "**토픽과 근거 수준**",
    "**3줄 요약**",
    "**왜 지금인가**",
    "**핵심 주장**",
    "**근거**",
    "**산업/현장 해석**",
    "**발췌**",
    "**읽는 법**",
)


def _strip_footer(card: str) -> str:
    body_lines: list[str] = []
    for line in card.split("\n"):
        if line.startswith("— "):
            break
        body_lines.append(line)
    return "\n".join(body_lines).rstrip()


def _has_long_overlap(a: str, b: str, *, min_len: int = 30) -> bool:
    if len(a) < min_len or len(b) < min_len:
        return False
    return any(a[i : i + min_len] in b for i in range(len(a) - min_len + 1))


def _body_paragraphs(card: str) -> list[str]:
    """Body chunks excluding the leading separator+title chunk and footer."""
    chunks = [chunk for chunk in _strip_footer(card).split("\n\n") if chunk.strip()]
    # First chunk is "{separator}\n**title**"; drop it.
    return chunks[1:]


def test_rich_card_uses_essay_paragraphs_with_connective() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "article_title": "GraphRAG systems benchmark",
                "url": "https://example.com/graphrag",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.92,
                "topic_reasons": ["rag", "knowledge graph"],
                "why_now": "지식그래프 기반 검색 평가가 빠르게 표준화되고 있다.",
                "claim": "그래프 기반 검색은 시스템 설계 선택지로 평가되어야 한다.",
                "mechanism": "인덱싱과 쿼리 플래닝이 생성기에 전달되는 근거 풀을 결정한다.",
                "evidence": "다섯 도메인에서 hop 수와 정확도 trade-off가 측정되었다.",
                "summary_lines": [
                    "그래프 기반 검색 에이전트의 비교 연구다.",
                    "운영 환경에서 인덱싱 비용이 어떻게 변하는지 검증한다.",
                    "정확도와 지연시간 trade-off를 함께 제시한다.",
                ],
                "source_name": "Newsletter A",
            }
        ],
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    for label in FORBIDDEN_LABELS:
        assert label not in card, f"unexpected label {label} in card: {card}"

    assert any(connector in card for connector in ("따라서", "다만", "구체적으로", "한편")), card

    paragraphs = _body_paragraphs(card)
    assert len(paragraphs) >= 3, paragraphs


def test_header_frames_cards_as_blog_publication_contract() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "article_title": "GraphRAG systems benchmark",
                "url": "https://example.com/graphrag",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.92,
                "topic_reasons": ["rag", "knowledge graph"],
                "why_now": "지식그래프 기반 검색 평가가 빠르게 표준화되고 있다.",
                "claim": "그래프 기반 검색은 시스템 설계 선택지로 평가되어야 한다.",
                "mechanism": "인덱싱과 쿼리 플래닝이 생성기에 전달되는 근거 풀을 결정한다.",
                "evidence": "다섯 도메인에서 hop 수와 정확도 trade-off가 측정되었다.",
                "summary_lines": [
                    "그래프 기반 검색 에이전트의 비교 연구다.",
                    "운영 환경에서 인덱싱 비용이 어떻게 변하는지 검증한다.",
                    "정확도와 지연시간 trade-off를 함께 제시한다.",
                ],
                "source_name": "Newsletter A",
            }
        ],
    }

    header = render_card_news_messages(payload, max_cards=1)[0]

    expected_sections = [
        "기술 블로그 브리핑",
        "대표 이미지(설명):",
        "> 3줄 요약",
        "## 왜 지금 이 이슈인가",
        "## 핵심 주장",
        "## 논증 구조",
        "## 산업사회학적·현장기반 해석",
        "## 앞으로 볼 질문",
        "## 카드뉴스·Discord 재사용안",
        "## 출처",
    ]
    for section in expected_sections:
        assert section in header
    assert "메일 본문·토큰·비밀값은 제외" in header
    assert "선별 1건 / 수집 1건" in header



def test_card_news_renderer_accepts_briefing_template_alias_fields() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "article_title": "GraphRAG carousel benchmark",
                "url": "https://example.com/graphrag",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.88,
                "topic_reasons": ["rag", "knowledge graph"],
                "hook": "RAG 평가가 그래프 근거 중심으로 이동하고 있습니다.",
                "context": "검색 품질은 인덱스와 쿼리 플래닝 선택에 좌우됩니다.",
                "core_change": "그래프 기반 검색은 카드뉴스의 핵심 변화로 설명해야 합니다.",
                "why_matters": "운영자는 정확도와 지연시간 trade-off를 함께 봐야 합니다.",
                "cta": "저장 후 원문의 평가 조건을 확인하세요.",
                "source_name": "Newsletter A",
            }
        ],
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    assert "RAG 평가가 그래프 근거 중심으로 이동하고 있습니다" in card
    assert "그래프 기반 검색은 카드뉴스의 핵심 변화" in card
    assert "검색 품질은 인덱스와 쿼리 플래닝" in card
    assert "운영자는 정확도와 지연시간 trade-off" in card
    assert "저장 후 원문의 평가 조건을 확인하세요" in card
    assert "rag" in card

def test_rich_card_omits_next_question_when_no_specific_input() -> None:
    payload = {
        "items": [
            {
                "article_title": "Specific input absent",
                "url": "https://example.com/no-summary",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.75,
                "why_now": "에이전트 운영 비용 평가가 본격화된다.",
                "claim": "기억 구조 선택이 운영 비용을 좌우한다.",
                "summary_lines": [],
                "source_name": "Newsletter B",
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    assert "**다음 질문**" not in card


def test_lean_card_is_excerpt_paragraph_with_honest_limit_line() -> None:
    payload = {
        "items": [
            {
                "article_title": "Agent memory benchmark",
                "public_excerpt": "장기 실행 에이전트의 검색 메모리 설계를 비교한 새 벤치마크가 발표되었다.",
                "url": "https://example.com/lean",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.65,
                "summary_lines": [],
                "source_name": "Newsletter L",
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    assert LEAN_DISCLAIMER_WITH_EXCERPT in card
    assert "발표되었습니다" in card  # excerpt + 합니다체 normalization
    for label in FORBIDDEN_LABELS + ("**다음 질문**",):
        assert label not in card, f"unexpected label {label} in lean card: {card}"


def test_skeletal_card_is_three_lines_max_no_topic_boilerplate() -> None:
    payload = {
        "items": [
            {
                "article_title": "Skeletal candidate",
                "url": "https://example.com/skel",
                "primary_topic_display": "오픈소스/코드",
                "topic_confidence": 0.3,
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    non_separator_lines = [line for line in card.split("\n") if line and CARD_SEPARATOR not in line]
    assert len(non_separator_lines) <= 3, non_separator_lines

    legacy_phrases = (
        "기술 확산 속도와 재현 가능성이",
        "검색 정확도보다 지식 구조",
        "모델 성능보다 기억 구조",
        "재사용 속도만큼 보안",
        "원문에서 방법, 평가 조건",
    )
    for phrase in legacy_phrases:
        assert phrase not in card, f"legacy boilerplate leaked: {phrase}"
    assert "수집 제목 기준" in card
    assert "세부 근거는 원문에서 확인" in card
    assert "`잠정`" in card


def test_footer_includes_source_url_topic_confidence_bucket() -> None:
    payload = {
        "items": [
            {
                "article_title": "Footer fixture",
                "url": "https://example.com/footer",
                "primary_topic_display": "멀티모달/비전",
                "topic_confidence": 0.85,
                "topic_reasons": ["vision", "encoder"],
                "claim": "비전 인코더는 입력 양식을 결정한다.",
                "mechanism": "토큰화 방식이 다운스트림 정확도를 바꾼다.",
                "summary_lines": ["요약 한 줄.", "요약 두 줄.", "요약 세 줄."],
                "source_name": "Vision Weekly",
            }
        ]
    }
    card = render_card_news_messages(payload, max_cards=1)[1]

    expected_prefix = "— Vision Weekly · <https://example.com/footer> · `멀티모달/비전` · `높음` · 단서 vision · encoder"
    assert expected_prefix in card

    # generic topic should drop the topic backtick
    generic_payload = {
        "items": [
            {
                "article_title": "Generic skeletal",
                "url": "https://example.com/gen",
                "primary_topic_display": "기타 테크 리포트",
                "topic_confidence": 0.55,
            }
        ]
    }
    generic_card = render_card_news_messages(generic_payload, max_cards=1)[1]
    assert "`기타 테크 리포트`" not in generic_card
    assert "`보통`" in generic_card

    # numeric confidence value must NOT appear anywhere
    for raw in ("0.85", "0.55"):
        assert raw not in card, raw
        assert raw not in generic_card, raw


def test_same_topic_cards_do_not_duplicate_body_text() -> None:
    payload = {
        "items": [
            {
                "article_title": "Card A — graph indexing",
                "url": "https://example.com/a",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.9,
                "topic_reasons": ["graph", "indexing"],
                "why_now": "그래프 인덱싱 평가가 운영 환경으로 옮겨지고 있다.",
                "claim": "그래프 인덱싱은 정확도에 영향을 미친다.",
                "mechanism": "인접 노드 정보가 생성기에 추가 컨텍스트를 제공한다.",
                "evidence": "두 도메인 데이터셋에서 정확도가 8% 상승했다.",
                "summary_lines": [
                    "그래프 인덱싱 사례 비교.",
                    "운영 환경에서 인접 노드 활용 방식을 분석한다.",
                    "데이터셋별 차이를 보고한다.",
                ],
                "source_name": "Search Weekly",
            },
            {
                "article_title": "Card B — query planner latency",
                "url": "https://example.com/b",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.8,
                "topic_reasons": ["query planner", "latency"],
                "why_now": "쿼리 플래너 지연 분석이 운영 로그로 확장되고 있다.",
                "claim": "쿼리 플래너 설계가 응답 지연시간을 결정한다.",
                "mechanism": "다단계 검색이 캐시 미스를 유발한다.",
                "evidence": "프로덕션 로그에서 평균 지연시간이 220ms 늘어났다.",
                "summary_lines": [
                    "쿼리 플래너 설계 비교.",
                    "운영 환경에서 캐시 전략을 검증한다.",
                    "지연시간 trade-off를 측정한다.",
                ],
                "source_name": "Latency Notes",
            },
        ]
    }

    messages = render_card_news_messages(payload, max_cards=2)
    assert len(messages) == 3, messages
    body_a = _strip_footer(messages[1])
    body_b = _strip_footer(messages[2])

    assert not _has_long_overlap(body_a, body_b), (body_a, body_b)


def test_header_card_uses_theme_sentence_not_machinery() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "article_title": "Header fixture",
                "url": "https://example.com/header",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.85,
                "why_now": "에이전트 운영 비용 평가가 본격화된다. 두 번째 문장은 무시된다.",
                "claim": "운영 비용은 기억 구조 선택에 좌우된다.",
                "summary_lines": ["요약 한 줄.", "요약 두 줄.", "요약 세 줄."],
            }
        ],
    }

    header = render_card_news_messages(payload, max_cards=1)[0]

    assert header.startswith(f"**{CARD_NEWS_TITLE} — 기술 블로그 브리핑 — 2026-05-05**")
    assert "구성:" not in header
    assert "기술 블로그 브리핑" in header
    assert "선별 1건" in header
    assert "수집 1건" in header
    assert "에이전트 운영 비용 평가가 본격화됩니다." in header
    assert "두 번째 문장은 무시된다." not in header


def test_register_normalization_converts_sentence_endings() -> None:
    payload = {
        "items": [
            {
                "article_title": "Register fixture",
                "url": "https://example.com/reg",
                "primary_topic_display": "오픈소스/코드",
                "topic_confidence": 0.8,
                "topic_reasons": ["oss"],
                "claim": "비용 감소는 도구 채택을 가속한다.",
                "mechanism": "운영 비용 구조가 도구 채택 속도를 결정한다.",
                "evidence": "사례 연구가 발표되었다. 채택 비율도 증가했다.",
                "summary_lines": [
                    "이 변화는 운영 비용을 줄인다.",
                    "운영 환경에서 오픈소스 도구 비교 결과를 다룬다.",
                    "후속 연구가 진행 중이다.",
                ],
                "source_name": "OSS Weekly",
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    for normalized in ("줄입니다.", "가속합니다.", "발표되었습니다.", "결정합니다.", "다룹니다.", "증가했습니다."):
        assert normalized in card, f"missing normalized form {normalized}: {card}"

    for raw in ("줄인다.", "가속한다.", "발표되었다.", "결정한다.", "다룬다.", "증가했다."):
        assert raw not in card, f"raw 한다체 leaked: {raw}: {card}"


# ============================================================
# Codex review (2026-05-05) — 8 new tests for essay contract v2
# ============================================================


def test_implication_paragraph_omits_when_no_action_signal() -> None:
    payload = {
        "items": [
            {
                "article_title": "Implication signal absent",
                "url": "https://example.com/im",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.8,
                "claim": "에이전트는 메모리 구조에 의존한다.",
                "mechanism": "기억 컴포넌트가 검색 우선순위를 바꾼다.",
                "summary_lines": [
                    "에이전트 메모리 사례 정리.",
                    "방법을 정량적으로 분석한다.",
                    "결과는 사례별로 다르다.",
                ],
                "source_name": "Memory Weekly",
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    for line in card.split("\n"):
        assert not line.startswith("따라서 "), f"unexpected 따라서 implication line: {line}"
    # `구체적으로` from claim+mechanism still satisfies the connective requirement
    assert "구체적으로" in card


def test_next_question_only_renders_when_interrogative() -> None:
    declarative_payload = {
        "items": [
            {
                "article_title": "Declarative summary",
                "url": "https://example.com/d",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.8,
                "claim": "X는 Y에 영향을 준다.",
                "mechanism": "Z가 발생한다.",
                "summary_lines": [
                    "리드 문장이다.",
                    "본문 문장이다.",
                    "이 논문은 결과를 보고한다.",
                ],
            }
        ]
    }
    decl_card = render_card_news_messages(declarative_payload, max_cards=1)[1]
    assert "**다음 질문**" not in decl_card

    interrogative_payload = {
        "items": [
            {
                "article_title": "Interrogative summary",
                "url": "https://example.com/i",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.8,
                "claim": "X는 Y에 영향을 준다.",
                "mechanism": "Z가 발생한다.",
                "summary_lines": [
                    "리드 문장이다.",
                    "본문 문장이다.",
                    "이 결과가 다른 운영 환경에서도 유지되는가?",
                ],
            }
        ]
    }
    int_card = render_card_news_messages(interrogative_payload, max_cards=1)[1]
    assert "**다음 질문**" in int_card
    assert "유지되는가?" in int_card

    transformable_payload = {
        "items": [
            {
                "article_title": "Transformable summary",
                "url": "https://example.com/t",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.8,
                "claim": "X는 Y에 영향을 준다.",
                "mechanism": "Z가 발생한다.",
                "summary_lines": [
                    "리드 문장이다.",
                    "본문 문장이다.",
                    "정확도와 지연시간 trade-off를 함께 제시합니다.",
                ],
            }
        ]
    }
    trans_card = render_card_news_messages(transformable_payload, max_cards=1)[1]
    assert "**다음 질문**" in trans_card
    assert "비교 가능한가?" in trans_card


def test_connector_avoids_double_topic_marker() -> None:
    """Connector `구체적으로는` must never collide with a mechanism subject's
    topic marker. The topic marker can appear on the first word, the second
    word, or deeper inside the noun phrase, so the renderer drops 는 from the
    bridging connector unconditionally."""

    case_first_word_collision = {
        "items": [
            {
                "article_title": "Topic marker collision (first word)",
                "url": "https://example.com/tm-1",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.8,
                "claim": "그래프 색인은 정확도에 영향을 미친다.",
                "mechanism": "엔터티는 그래프 노드에 매핑된다.",
                "summary_lines": ["x.", "y.", "z."],
            }
        ]
    }
    case_second_word_collision = {
        "items": [
            {
                "article_title": "Topic marker collision (second word)",
                "url": "https://example.com/tm-2",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.8,
                "claim": "검색 정확도는 모델 성능보다 색인 구조와 질의 계획에 의해 더 크게 좌우된다.",
                "mechanism": "그래프 색인은 엔터티 간 관계를 보존해 후보 문서의 정밀도를 끌어올린다.",
                "summary_lines": ["x.", "y.", "z."],
            }
        ]
    }

    for payload in (case_first_word_collision, case_second_word_collision):
        card = render_card_news_messages(payload, max_cards=1)[1]

        # No `구체적으로는` followed (directly or after a few words) by a 은/는 token.
        assert "구체적으로는" not in card, card
        assert not re.search(r"구체적으로는 [가-힣\s]*[가-힣]+(은|는)(\s|\.|$)", card), card

    card_first = render_card_news_messages(case_first_word_collision, max_cards=1)[1]
    assert "구체적으로 엔터티는" in card_first, card_first

    card_second = render_card_news_messages(case_second_word_collision, max_cards=1)[1]
    assert "구체적으로 그래프 색인은" in card_second, card_second
    # The exact double-topic-marker phrase from the regression report must not appear.
    assert "구체적으로는 그래프 색인은" not in card_second, card_second


def test_adjective_register_normalization() -> None:
    payload = {
        "items": [
            {
                "article_title": "Adjective register",
                "url": "https://example.com/adj",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.8,
                "claim": "정확도 분산이 크다.",
                "mechanism": "도메인별 정규화 정책의 영향이 컸다.",
                "evidence": "샘플 수가 많다. 측정 오차도 작다.",
                "summary_lines": [
                    "이 결과는 좋다.",
                    "운영 환경에서 추가 단서가 좋았다.",
                    "후속 작업이 필요하다.",
                ],
                "source_name": "Adj Weekly",
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    for normalized in ("큽니다.", "컸습니다.", "많습니다.", "좋습니다.", "좋았습니다.", "작습니다."):
        assert normalized in card, f"missing adjective form {normalized}: {card}"
    for raw in ("크다.", "컸다.", "많다.", "좋다.", "좋았다.", "작다."):
        assert raw not in card, f"raw adjective leaked: {raw}: {card}"


def test_same_topic_cards_use_distinct_discourse_frames() -> None:
    payload = {
        "items": [
            {
                "article_title": "Frame card A",
                "url": "https://example.com/fa",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.9,
                "topic_reasons": ["graph", "indexing"],
                "why_now": "OpenAI가 그래프 인덱싱 운영 평가를 공개했다.",
                "claim": "그래프 인덱싱은 정확도에 영향을 준다.",
                "mechanism": "인접 노드 정보가 컨텍스트 풀을 바꾼다.",
                "evidence": "두 도메인에서 정확도가 8% 상승했다.",
                "summary_lines": ["사례 정리.", "운영 환경 분석을 제공한다.", "추가 자료를 제시한다."],
                "source_name": "Search Weekly",
            },
            {
                "article_title": "Frame card B",
                "url": "https://example.com/fb",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.85,
                "topic_reasons": ["query planner", "latency"],
                "why_now": "벤더별 쿼리 플래너 비교가 새 보고서로 정리되었다.",
                "claim": "쿼리 플래너 설계가 지연시간을 결정한다.",
                "mechanism": "다단계 검색이 캐시 미스를 유발한다.",
                "evidence": "프로덕션 로그에서 지연시간이 220ms 늘어났다.",
                "summary_lines": ["설계 비교.", "운영 환경 캐시 전략을 검증한다.", "trade-off를 측정한다."],
                "source_name": "Latency Notes",
            },
        ]
    }

    messages = render_card_news_messages(payload, max_cards=2)
    paras_a = _body_paragraphs(messages[1])
    paras_b = _body_paragraphs(messages[2])

    # First-paragraph first words should differ (frame1 leads with why_now,
    # frame2 leads with claim text).
    first_word_a = paras_a[0].split()[0]
    first_word_b = paras_b[0].split()[0]
    assert first_word_a != first_word_b, (first_word_a, first_word_b)

    # Second-paragraph connectives differ:
    # frame1's second paragraph contains "구체적으로" (claim+mechanism connector);
    # frame2's second paragraph starts with "다만" (evidence caveat).
    assert "구체적으로" in paras_a[1], paras_a[1]
    assert paras_b[1].startswith("다만 "), paras_b[1]


def test_header_synthesizes_cross_topic_theme_when_diverse() -> None:
    payload = {
        "items": [
            {
                "article_title": "A",
                "url": "https://a",
                "primary_topic_display": "검색/RAG/지식그래프",
                "claim": "claim A",
                "topic_confidence": 0.8,
            },
            {
                "article_title": "B",
                "url": "https://b",
                "primary_topic_display": "LLM/에이전트",
                "claim": "claim B",
                "topic_confidence": 0.8,
            },
        ]
    }
    header = render_card_news_messages(payload, max_cards=2)[0]

    assert "오늘의 축" not in header
    assert "무엇을 기억하고" in header
    assert "검색/RAG/지식그래프" in header
    assert "LLM/에이전트" in header
    for boilerplate in (
        "공개 링크를 한 번에 소비하기보다",
        "토픽 간 연결과 검증 질문",
        "같은 변화 방향을 가리키는지",
        "후속 검토를 좁힌다",
    ):
        assert boilerplate not in header


def test_lean_disclaimer_variant_for_excerpt_present() -> None:
    payload = {
        "items": [
            {
                "article_title": "Lean fixture",
                "public_excerpt": "공개 발췌 단락이다.",
                "url": "https://example.com/l",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.65,
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    assert LEAN_DISCLAIMER_WITH_EXCERPT in card
    assert LEAN_DISCLAIMER_WITHOUT_EXCERPT not in card
    # Old disclaimer copy must not leak
    assert "본문 근거가 아직 얇아 원문 확인이 필요합니다." not in card


def test_evidence_blockquote_drops_classification_reasons_line() -> None:
    payload = {
        "items": [
            {
                "article_title": "Blockquote test",
                "url": "https://example.com/bq",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 0.9,
                "topic_reasons": ["rag", "graph"],
                "claim": "claim text.",
                "mechanism": "mechanism text.",
                "evidence": "evidence sentence.",
                "summary_lines": ["a.", "b.", "c."],
                "source_name": "Newsletter",
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    assert "분류 근거" not in card
    assert "> Newsletter: evidence sentence." in card
    # Bullet markers must not appear in the blockquote
    assert "> -" not in card
    # Reasons surface in the footer instead
    assert "단서 rag · graph" in card


def test_card_news_renderer_deduplicates_titles_and_prioritizes_topic_spread() -> None:
    payload = {
        "items": [
            {"article_title": "Same", "url": "https://a", "primary_topic_display": "검색/RAG/지식그래프"},
            {"article_title": "Same", "url": "https://b", "primary_topic_display": "검색/RAG/지식그래프"},
            {"article_title": "Vision", "url": "https://c", "primary_topic_display": "멀티모달/비전"},
        ]
    }

    messages = render_card_news_messages(payload, max_cards=3)
    joined = "\n".join(messages)

    assert joined.count(CARD_SEPARATOR) == 2
    assert "**Same**" in joined
    assert "**Vision**" in joined
    assert "**제목**" not in joined



def test_select_cards_collapses_repeated_graph_embedding_family() -> None:
    items = [
        {
            "article_title": "Dynamic graph embedding for anomaly detection",
            "url": "https://example.com/dynamic-graph",
            "primary_topic_display": "검색/RAG/지식그래프",
            "public_excerpt": "Dynamic graph embedding benchmark.",
        },
        {
            "article_title": "Heterogeneous graph embedding for recommendation",
            "url": "https://example.com/heterogeneous-graph",
            "primary_topic_display": "검색/RAG/지식그래프",
            "public_excerpt": "Heterogeneous graph embedding benchmark.",
        },
        {
            "article_title": "Vision-language evaluation",
            "url": "https://example.com/vision",
            "primary_topic_display": "멀티모달/비전",
            "public_excerpt": "Distinct vision-language benchmark.",
        },
    ]

    selected = _select_cards(items, max_cards=3)
    titles = [item["article_title"] for item in selected]

    assert len(selected) == 2
    assert any("graph embedding" in title.lower() for title in titles)
    assert "Vision-language evaluation" in titles

def test_card_news_bot_message_matcher_targets_only_card_news_bot_messages() -> None:
    assert _is_card_news_bot_message({"content": f"**{CARD_NEWS_TITLE}**", "author": {"bot": True}})
    assert not _is_card_news_bot_message({"content": f"**{CARD_NEWS_TITLE}**", "author": {"bot": False}})
    assert not _is_card_news_bot_message({"content": "집현전-Claw 뉴스레터 수집 브리핑", "author": {"bot": True}})


def test_card_news_purge_deletes_only_prior_card_news_messages() -> None:
    import httpx

    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": "1", "content": f"**{CARD_NEWS_TITLE}**\nold", "author": {"bot": True}},
                    {"id": "2", "content": "집현전-Claw 뉴스레터 수집 브리핑", "author": {"bot": True}},
                    {"id": "3", "content": f"**{CARD_NEWS_TITLE}**", "author": {"bot": False}},
                ],
                request=request,
            )
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204, request=request)
        raise AssertionError(json.dumps({"method": request.method}))

    async def scenario() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _purge_previous_card_news_messages(
                client,
                "https://discord.com/api/v10/channels/1/messages",
                headers={"Authorization": "Bot test"},
            )

    purged = asyncio.run(scenario())

    assert purged == 1
    assert deleted == ["1"]


def test_card_news_thread_purge_archives_when_delete_is_forbidden() -> None:
    import httpx

    patched: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "threads": [
                        {
                            "id": "1501834279691489420",
                            "name": "2026-05-07 기술 브리핑 카드뉴스",
                            "parent_id": "111111111111111111",
                        }
                    ]
                },
                request=request,
            )
        if request.method == "DELETE":
            return httpx.Response(403, json={"code": 50013, "message": "Missing Permissions"}, request=request)
        if request.method == "PATCH":
            patched.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"id": "1501834279691489420"}, request=request)
        raise AssertionError(json.dumps({"method": request.method}))

    async def scenario() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _purge_previous_card_news_threads(
                client,
                "https://discord.com/api/v10/guilds/1/threads/active",
                headers={"Authorization": "Bot test"},
            )

    purged = asyncio.run(scenario())

    assert purged == 1
    assert patched == [{"archived": True, "locked": False}]


def test_forum_thread_creation_uses_thread_starter_with_suppressed_embeds() -> None:
    import httpx

    captured_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v10/channels/1501073491921993758/threads"
        captured_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(201, json={"id": "1502000000000000000"}, request=request)

    async def scenario() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _create_forum_card_news_thread(
                client,
                "https://discord.com/api/v10/channels/1501073491921993758",
                headers={"Authorization": "Bot test"},
                name="2026-05-05 기술 브리핑 카드뉴스",
                content=f"**{CARD_NEWS_TITLE}**\nheader",
            )

    thread_id = asyncio.run(scenario())

    assert thread_id == "1502000000000000000"
    assert captured_payloads == [
        {
            "name": "2026-05-05 기술 브리핑 카드뉴스",
            "auto_archive_duration": 1440,
            "message": {
                "content": f"**{CARD_NEWS_TITLE}**\nheader",
                "allowed_mentions": {"parse": []},
                "flags": DISCORD_SUPPRESS_EMBEDS_FLAG,
            },
        }
    ]


def test_forum_thread_creation_retries_transient_read_errors() -> None:
    import httpx

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadError("temporary EOF", request=request)
        assert request.method == "POST"
        assert request.url.path == "/api/v10/channels/1501073491921993758/threads"
        return httpx.Response(201, json={"id": "1502000000000000001"}, request=request)

    async def scenario() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _create_forum_card_news_thread(
                client,
                "https://discord.com/api/v10/channels/1501073491921993758",
                headers={"Authorization": "Bot test"},
                name="2026-05-14 기술 브리핑 카드뉴스",
                content=f"**{CARD_NEWS_TITLE}**\nheader",
            )

    thread_id = asyncio.run(scenario())

    assert thread_id == "1502000000000000001"
    assert attempts == 2


def test_split_discord_content_keeps_chunks_under_limit() -> None:
    content = "intro\n\n" + ("A" * 1200) + "\n\n" + ("B" * 1200)

    chunks = _split_discord_content(content, limit=1500)

    assert len(chunks) == 2
    assert all(len(chunk) <= 1500 for chunk in chunks)
    assert chunks[0].startswith("intro")
    assert chunks[1].startswith("B")


def test_footer_strips_sensitive_url_query_params() -> None:
    payload = {
        "items": [
            {
                "article_title": "Sensitive URL fixture",
                "url": "https://example.com/post?token=secret&utm_source=x&id=7",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.8,
                "summary_lines": ["요약 한 줄.", "요약 두 줄.", "요약 세 줄."],
            }
        ]
    }

    card = render_card_news_messages(payload, max_cards=1)[1]

    assert "token=secret" not in card
    assert "utm_source" not in card
    assert "https://example.com/post?id=7" in card


def test_sanitizer_strips_newsletter_tracking_query_params() -> None:
    url = (
        "https://www.linkedin.com/comm/pulse/post"
        "?midToken=secret&midSig=sig&trkEmail=mail&lipi=abc&id=7&utm_medium=email"
    )

    assert _sanitize_public_url(url) == "https://www.linkedin.com/comm/pulse/post?id=7"


def test_selection_prefers_substantive_article_over_tracking_profile_link() -> None:
    payload = {
        "items": [
            {
                "article_title": "Same Article",
                "url": "https://medium.com/@author?source=email-tracking",
                "primary_topic_display": "LLM/에이전트",
                "public_excerpt": "Same Article",
            },
            {
                "article_title": "Same Article",
                "url": "https://medium.com/ai-advances/same-article-123",
                "primary_topic_display": "LLM/에이전트",
                "public_excerpt": "이 글은 에이전트 메모리 구조가 검색 방식과 장기 실행 품질에 미치는 차이를 비교합니다.",
            },
        ]
    }

    messages = render_card_news_messages(payload, max_cards=1)
    joined = "\n".join(messages)

    assert "https://medium.com/ai-advances/same-article-123" in joined
    assert "source=email-tracking" not in joined
    assert "에이전트 메모리 구조" in joined


def test_selection_omits_title_only_cards_when_evidence_exists() -> None:
    payload = {
        "items": [
            {
                "article_title": "Important but thin RAG article",
                "url": "https://example.com/thin-rag",
                "primary_topic_display": "검색/RAG/지식그래프",
            },
            {
                "article_title": "Evidence backed agent article",
                "url": "https://example.com/agent",
                "primary_topic_display": "LLM/에이전트",
                "public_excerpt": "에이전트 메모리 설계가 검색 근거와 장기 실행 비용을 함께 바꾼다는 공개 요약입니다.",
            },
        ]
    }

    joined = "\n".join(render_card_news_messages(payload, max_cards=2))

    assert "Evidence backed agent article" in joined
    assert "Important but thin RAG article" not in joined


def test_selection_deduplicates_same_paper_across_code_and_arxiv_cards() -> None:
    payload = {
        "items": [
            {
                "article_title": "GitHub - user/ToMA: Implementation of the paper \"Topology-Aware Representation Alignment\"",
                "url": "https://github.com/user/ToMA",
                "primary_topic_display": "오픈소스/코드",
                "public_excerpt": "Implementation of the paper \"Topology-Aware Representation Alignment\" - user/ToMA",
            },
            {
                "article_title": "[2604.26370] Topology-Aware Representation Alignment",
                "url": "https://arxiv.org/abs/2604.26370",
                "primary_topic_display": "논문/리서치",
                "public_excerpt": "Vision-language models often generalize poorly to specialized domains.",
            },
        ]
    }

    joined = "\n".join(render_card_news_messages(payload, max_cards=2))

    assert joined.count(CARD_SEPARATOR) == 1
    assert "arxiv.org/abs/2604.26370" in joined
    assert "github.com/user/ToMA" not in joined


def test_public_metadata_enrichment_adds_article_description_before_render() -> None:
    import httpx

    payload = {
        "items": [
            {
                "article_title": "Thin Paper Card",
                "url": "https://example.com/paper?utm_source=newsletter&token=secret",
                "primary_topic_display": "논문/리서치",
                "public_excerpt": "Thin Paper Card",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "token=secret" not in str(request.url)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><head>"
                "<meta name=\"description\" content=\"이 논문은 검색 증강 생성 시스템에서 그래프 구조가 근거 선택과 평가 안정성에 미치는 영향을 실험으로 비교합니다.\">"
                "<title>Thin Paper Card</title>"
                "</head></html>"
            ),
            request=request,
        )

    async def scenario() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await enrich_public_metadata(payload, client, max_items=1)

    enriched = asyncio.run(scenario())
    joined = "\n".join(render_card_news_messages(enriched, max_cards=1))

    assert "그래프 구조가 근거 선택" in joined
    assert "token=secret" not in joined
    assert "utm_source" not in joined


def _gate_card(idx: int, *, url: str | None = None, evidence: bool = True) -> dict[str, object]:
    card: dict[str, object] = {
        "article_title": f"Gate Card {idx}",
        "url": url or f"https://example.com/research/{idx}?utm_source=newsletter&token=secret-{idx}",
        "primary_topic_display": "LLM/에이전트",
        "source_name": "Gate Weekly",
    }
    if evidence:
        card["public_excerpt"] = f"에이전트 메모리 설계와 검색 근거를 비교하는 공개 요약 {idx}입니다."
    return card


def test_quality_gate_config_defaults_and_env_overrides(monkeypatch, tmp_path) -> None:
    for name in (
        "DISCORD_CARD_NEWS_QUALITY_GATE",
        "DISCORD_CARD_NEWS_AUDIT_PATH",
        "DISCORD_CARD_NEWS_HISTORY_DAYS",
        "DISCORD_CARD_NEWS_MIN_PUBLISHABLE_CARDS",
        "DISCORD_CARD_NEWS_MIN_NEW_CARDS",
        "DISCORD_CARD_NEWS_MAX_PREVIOUS_OVERLAP_RATIO",
        "DISCORD_CARD_NEWS_MIN_EVIDENCE_CARDS",
        "DISCORD_CARD_NEWS_CONTENT_SIMILARITY_THRESHOLD",
        "DISCORD_CARD_NEWS_AGENT_DEDUPE",
        "DISCORD_CARD_NEWS_AGENT_DEDUPE_MAX_PREVIOUS",
        "DISCORD_CARD_NEWS_AGENT_DEDUPE_TIMEOUT_SEC",
        "NEWSLETTER_WIKI_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    default_config = _card_news_quality_gate_config_from_env()

    assert default_config.enabled is True
    assert default_config.history_days == 14
    assert default_config.min_publishable_cards == 3
    assert default_config.min_new_cards == 3
    assert default_config.max_previous_overlap_ratio == 0.5
    assert default_config.min_evidence_cards == 2
    assert default_config.content_similarity_threshold == 0.72
    assert default_config.agent_dedupe_enabled is False
    assert default_config.agent_dedupe_max_previous == 5
    assert default_config.agent_dedupe_timeout_sec == 45.0

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("DISCORD_CARD_NEWS_QUALITY_GATE", "0")
    monkeypatch.setenv("DISCORD_CARD_NEWS_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("DISCORD_CARD_NEWS_HISTORY_DAYS", "3")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_PUBLISHABLE_CARDS", "4")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_NEW_CARDS", "2")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MAX_PREVIOUS_OVERLAP_RATIO", "0.25")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_EVIDENCE_CARDS", "3")
    monkeypatch.setenv("DISCORD_CARD_NEWS_CONTENT_SIMILARITY_THRESHOLD", "0.8")
    monkeypatch.setenv("DISCORD_CARD_NEWS_AGENT_DEDUPE", "1")
    monkeypatch.setenv("DISCORD_CARD_NEWS_AGENT_DEDUPE_MAX_PREVIOUS", "5")
    monkeypatch.setenv("DISCORD_CARD_NEWS_AGENT_DEDUPE_TIMEOUT_SEC", "9.5")

    override_config = _card_news_quality_gate_config_from_env()

    assert override_config.enabled is False
    assert override_config.audit_path == audit_path
    assert override_config.history_days == 3
    assert override_config.min_publishable_cards == 4
    assert override_config.min_new_cards == 2
    assert override_config.max_previous_overlap_ratio == 0.25
    assert override_config.min_evidence_cards == 3
    assert override_config.content_similarity_threshold == 0.8
    assert override_config.agent_dedupe_enabled is True
    assert override_config.agent_dedupe_max_previous == 5
    assert override_config.agent_dedupe_timeout_sec == 9.5


def test_identity_fingerprint_uses_sanitized_url_but_content_tracks_evidence() -> None:
    base = _gate_card(1, url="https://example.com/post?utm_source=mail&token=secret&id=7")
    changed = {
        **base,
        "public_excerpt": "새로운 실험 조건과 평가 결과를 설명하는 공개 요약입니다.",
    }

    assert _card_identity_fingerprint(base) == _card_identity_fingerprint(changed)
    assert _card_content_fingerprint(base) != _card_content_fingerprint(changed)
    assert "secret" not in _card_identity_fingerprint(base)
    assert "utm_source" not in _card_identity_fingerprint(base)


def test_quality_gate_skips_when_previous_overlap_exceeds_threshold() -> None:
    cards = [_gate_card(idx) for idx in range(7)]
    previous = {_card_identity_fingerprint(card) for card in cards[:5]}
    config = CardNewsQualityGateConfig(
        min_publishable_cards=3,
        min_new_cards=1,
        max_previous_overlap_ratio=0.5,
        min_evidence_cards=2,
    )

    evaluation = _evaluate_card_news_quality(cards, previous, config)

    assert evaluation["decision"] == "skip"
    assert "max_previous_overlap_ratio" in evaluation["reason_codes"]
    assert evaluation["counts"]["selected"] == 7
    assert evaluation["counts"]["repeated"] == 5
    assert evaluation["counts"]["new"] == 2


def test_duplicate_quality_skip_formats_ops_report(tmp_path) -> None:
    cards = [_gate_card(idx) for idx in range(4)]
    previous = {_card_identity_fingerprint(card) for card in cards[:3]}
    config = CardNewsQualityGateConfig(
        min_publishable_cards=3,
        min_new_cards=3,
        max_previous_overlap_ratio=0.5,
        min_evidence_cards=2,
    )
    evaluation = _evaluate_card_news_quality(cards, previous, config)
    payload = {"date": "2026-05-17"}
    source = tmp_path / "raw" / "newsletters" / "2026-05-17" / "items.json"

    assert evaluation["decision"] == "skip"
    assert _card_news_skip_is_duplicate_related(evaluation) is True

    title = _format_card_news_skip_ops_title(payload, evaluation)
    body = _format_card_news_skip_ops_body(
        payload=payload,
        source=source,
        evaluation=evaluation,
        audit_path=tmp_path / "audit.jsonl",
        token_source="guard",
    )

    assert "2026-05-17" in title
    assert "Card News 보류" in title
    assert "카드뉴스 발행 보류" in body
    assert "duplicate/newness reasons" in body
    assert "min_new_cards" in body
    assert "max_previous_overlap_ratio" in body
    assert "new: `1`" in body
    assert "overlap: `0.75`" in body
    assert "DISCORD_CARD_NEWS_QUALITY_GATE=0" in body


def test_non_duplicate_quality_skip_does_not_trigger_ops_report() -> None:
    evaluation = {
        "decision": "skip",
        "reason_codes": ["min_evidence_cards"],
        "counts": {"selected": 3, "new": 3, "overlap_ratio": 0.0},
        "thresholds": {},
    }

    assert _card_news_skip_is_duplicate_related(evaluation) is False


def test_duplicate_quality_skip_posts_ops_forum_thread(tmp_path, monkeypatch) -> None:
    posts: list[dict[str, object]] = []

    class FakeClient:
        async def get(self, url, *, headers):
            return post_card_news.httpx.Response(
                200,
                json={"type": 15},
                request=post_card_news.httpx.Request("GET", url),
            )

        async def post(self, url, *, headers, json):
            posts.append({"url": url, "json": json, "headers": headers})
            return post_card_news.httpx.Response(
                200,
                json={"id": "ops-thread-1"},
                request=post_card_news.httpx.Request("POST", url),
            )

    evaluation = {
        "decision": "skip",
        "reason_codes": ["min_new_cards", "max_previous_overlap_ratio"],
        "counts": {"selected": 4, "publishable": 4, "evidence": 4, "new": 1, "repeated": 3, "overlap_ratio": 0.75},
        "thresholds": {"min_new_cards": 3, "max_previous_overlap_ratio": 0.5},
    }
    monkeypatch.setenv("DISCORD_GUARD_BOT_TOKEN", "guard-token")
    monkeypatch.setenv("DISCORD_OPS_REPORT_CHANNEL_ID", "1502980129343672504")

    thread_id = asyncio.run(
        _post_card_news_skip_ops_report(
            FakeClient(),
            payload={"date": "2026-05-17"},
            source=tmp_path / "raw" / "newsletters" / "2026-05-17" / "items.json",
            evaluation=evaluation,
            audit_path=tmp_path / "audit.jsonl",
        )
    )

    assert thread_id == "ops-thread-1"
    assert posts[0]["url"] == "https://discord.com/api/v10/channels/1502980129343672504/threads"
    payload = posts[0]["json"]
    assert "Card News 보류" in payload["name"]
    assert payload["message"]["allowed_mentions"] == {"parse": []}
    assert payload["message"]["flags"] == DISCORD_SUPPRESS_EMBEDS_FLAG
    assert "카드뉴스 발행 보류" in payload["message"]["content"]


def test_quality_gate_allows_enough_new_publishable_evidence_cards() -> None:
    cards = [_gate_card(idx) for idx in range(3)]
    config = CardNewsQualityGateConfig(
        min_publishable_cards=3,
        min_new_cards=3,
        max_previous_overlap_ratio=0.5,
        min_evidence_cards=2,
    )

    evaluation = _evaluate_card_news_quality(cards, set(), config)

    assert evaluation["decision"] == "publish"
    assert evaluation["reason_codes"] == []
    assert evaluation["counts"]["publishable"] == 3
    assert evaluation["counts"]["evidence"] == 3


def test_quality_gate_skips_skeletal_fallback_cards() -> None:
    cards = [_gate_card(idx, evidence=False) for idx in range(3)]
    config = CardNewsQualityGateConfig(
        min_publishable_cards=3,
        min_new_cards=3,
        max_previous_overlap_ratio=0.5,
        min_evidence_cards=2,
    )

    evaluation = _evaluate_card_news_quality(cards, set(), config)

    assert evaluation["decision"] == "skip"
    assert "min_publishable_cards" in evaluation["reason_codes"]
    assert "min_evidence_cards" in evaluation["reason_codes"]
    assert evaluation["counts"]["publishable"] == 0
    assert evaluation["counts"]["evidence"] == 0


def test_audit_jsonl_is_sanitized_and_history_reads_recent_publish(tmp_path) -> None:
    audit_path = tmp_path / "card-news-audit.jsonl"
    cards = [_gate_card(1)]
    evaluation = {
        "thresholds": {"min_new_cards": 1},
        "counts": {"selected": 1, "publishable": 1, "evidence": 1, "new": 1, "repeated": 0, "overlap_ratio": 0.0},
        "reason_codes": [],
    }

    record = _build_card_news_audit_record(
        decision="publish",
        payload={"date": "2026-05-09"},
        source=tmp_path / "raw" / "newsletters" / "2026-05-09" / "items.json",
        cards=cards,
        evaluation=evaluation,
        publish_metadata={"message_count": 2, "purged_count": 0},
    )
    _append_card_news_audit(audit_path, record)

    raw = audit_path.read_text(encoding="utf-8")
    stored = json.loads(raw)

    assert stored["decision"] == "publish"
    assert stored["source_ref"] == "2026-05-09"
    assert stored["cards"][0]["url"] == "https://example.com/research/1"
    assert "secret" not in raw
    assert "utm_source" not in raw
    assert _load_recent_published_identities(audit_path, history_days=14) == {
        _card_identity_fingerprint(cards[0])
    }


def test_run_skips_before_discord_calls_when_quality_gate_fails(monkeypatch, tmp_path, capsys) -> None:
    source = tmp_path / "items.json"
    audit_path = tmp_path / "audit.jsonl"
    previous_cards = [_gate_card(idx) for idx in range(5)]
    current_cards = [*previous_cards, _gate_card(6), _gate_card(7)]
    source.write_text(json.dumps({"date": "2026-05-09", "items": current_cards}), encoding="utf-8")
    previous_record = _build_card_news_audit_record(
        decision="publish",
        payload={"date": "2026-05-08"},
        source=tmp_path / "items-prev.json",
        cards=previous_cards,
        evaluation={
            "thresholds": {},
            "counts": {"selected": 5, "publishable": 5, "evidence": 5, "new": 5, "repeated": 0, "overlap_ratio": 0.0},
            "reason_codes": [],
        },
    )
    _append_card_news_audit(audit_path, previous_record)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_CARD_NEWS_CHANNEL_ID", "111111111111111111")
    monkeypatch.setenv("DISCORD_CARD_NEWS_SOURCE", str(source))
    monkeypatch.setenv("DISCORD_CARD_NEWS_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("DISCORD_CARD_NEWS_MAX_CARDS", "7")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_NEW_CARDS", "3")
    monkeypatch.setenv("DISCORD_CARD_NEWS_ENRICH_PUBLIC_URLS", "0")

    asyncio.run(run())

    output = capsys.readouterr().out
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert "skipped card news quality_gate" in output
    assert records[-1]["decision"] == "skip"
    assert "max_previous_overlap_ratio" in records[-1]["reason_codes"]


def test_selection_used_for_gate_matches_rendered_card_count() -> None:
    payload = {"date": "2026-05-09", "items": [_gate_card(idx) for idx in range(4)]}
    cards = _select_cards([item for item in payload["items"] if isinstance(item, dict)], max_cards=3)
    messages = render_card_news_messages(payload, max_cards=3)

    assert len(cards) == 3
    assert len(messages) == len(cards) + 1
    for card in cards:
        assert str(card["article_title"]) in "\n".join(messages)


def _quality_card(idx: int, *, url: str | None = None, topic: str = "LLM/에이전트") -> dict[str, object]:
    return {
        "article_title": f"Agent memory evaluation {idx}",
        "url": url or f"https://example.com/articles/{idx}?utm_source=newsletter&token=secret&id={idx}",
        "primary_topic_display": topic,
        "topic_confidence": 0.82,
        "claim": f"에이전트 메모리 평가 {idx}는 운영 비용과 검색 품질을 함께 바꿉니다.",
        "mechanism": "검색 근거와 장기 실행 로그가 의사결정 기준을 바꿉니다.",
        "evidence": "공개 벤치마크에서 정확도와 지연시간 trade-off가 함께 측정되었습니다.",
        "summary_lines": [
            "운영 환경에서 메모리 설계 차이를 비교합니다.",
            "검색 근거와 비용을 함께 검증합니다.",
            "이 결과가 다른 운영 환경에서도 유지되는가?",
        ],
        "source_name": "Agent Weekly",
    }


def test_quality_fingerprints_separate_identity_from_content() -> None:
    base = _quality_card(1, url="https://example.com/post?utm_source=x&token=secret&id=7")
    changed = dict(base)
    changed["evidence"] = "새 공개 실험에서 비용 절감 효과가 추가로 보고되었습니다."

    assert _card_identity_fingerprint(base) == _card_identity_fingerprint(changed)
    assert _card_content_fingerprint(base) != _card_content_fingerprint(changed)


def test_quality_content_signature_detects_same_story_across_different_urls() -> None:
    previous = _quality_card(1, url="https://arxiv.org/abs/2605.03546")
    previous.update(
        {
            "article_title": '📑Google: "AI 의사가 진짜 의사를 2.5배 이겼다고?"',
            "claim": "AI 의사 평가에서 대화형 진단 모델이 임상 의사와 비교되었습니다.",
            "mechanism": "동일한 증상 문진과 진단 추론 과정을 공개 초록 기준으로 비교합니다.",
            "evidence": "공개 벤치마크에서 정확도와 상담 품질 지표가 함께 보고되었습니다.",
            "summary_lines": [
                "AI 의사 성능 비교를 다룹니다.",
                "진단 대화와 평가 지표를 검토합니다.",
                "임상 적용 전 검증 한계를 확인해야 합니다.",
            ],
            "public_excerpt": "AI 의사 모델과 실제 의사의 진단 대화 성능을 공개 평가로 비교한 연구입니다.",
        }
    )
    current = dict(previous)
    current["url"] = "https://research.google/blog/amie-doctor-evaluation"
    current["article_title"] = 'Google: "AI 의사가 진짜 의사를 2.5배 이겼다고?"'

    config = CardNewsQualityGateConfig(
        audit_path=Path("unused"),
        min_publishable_cards=1,
        min_new_cards=1,
        max_previous_overlap_ratio=0.5,
        min_evidence_cards=1,
    )
    evaluation = _evaluate_card_news_quality(
        [current],
        {_card_identity_fingerprint(previous)},
        config,
        [set(_content_signature_hashes(previous))],
    )

    assert _card_identity_fingerprint(current) != _card_identity_fingerprint(previous)
    assert evaluation["decision"] == "skip"
    assert evaluation["counts"]["identity_repeated"] == 0
    assert evaluation["counts"]["content_repeated"] == 1
    assert evaluation["counts"]["new"] == 0
    assert "min_new_cards" in evaluation["reason_codes"]
    assert "max_previous_overlap_ratio" in evaluation["reason_codes"]


def test_quality_content_similarity_does_not_block_distinct_story_with_new_url() -> None:
    previous = _quality_card(1, url="https://example.com/old")
    current = _quality_card(2, url="https://example.com/new", topic="검색/RAG/지식그래프")
    current.update(
        {
            "article_title": "Graph retrieval benchmark exposes cache latency trade-offs",
            "claim": "그래프 검색 벤치마크는 캐시 지연시간과 검색 정확도의 상충관계를 분리해 측정합니다.",
            "mechanism": "쿼리 플래너가 이웃 노드 확장 폭과 캐시 미스율을 함께 조정합니다.",
            "evidence": "공개 로그 분석에서 지연시간과 정확도 변화가 서로 다른 축으로 보고되었습니다.",
            "summary_lines": [
                "그래프 검색 평가는 캐시 정책을 따로 비교합니다.",
                "정확도와 지연시간의 상충관계를 계량합니다.",
                "운영 환경에서 캐시 정책을 재검토해야 합니다.",
            ],
        }
    )
    config = CardNewsQualityGateConfig(
        audit_path=Path("unused"),
        min_publishable_cards=1,
        min_new_cards=1,
        max_previous_overlap_ratio=0.5,
        min_evidence_cards=1,
    )

    evaluation = _evaluate_card_news_quality(
        [current],
        {_card_identity_fingerprint(previous)},
        config,
        [set(_content_signature_hashes(previous))],
    )

    assert evaluation["decision"] == "publish"
    assert evaluation["counts"]["content_repeated"] == 0
    assert evaluation["counts"]["new"] == 1


def test_quality_gate_counts_agent_duplicate_indices_as_repeated() -> None:
    current = _quality_card(1, url="https://mirror.example.com/new-url")
    config = CardNewsQualityGateConfig(
        audit_path=Path("unused"),
        min_publishable_cards=1,
        min_new_cards=1,
        max_previous_overlap_ratio=0.5,
        min_evidence_cards=1,
        agent_dedupe_enabled=True,
    )

    evaluation = _evaluate_card_news_quality([current], set(), config, [], {0})

    assert evaluation["decision"] == "skip"
    assert evaluation["counts"]["identity_repeated"] == 0
    assert evaluation["counts"]["content_repeated"] == 0
    assert evaluation["counts"]["agent_repeated"] == 1
    assert evaluation["counts"]["new"] == 0


def test_agent_duplicate_indices_uses_openclaw_json(monkeypatch) -> None:
    previous = _quality_card(1, url="https://arxiv.org/abs/2605.03546")
    current = dict(previous)
    current["url"] = "https://research.google/blog/amie-doctor-evaluation"
    requests: list[dict[str, object]] = []

    class FakeGatewayClient:
        def __init__(self, policy):
            self.policy = policy

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def chat_completion(self, model, messages, *, temperature=0, max_tokens=None, response_format_json=False):
            requests.append({
                "url": self.policy.chat_url,
                "headers": self.policy.headers,
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            })
            return '{"duplicates":[{"current_index":0,"reason":"same_story"}]}'

    monkeypatch.setattr(post_card_news, "OpenClawGatewayClient", FakeGatewayClient)
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")
    monkeypatch.setenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789/v1")

    config = CardNewsQualityGateConfig(agent_dedupe_enabled=True, agent_dedupe_max_previous=3)
    result = asyncio.run(_agent_duplicate_indices([current], [_agent_context(previous)], config))

    assert result == {0}
    assert requests
    assert requests[0]["url"] == "http://127.0.0.1:18789/v1/chat/completions"
    assert requests[0]["headers"]["Authorization"] == "Bearer test-token"
    assert requests[0]["model"] == "openclaw/clawbridge"
    assert requests[0]["temperature"] == 0
    assert requests[0]["max_tokens"] == 400


def test_rank_agent_contexts_prefers_similar_story_over_recent_noise() -> None:
    current = _quality_card(1, url="https://mirror.example.com/agent-memory")
    similar = _agent_context(_quality_card(1, url="https://example.com/original-agent-memory"))
    noise = [
        _agent_context(
            {
                **_quality_card(idx, url=f"https://example.com/noise/{idx}", topic="검색/RAG/지식그래프"),
                "article_title": f"Graph cache latency benchmark {idx}",
                "claim": "그래프 캐시 지연시간과 검색 정확도 상충관계를 측정합니다.",
            }
        )
        for idx in range(2, 7)
    ]

    selected = _rank_agent_contexts_for_cards([current], [*noise, similar], limit=1)

    assert selected == [similar]


def test_quality_history_overlap_skips_and_new_cards_pass() -> None:
    cards = [_quality_card(idx) for idx in range(7)]
    config = CardNewsQualityGateConfig(
        audit_path=Path("unused"),
        min_publishable_cards=3,
        min_new_cards=3,
        max_previous_overlap_ratio=0.5,
        min_evidence_cards=2,
    )
    previous = {_card_identity_fingerprint(card) for card in cards[:5]}

    skipped = _evaluate_card_news_quality(cards, previous, config)

    assert skipped["decision"] == "skip"
    assert skipped["counts"]["repeated"] == 5
    assert skipped["counts"]["new"] == 2
    assert skipped["counts"]["overlap_ratio"] > 0.5
    assert "max_previous_overlap_ratio" in skipped["reason_codes"]
    assert "min_new_cards" in skipped["reason_codes"]

    passed = _evaluate_card_news_quality(cards[:4], set(), config)
    assert passed["decision"] == "publish"
    assert passed["reason_codes"] == []


def test_quality_substance_threshold_skips_skeletal_cards() -> None:
    cards = [
        {
            "article_title": f"Title only {idx}",
            "url": f"https://example.com/thin/{idx}",
            "primary_topic_display": "오픈소스/코드",
            "topic_confidence": 0.3,
        }
        for idx in range(3)
    ]
    config = CardNewsQualityGateConfig(audit_path=Path("unused"))

    result = _evaluate_card_news_quality(cards, set(), config)

    assert result["decision"] == "skip"
    assert result["counts"]["publishable"] == 0
    assert result["counts"]["evidence"] == 0
    assert "min_publishable_cards" in result["reason_codes"]
    assert "min_evidence_cards" in result["reason_codes"]


def test_quality_audit_jsonl_is_sanitized(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    secret_card = _quality_card(1, url="https://example.com/post?token=secret&utm_source=x&id=7")
    evaluation = _evaluate_card_news_quality([secret_card], set(), CardNewsQualityGateConfig(audit_path=audit_path))
    record = _build_card_news_audit_record(
        decision="skip",
        payload={"date": "2026-05-09"},
        source=tmp_path / "raw" / "newsletters" / "2026-05-09" / "items.json",
        cards=[secret_card],
        evaluation=evaluation,
    )

    _append_card_news_audit(audit_path, record)

    raw = audit_path.read_text(encoding="utf-8")
    stored = json.loads(raw)
    assert "token=secret" not in raw
    assert "utm_source" not in raw
    assert stored["source_ref"] == "2026-05-09"
    assert stored["cards"][0]["url"] == "https://example.com/post?id=7"
    assert stored["cards"][0]["identity_fingerprint"].startswith("url:")
    assert stored["cards"][0]["content_signature"]
    assert stored["cards"][0]["agent_context"]["title"] == str(secret_card["article_title"])
    assert all("Agent" not in token and "메모리" not in token for token in stored["cards"][0]["content_signature"])
    assert _load_recent_published_identities(audit_path, history_days=14) == set()


def test_quality_history_loader_uses_recent_publish_identities_only(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    now = datetime(2026, 5, 9, tzinfo=UTC)
    recent_identity = _card_identity_fingerprint(_quality_card(1))
    old_identity = _card_identity_fingerprint(_quality_card(2))
    skipped_identity = _card_identity_fingerprint(_quality_card(3))
    records = [
        {"not": "json"},
        {
            "decision": "publish",
            "timestamp": (now - timedelta(days=2)).isoformat(),
            "cards": [{"identity_fingerprint": recent_identity}],
        },
        {
            "decision": "publish",
            "timestamp": (now - timedelta(days=30)).isoformat(),
            "cards": [{"identity_fingerprint": old_identity}],
        },
        {
            "decision": "skip",
            "timestamp": now.isoformat(),
            "cards": [{"identity_fingerprint": skipped_identity}],
        },
    ]
    audit_path.write_text(
        "\n".join(["{malformed", *(json.dumps(record) for record in records)]) + "\n",
        encoding="utf-8",
    )

    identities = _load_recent_published_identities(audit_path, history_days=14, now=now)

    assert identities == {recent_identity}


def test_quality_history_loader_reads_recent_content_signatures(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    now = datetime(2026, 5, 9, tzinfo=UTC)
    recent_card = _quality_card(1)
    old_card = _quality_card(2)
    recent_signature = _content_signature_hashes(recent_card)
    old_signature = _content_signature_hashes(old_card)
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "decision": "publish",
                        "timestamp": (now - timedelta(days=1)).isoformat(),
                        "cards": [
                            {
                                "identity_fingerprint": _card_identity_fingerprint(recent_card),
                                "content_signature": recent_signature,
                            }
                        ],
                    }
                ),
                json.dumps(
                    {
                        "decision": "publish",
                        "timestamp": (now - timedelta(days=30)).isoformat(),
                        "cards": [
                            {
                                "identity_fingerprint": _card_identity_fingerprint(old_card),
                                "content_signature": old_signature,
                            }
                        ],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    identities, signatures = _load_recent_published_card_history(audit_path, history_days=14, now=now)

    assert identities == {_card_identity_fingerprint(recent_card)}
    assert signatures == [set(recent_signature)]


def test_quality_history_loader_accepts_legacy_identity_only_records(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    now = datetime(2026, 5, 9, tzinfo=UTC)
    legacy_card = _quality_card(1)
    signed_card = _quality_card(2)
    signed_signature = _content_signature_hashes(signed_card)
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "decision": "publish",
                        "timestamp": (now - timedelta(days=1)).isoformat(),
                        "cards": [{"identity_fingerprint": _card_identity_fingerprint(legacy_card)}],
                    }
                ),
                json.dumps(
                    {
                        "decision": "publish",
                        "timestamp": now.isoformat(),
                        "cards": [
                            {
                                "identity_fingerprint": _card_identity_fingerprint(signed_card),
                                "content_signature": signed_signature,
                            }
                        ],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    identities, signatures = _load_recent_published_card_history(audit_path, history_days=14, now=now)

    assert identities == {
        _card_identity_fingerprint(legacy_card),
        _card_identity_fingerprint(signed_card),
    }
    assert signatures == [set(signed_signature)]


def test_quality_history_loader_reads_agent_contexts(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    now = datetime(2026, 5, 9, tzinfo=UTC)
    recent_card = _quality_card(1)
    old_card = _quality_card(2)
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "decision": "publish",
                        "timestamp": (now - timedelta(days=1)).isoformat(),
                        "cards": [{"agent_context": _agent_context(recent_card)}],
                    }
                ),
                json.dumps(
                    {
                        "decision": "publish",
                        "timestamp": (now - timedelta(days=30)).isoformat(),
                        "cards": [{"agent_context": _agent_context(old_card)}],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    contexts = _load_recent_published_agent_contexts(audit_path, history_days=14, now=now)

    assert len(contexts) == 1
    assert contexts[0]["title"] == str(recent_card["article_title"])


def test_render_uses_same_selected_cards_that_gate_evaluates() -> None:
    payload = {"date": "2026-05-09", "items": [_quality_card(idx) for idx in range(4)]}
    cards = _select_cards([item for item in payload["items"] if isinstance(item, dict)], max_cards=3)
    evaluation = _evaluate_card_news_quality(cards, set(), CardNewsQualityGateConfig(audit_path=Path("unused")))
    rendered = render_card_news_messages(payload, max_cards=3)

    assert evaluation["counts"]["selected"] == 3
    for item in cards:
        assert str(item["article_title"]) in "\n".join(rendered)


def test_run_skips_before_discord_calls_and_writes_audit(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "items.json"
    source.write_text(json.dumps({"date": "2026-05-09", "items": [_quality_card(1)]}), encoding="utf-8")
    audit_path = tmp_path / "audit.jsonl"
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            calls.append("get")
            raise AssertionError("Discord GET should not run on quality-gate skip")

    monkeypatch.setattr(post_card_news.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_CARD_NEWS_CHANNEL_ID", "111111111111111111")
    monkeypatch.setenv("DISCORD_CARD_NEWS_SOURCE", str(source))
    monkeypatch.setenv("DISCORD_CARD_NEWS_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("DISCORD_CARD_NEWS_ENRICH_PUBLIC_URLS", "0")
    monkeypatch.setenv("DISCORD_CARD_NEWS_REPORT_SKIP_TO_OPS", "0")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_PUBLISHABLE_CARDS", "3")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_NEW_CARDS", "3")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_EVIDENCE_CARDS", "2")

    asyncio.run(run())

    assert calls == []
    out = capsys.readouterr().out
    assert "skipped card news quality_gate" in out
    record = json.loads(audit_path.read_text(encoding="utf-8"))
    assert record["decision"] == "skip"


def test_run_quality_gate_disable_bypasses_skip_and_audit(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "items.json"
    source.write_text(json.dumps({"date": "2026-05-09", "items": [_quality_card(1)]}), encoding="utf-8")
    audit_path = tmp_path / "audit.jsonl"
    requests: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *args, **kwargs):
            requests.append(f"GET {url}")
            return post_card_news.httpx.Response(
                200,
                json={"id": "111111111111111111", "type": 0},
                request=post_card_news.httpx.Request("GET", url),
            )

        async def post(self, url, *args, **kwargs):
            requests.append(f"POST {url}")
            return post_card_news.httpx.Response(200, json={"id": "m1"}, request=post_card_news.httpx.Request("POST", url))

    monkeypatch.setattr(post_card_news.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_CARD_NEWS_CHANNEL_ID", "111111111111111111")
    monkeypatch.setenv("DISCORD_CARD_NEWS_SOURCE", str(source))
    monkeypatch.setenv("DISCORD_CARD_NEWS_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("DISCORD_CARD_NEWS_ENRICH_PUBLIC_URLS", "0")
    monkeypatch.setenv("DISCORD_CARD_NEWS_QUALITY_GATE", "0")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_PUBLISHABLE_CARDS", "3")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_NEW_CARDS", "3")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_EVIDENCE_CARDS", "2")
    monkeypatch.setenv("DISCORD_PURGE_PREVIOUS_CARD_NEWS", "0")

    asyncio.run(run())

    out = capsys.readouterr().out
    assert "skipped card news quality_gate" not in out
    assert "posted card news to channel=111111111111111111" in out
    assert any(request.startswith("GET https://discord.com/api/v10/channels/111111111111111111") for request in requests)
    assert any(request.startswith("POST https://discord.com/api/v10/channels/111111111111111111/messages") for request in requests)
    assert not audit_path.exists()


def test_run_writes_publish_and_failure_audits(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "items.json"
    source.write_text(
        json.dumps({"date": "2026-05-09", "items": [_quality_card(idx) for idx in range(3)]}),
        encoding="utf-8",
    )
    audit_path = tmp_path / "audit.jsonl"
    requests: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *args, **kwargs):
            requests.append(f"GET {url}")
            if "/messages" in url:
                return post_card_news.httpx.Response(200, json=[], request=post_card_news.httpx.Request("GET", url))
            return post_card_news.httpx.Response(
                200,
                json={"id": "111111111111111111", "type": 0},
                request=post_card_news.httpx.Request("GET", url),
            )

        async def post(self, url, *args, **kwargs):
            requests.append(f"POST {url}")
            return post_card_news.httpx.Response(200, json={"id": "m1"}, request=post_card_news.httpx.Request("POST", url))

    monkeypatch.setattr(post_card_news.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_CARD_NEWS_CHANNEL_ID", "111111111111111111")
    monkeypatch.setenv("DISCORD_CARD_NEWS_SOURCE", str(source))
    monkeypatch.setenv("DISCORD_CARD_NEWS_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("DISCORD_CARD_NEWS_ENRICH_PUBLIC_URLS", "0")
    monkeypatch.setenv("DISCORD_PURGE_PREVIOUS_CARD_NEWS", "0")

    asyncio.run(run())

    assert any(request.startswith("GET https://discord.com/api/v10/channels/111111111111111111") for request in requests)
    assert any(request.startswith("POST https://discord.com/api/v10/channels/111111111111111111/messages") for request in requests)
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["decision"] == "publish"
    assert records[-1]["publish"]["message_count"] == 4

    class FailingClient(FakeClient):
        async def post(self, url, *args, **kwargs):
            requests.append(f"POST_FAIL {url}")
            return post_card_news.httpx.Response(
                500,
                json={"message": "boom"},
                request=post_card_news.httpx.Request("POST", url),
            )

    monkeypatch.setattr(post_card_news.httpx, "AsyncClient", FailingClient)
    monkeypatch.setenv("DISCORD_CARD_NEWS_MIN_NEW_CARDS", "0")
    monkeypatch.setenv("DISCORD_CARD_NEWS_MAX_PREVIOUS_OVERLAP_RATIO", "1.0")

    try:
        asyncio.run(run())
    except post_card_news.httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected post failure")

    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["decision"] == "failure"
    assert records[-1]["failure"]["stage"] == "post_messages"
