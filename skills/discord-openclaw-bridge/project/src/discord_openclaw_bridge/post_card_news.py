from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .post_newsletter import (
    DISCORD_SUPPRESS_EMBEDS_FLAG,
    NewsletterPostConfigError,
    _delete_message_with_rate_limit,
    _load_dotenv,
    _post_message_with_rate_limit,
    _required_snowflake,
)

DEFAULT_CARD_NEWS_CHANNEL_ID = "1501073491921993758"
CARD_NEWS_TITLE = "집현전-Claw 카드뉴스"
FORUM_CHANNEL_TYPES = {15}
CARD_NEWS_THREAD_NAME_MARKERS = (
    "기술 브리핑 카드뉴스",
    "블로그 포스팅 워크플로우 카드뉴스",
)

CARD_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"
GENERIC_TOPIC = "기타 테크 리포트"
LEAN_DISCLAIMER_WITH_EXCERPT = "공개 본문만으로는 추가 근거를 확인하지 못했습니다."
LEAN_DISCLAIMER_WITHOUT_EXCERPT = "현재 카드에는 원문 발췌가 포함되지 않았습니다."
CONNECTIVES = ("따라서", "다만", "구체적으로", "한편")
TOPIC_PRIORITY: dict[str, int] = {
    "검색/RAG/지식그래프": 10,
    "LLM/에이전트": 20,
    "멀티모달/비전": 30,
    "인프라/배포": 40,
    "오픈소스/코드": 50,
    "AI 안전/평가": 60,
    "산업/제품 동향": 70,
    "논문/리서치": 80,
    GENERIC_TOPIC: 900,
}
_REGISTER_PAIRS = (
    # Order matters: longer / more specific endings first so they win the lookahead match.
    ("되었다", "되었습니다"),
    ("하였다", "하였습니다"),
    ("했다", "했습니다"),
    ("였다", "였습니다"),
    ("많았다", "많았습니다"),
    ("좋았다", "좋았습니다"),
    ("작았다", "작았습니다"),
    ("높았다", "높았습니다"),
    ("낮았다", "낮았습니다"),
    ("컸다", "컸습니다"),
    ("된다", "됩니다"),
    ("한다", "합니다"),
    ("하다", "합니다"),
    ("는다", "습니다"),
    ("이다", "입니다"),
    ("있다", "있습니다"),
    ("없다", "없습니다"),
    ("많다", "많습니다"),
    ("좋다", "좋습니다"),
    ("작다", "작습니다"),
    ("높다", "높습니다"),
    ("낮다", "낮습니다"),
    ("크다", "큽니다"),
)
_ACTION_SIGNAL_TERMS = (
    "원문",
    "확인",
    "검증",
    "검토",
    "도입",
    "운영",
    "평가",
    "재현",
    "한계",
    "주의",
    "리스크",
    "트레이드오프",
    "trade-off",
    "trade off",
)
_INTERROGATIVE_SUFFIXES = (
    "인가",
    "는가",
    "할 수 있는가",
    "해야 하는가",
    "가능한가",
)
_QUESTION_TRANSFORMS = (
    (re.compile(r"^(.+?)을 함께 제시합니다\.?$"), r"\1이 같은 조건에서 비교 가능한가?"),
    (re.compile(r"^(.+?)를 함께 제시합니다\.?$"), r"\1가 같은 조건에서 비교 가능한가?"),
    (re.compile(r"^(.+?)을 함께 측정합니다\.?$"), r"\1이 운영 환경에서도 유지되는가?"),
    (re.compile(r"^(.+?)를 함께 측정합니다\.?$"), r"\1가 운영 환경에서도 유지되는가?"),
    (re.compile(r"^(.+?)을 측정합니다\.?$"), r"\1이 운영 환경에서도 유지되는가?"),
    (re.compile(r"^(.+?)를 측정합니다\.?$"), r"\1가 운영 환경에서도 유지되는가?"),
)


def _clean(value: object, *, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split()).strip()
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _strip_emoji(value: str) -> str:
    return "".join(
        char
        for char in value
        if not (
            "\U0001F000" <= char <= "\U0001FAFF"
            or "☀" <= char <= "➿"
        )
    ).strip()


def _clean_title(value: object, *, limit: int | None = None) -> str:
    return _clean(_strip_emoji(str(value or "")), limit=limit)


def _clean_multiline(value: object) -> str:
    lines = [_clean(line) for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _latest_archive_path() -> Path:
    root = Path(os.environ.get("NEWSLETTER_WIKI_ROOT", str(Path.home() / ".openclaw" / "workspace" / "wiki"))).expanduser()
    raw_root = root / "raw" / "newsletters"
    today_path = raw_root / date.today().isoformat() / "items.json"
    if today_path.exists():
        return today_path
    candidates = sorted(raw_root.glob("*/items.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    raise NewsletterPostConfigError(f"newsletter raw archive not found under {raw_root}")


def _load_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise NewsletterPostConfigError(f"card news archive source not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise NewsletterPostConfigError(f"invalid newsletter archive payload: {path}")
    return payload


def _summary_lines(item: dict[str, Any]) -> list[str]:
    raw = item.get("summary_lines") or item.get("summaryLines") or []
    lines: list[str] = []
    if isinstance(raw, list):
        for line in raw:
            text = _clean(line, limit=160)
            if text and text not in lines:
                lines.append(text)
            if len(lines) == 3:
                break
    return lines[:3]


def _source_name(item: dict[str, Any]) -> str:
    return _clean(
        item.get("source_name")
        or item.get("sender_name")
        or item.get("newsletter_name")
        or item.get("sender")
        or "원문",
        limit=80,
    )


def _title(item: dict[str, Any]) -> str:
    return _clean_title(item.get("article_title") or item.get("title") or "Untitled", limit=90)


def _raw_title(item: dict[str, Any]) -> str:
    return _clean_title(item.get("article_title") or item.get("title") or "Untitled")


_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_JONGSEONG_NIEUN = 4  # ㄴ
_JONGSEONG_SSANG_SIOT = 20  # ㅆ (past tense marker like 갔/했/었)
_JONGSEONG_BIEUP = 17  # ㅂ
_DA_PATTERN = re.compile(r"([가-힣])다(?=[.!?]|$)")


def _convert_da_ending(match: re.Match[str]) -> str:
    char = match.group(1)
    code = ord(char) - _HANGUL_BASE
    final = code % 28
    if final == _JONGSEONG_NIEUN:
        # (vowel-stem)ㄴ다 → (stem with ㅂ final)니다 (e.g. 인 → 입, 룬 → 룹)
        swapped = chr(_HANGUL_BASE + code - _JONGSEONG_NIEUN + _JONGSEONG_BIEUP)
        return f"{swapped}니다"
    if final == _JONGSEONG_SSANG_SIOT:
        # past tense (...)ㅆ다 → (...)ㅆ습니다 (e.g. 났 → 났습니다, 갔 → 갔습니다)
        return f"{char}습니다"
    return match.group(0)


def _normalize_register(text: str) -> str:
    if not text:
        return text
    for src, dst in _REGISTER_PAIRS:
        text = re.sub(rf"{src}(?=[.!?]|$)", dst, text)
    text = _DA_PATTERN.sub(_convert_da_ending, text)
    return text


def _confidence_bucket(value: object) -> str:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "잠정"
    if v >= 0.7:
        return "높음"
    if v >= 0.5:
        return "보통"
    return "잠정"


def _format_footer(
    source: str,
    url: str,
    topic: str,
    bucket: str,
    *,
    reasons: list[str] | None = None,
) -> str:
    parts = [f"— {source}", f"<{url}>"]
    if topic and topic != GENERIC_TOPIC:
        parts.append(f"`{topic}`")
    parts.append(f"`{bucket}`")
    base = " · ".join(parts)
    if reasons:
        cleaned = [r for r in (_clean(item, limit=40) for item in reasons[:2]) if r]
        if cleaned:
            label = f"단서 {cleaned[0]}"
            tail = " · ".join([label, *cleaned[1:]])
            base = f"{base} · {tail}"
    return base


def _has_substring_overlap(prev: str, new: str, *, min_len: int = 30) -> bool:
    if len(new) < min_len or len(prev) < min_len:
        return False
    for i in range(len(new) - min_len + 1):
        if new[i:i + min_len] in prev:
            return True
    return False


def _dedup_paragraphs(paragraphs: list[str]) -> list[str]:
    kept: list[str] = []
    for para in paragraphs:
        if any(_has_substring_overlap(prev, para) for prev in kept):
            continue
        kept.append(para)
    return kept


def _richness(item: dict[str, Any], *, raw_title: str) -> str:
    if (
        _clean(item.get("hook") or item.get("why_now"))
        or _clean(item.get("core_change") or item.get("claim") or item.get("thesis"))
        or _clean(item.get("context") or item.get("mechanism") or item.get("claim_mechanism"))
        or _clean(item.get("why_matters"))
        or _clean(item.get("evidence"))
        or _clean(item.get("cta") or item.get("save_point"))
        or _summary_lines(item)
    ):
        return "rich"
    excerpt = _clean(item.get("public_excerpt") or item.get("article_description"))
    if excerpt and _clean_title(excerpt).lower() != raw_title.lower():
        return "lean"
    return "skeletal"


def _topic_groups(items: list[dict[str, Any]]) -> OrderedDict[str, list[dict[str, Any]]]:
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in items:
        topic = _clean(item.get("primary_topic_display") or GENERIC_TOPIC)
        groups.setdefault(topic, []).append(item)
    return OrderedDict(
        sorted(groups.items(), key=lambda pair: (TOPIC_PRIORITY.get(pair[0], 800), -len(pair[1]), pair[0]))
    )


def _select_cards(items: list[dict[str, Any]], *, max_cards: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for _topic, topic_items in _topic_groups(items).items():
        for item in topic_items:
            title = _clean(item.get("article_title") or item.get("title"), limit=140).lower()
            url = _clean(item.get("url"))
            key = title or url
            if not url or not key or key in seen_titles:
                continue
            seen_titles.add(key)
            selected.append(item)
            break
        if len(selected) >= max_cards:
            return selected
    if len(selected) < max_cards:
        for item in items:
            title = _clean(item.get("article_title") or item.get("title"), limit=140).lower()
            url = _clean(item.get("url"))
            key = title or url
            if not url or not key or key in seen_titles:
                continue
            seen_titles.add(key)
            selected.append(item)
            if len(selected) >= max_cards:
                break
    return selected


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?。])\s+", text.strip(), maxsplit=1)
    return parts[0].strip() if parts else text.strip()


def _distinct_topics_in_order(cards: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for item in cards:
        topic = _clean(item.get("primary_topic_display") or GENERIC_TOPIC)
        if topic and topic not in seen:
            seen.append(topic)
    return sorted(seen, key=lambda t: TOPIC_PRIORITY.get(t, 800))


def _theme_sentence(cards: list[dict[str, Any]]) -> str:
    distinct = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    if len(distinct) >= 2:
        return f"{distinct[0]}와 {distinct[1]}가 오늘의 축입니다."
    for item in cards:
        why_now = _clean(item.get("why_now"), limit=240)
        if why_now:
            return _normalize_register(_first_sentence(why_now))
    for item in cards:
        summary = _summary_lines(item)
        if summary:
            return _normalize_register(summary[0])
    return "오늘은 본문 근거가 얇은 읽기 후보 중심입니다."


def _hero_image_description(topics: list[str]) -> str:
    topic_text = " · ".join(topics[:3]) if topics else "기술 브리핑"
    return (
        f"{topic_text} 변화가 연구 현장, 제품 조직, 운영 대시보드로 이어지는 장면을 "
        "추상적으로 표현한 대표 이미지. 읽을 수 있는 텍스트·로고·실존 인물 초상은 제외."
    )


def _three_line_summary(cards: list[dict[str, Any]], theme: str) -> list[str]:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    first_title = _title(cards[0]) if cards else "공개 기술 후보"
    topic_text = "·".join(topics[:2]) if topics else "기술 후보"
    return [
        _clean(theme, limit=120),
        f"{first_title} 등 {len(cards)}개 공개 출처를 {topic_text} 변화 축으로 묶었습니다.",
        "각 카드는 원문 링크에서 확인 가능한 근거만 남기고, 메일 본문·토큰·비밀값은 제외합니다.",
    ]


def _article_thesis(cards: list[dict[str, Any]], theme: str) -> str:
    for item in cards:
        claim = _normalize_register(
            _clean(item.get("core_change") or item.get("claim") or item.get("thesis"), limit=180)
        )
        if claim:
            return f"{claim} 이 흐름은 단일 링크 묶음보다 문제의식-근거-현장 질문으로 읽어야 합니다."
    return f"{theme} 단순 링크 나열보다 토픽 간 연결과 검증 질문을 함께 읽어야 합니다."


def _argument_structure(cards: list[dict[str, Any]], theme: str) -> list[str]:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    topic_text = ", ".join(topics[:3]) if topics else "여러 기술 후보"
    evidence_count = sum(1 for item in cards if _clean(item.get("evidence") or item.get("why_matters")))
    return [
        f"관찰: {theme}",
        f"메커니즘: {topic_text}에서 공개 원문·요약·토픽 단서가 같은 변화 방향을 가리키는지 본다.",
        f"긴장: 연구 성능, 운영 비용, 제품 적용 조건이 같은 속도로 움직이지 않을 수 있다.",
        f"반론: 근거가 얇은 후보는 홍보 문구나 제목 신호일 수 있어 원문 조건 확인이 필요하다.",
        f"판단: 현재 렌더링은 {len(cards)}개 후보 중 근거 문장 {evidence_count}개를 우선 노출해 후속 검토를 좁힌다.",
    ]


def _industry_interpretation(cards: list[dict[str, Any]]) -> str:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    if not topics:
        return "현장에서는 후보 링크를 바로 채택하기보다 공개 근거와 재현 조건을 먼저 확인해야 합니다."
    return (
        f"{topics[0]} 흐름은 모델 성능만의 문제가 아니라 데이터 흐름, 평가 지표, 운영 조직의 "
        "책임 배분을 함께 바꾸는 신호입니다."
    )


def _future_questions(cards: list[dict[str, Any]]) -> list[str]:
    questions: list[str] = []
    for item in cards:
        for seed in [*_summary_lines(item), _clean(item.get("why_now")), _clean(item.get("evidence"))]:
            qtext = _question_or_transform(_normalize_register(seed))
            if qtext and qtext not in questions:
                questions.append(qtext)
            if len(questions) >= 2:
                break
        if len(questions) >= 2:
            break
    while len(questions) < 2:
        questions.append("원문이 제시한 평가 조건과 실제 운영 환경의 제약은 어디에서 달라지는가?")
    return questions[:2]


def _render_article_header(cards: list[dict[str, Any]], *, run_date: str, item_count: int) -> str:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    theme = _theme_sentence(cards)
    summary = _three_line_summary(cards, theme)
    argument = _argument_structure(cards, theme)
    questions = _future_questions(cards)
    topic_text = " · ".join(topics[:3]) if topics else "공개 기술 후보"
    lines = [
        f"**{CARD_NEWS_TITLE} — 기술 블로그 브리핑 — {run_date}**",
        "",
        f"대표 이미지(설명): {_hero_image_description(topics)}",
        "",
        "> 3줄 요약",
        f"> 1. {summary[0]}",
        f"> 2. {summary[1]}",
        f"> 3. {summary[2]}",
        "",
        "## 왜 지금 이 이슈인가",
        f"{topic_text}에서 나온 공개 링크를 한 번에 소비하기보다, 지금 어떤 문제가 반복되는지 읽어야 합니다.",
        "",
        "## 핵심 주장",
        f"- 주장: {_article_thesis(cards, theme)}",
        f"- 근거: 선별 {len(cards)}건 / 수집 {item_count}건의 공개 URL과 토픽 근거만 사용했습니다.",
        "",
        "## 논증 구조",
    ]
    lines.extend(f"{idx}. {line}" for idx, line in enumerate(argument, start=1))
    lines += [
        "",
        "## 산업사회학적·현장기반 해석",
        _industry_interpretation(cards),
        "",
        "## 앞으로 볼 질문",
        f"- {questions[0]}",
        f"- {questions[1]}",
        "",
        "## 카드뉴스·Discord 재사용안",
        "아래 메시지들은 이 글의 섹션을 훅-맥락-핵심 변화-근거-CTA 카드로 쪼갠 재사용 블록입니다.",
        "",
        "## 출처",
        "각 카드 하단의 공개 URL만 사용합니다.",
    ]
    return "\n".join(lines)


def _is_interrogative(text: str) -> bool:
    if not text:
        return False
    if text.endswith("?"):
        return True
    stripped = text.rstrip(".!?。")
    return any(stripped.endswith(suffix) for suffix in _INTERROGATIVE_SUFFIXES)


def _question_or_transform(seed: str) -> str | None:
    if not seed:
        return None
    if _is_interrogative(seed):
        if seed.endswith("?"):
            return seed
        return seed.rstrip(".!?。") + "?"
    for pattern, replacement in _QUESTION_TRANSFORMS:
        if pattern.match(seed):
            return pattern.sub(replacement, seed)
    return None


def _has_action_signal(text: str) -> bool:
    return any(term in text for term in _ACTION_SIGNAL_TERMS)


def _maybe_implication(seed: str, *, has_claim: bool, has_evidence: bool) -> str | None:
    if not seed or not _has_action_signal(seed):
        return None
    if has_evidence and not has_claim:
        return f"다만 {seed}"
    return f"따라서 {seed}"


def _format_claim_mechanism(claim: str, mechanism: str) -> str:
    """Combine claim and mechanism in a single paragraph (frame-1 style).

    Always bridges with the bare adverb ``구체적으로`` (no 는). Preserving the
    topic marker on the connector creates a ``구체적으로는 X은/는`` collision
    whenever the mechanism's subject also carries 은/는 — which can land on any
    of the first few tokens, not just the first word. Dropping 는 here reads
    equally well in Korean prose and eliminates the entire collision class.
    """
    if claim and mechanism:
        return f"{claim} 구체적으로 {mechanism}"
    if claim:
        return claim
    if mechanism:
        return f"구체적으로 {mechanism}"
    return ""


def _build_frame1_paragraphs(
    *,
    why_now: str,
    summary: list[str],
    claim: str,
    mechanism: str,
    evidence: str,
    source: str,
) -> list[str]:
    paragraphs: list[str] = []

    if why_now:
        paragraphs.append(why_now)
    elif summary:
        paragraphs.append(_normalize_register(summary[0]))

    combined = _format_claim_mechanism(claim, mechanism)
    if combined:
        paragraphs.append(combined)

    if evidence:
        paragraphs.append(f"> {source}: {evidence}")

    if len(summary) >= 2:
        seed = _normalize_register(summary[1])
        implication = _maybe_implication(seed, has_claim=bool(claim), has_evidence=bool(evidence))
        if implication:
            paragraphs.append(implication)

    return paragraphs


def _build_frame2_paragraphs(
    *,
    why_now: str,
    summary: list[str],
    claim: str,
    mechanism: str,
    evidence: str,
) -> list[str]:
    paragraphs: list[str] = []

    if claim:
        paragraphs.append(claim)
    elif why_now:
        paragraphs.append(why_now)
    elif summary:
        paragraphs.append(_normalize_register(summary[0]))

    if evidence:
        paragraphs.append(f"다만 {evidence}")

    if mechanism:
        paragraphs.append(f"구체적으로 {mechanism}")

    seeds: list[str] = []
    if summary:
        seeds.append(_normalize_register(summary[-1]))
    if why_now and why_now != claim:
        seeds.append(why_now)
    if summary and len(summary) >= 1:
        seeds.append(_normalize_register(summary[0]))
    for candidate in seeds:
        qtext = _question_or_transform(candidate)
        if qtext and not any(_has_substring_overlap(p, qtext) for p in paragraphs):
            paragraphs.append(qtext)
            break

    return paragraphs


def _render_rich_card(
    item: dict[str, Any],
    *,
    title: str,
    topic: str,
    source: str,
    url: str,
    bucket: str,
    reasons: list[str] | None = None,
    frame: int = 1,
) -> str:
    summary = _summary_lines(item)
    why_now = _normalize_register(_clean(item.get("hook") or item.get("why_now"), limit=240))
    claim = _normalize_register(_clean(item.get("core_change") or item.get("claim") or item.get("thesis"), limit=220))
    mechanism = _normalize_register(_clean(item.get("context") or item.get("mechanism") or item.get("claim_mechanism"), limit=220))
    evidence = _normalize_register(_clean(item.get("why_matters") or item.get("evidence"), limit=220))
    cta = _normalize_register(_clean(item.get("cta") or item.get("save_point"), limit=180))

    if frame == 2:
        paragraphs = _build_frame2_paragraphs(
            why_now=why_now,
            summary=summary,
            claim=claim,
            mechanism=mechanism,
            evidence=evidence,
        )
        emit_next_question_label = False
    else:
        paragraphs = _build_frame1_paragraphs(
            why_now=why_now,
            summary=summary,
            claim=claim,
            mechanism=mechanism,
            evidence=evidence,
            source=source,
        )
        emit_next_question_label = True

    if cta and not any(_has_substring_overlap(p, cta) for p in paragraphs):
        paragraphs.append(cta)

    paragraphs = _dedup_paragraphs(paragraphs)

    next_question_text: str | None = None
    if emit_next_question_label and summary:
        seed = _normalize_register(summary[-1])
        qtext = _question_or_transform(seed)
        if qtext and not any(_has_substring_overlap(p, qtext) for p in paragraphs):
            next_question_text = qtext

    body_parts: list[str] = [
        CARD_SEPARATOR,
        f"**{title}**",
        "",
    ]
    for para in paragraphs:
        body_parts.append(para)
        body_parts.append("")
    if next_question_text:
        body_parts.append("**다음 질문**")
        body_parts.append(next_question_text)
        body_parts.append("")
    body_parts.append(_format_footer(source, url, topic, bucket, reasons=reasons))
    return "\n".join(body_parts)


def _render_lean_card(
    item: dict[str, Any],
    *,
    title: str,
    topic: str,
    source: str,
    url: str,
    bucket: str,
    reasons: list[str] | None = None,
) -> str:
    excerpt = _normalize_register(
        _clean_title(item.get("public_excerpt") or item.get("article_description"), limit=320)
    )
    disclaimer = LEAN_DISCLAIMER_WITH_EXCERPT if excerpt else LEAN_DISCLAIMER_WITHOUT_EXCERPT
    body_parts = [
        CARD_SEPARATOR,
        f"**{title}**",
        "",
    ]
    if excerpt:
        body_parts.extend([excerpt, ""])
    body_parts.extend(
        [
            disclaimer,
            "",
            _format_footer(source, url, topic, bucket, reasons=reasons),
        ]
    )
    return "\n".join(body_parts)


def _render_skeletal_card(
    *,
    title: str,
    topic: str,
    url: str,
    bucket: str,
) -> str:
    follow_up_topic = topic if topic and topic != GENERIC_TOPIC else "기술"
    body_parts = [
        CARD_SEPARATOR,
        f"**{title}**",
        f"{follow_up_topic} 영역의 후속 읽기 후보입니다.",
        f"<{url}> · `{bucket}`",
    ]
    return "\n".join(body_parts)




def _first_nonempty(values: list[str], *, fallback: str = "") -> str:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return fallback


def _publication_summary_lines(cards: list[dict[str, Any]]) -> list[str]:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    summary: list[str] = []
    if topics:
        summary.append(f"오늘 브리핑은 {', '.join(topics[:3])} 흐름을 하나의 기술 변화로 묶어 읽습니다.")
    else:
        summary.append("오늘 브리핑은 공개 근거가 확인된 기술 읽기 후보를 선별해 점검합니다.")

    claim = ""
    for item in cards:
        claim = _first_nonempty([
            str(item.get("core_change") or ""),
            str(item.get("claim") or ""),
            str(item.get("thesis") or ""),
            *(_summary_lines(item)[:1]),
        ])
        if claim:
            break
    summary.append(_normalize_register(claim) if claim else "개별 링크보다 문제의식과 근거 수준을 먼저 분리해 읽어야 합니다.")

    question = ""
    for item in cards:
        lines = _summary_lines(item)
        if lines:
            question = _question_or_transform(_normalize_register(lines[-1])) or ""
        if question:
            break
    summary.append(question or "다음 실행에서는 원문 근거와 운영 적용 조건을 함께 확인해야 합니다.")
    return [_clean(line, limit=180) for line in summary[:3]]


def _publication_header(cards: list[dict[str, Any]], *, run_date: str, total_count: int) -> str:
    summary = _publication_summary_lines(cards)
    theme = _theme_sentence(cards)
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    topic_text = ", ".join(topics[:3]) if topics else "기술 브리핑 후보"
    first_title = _title(cards[0]) if cards else "수집 결과 없음"
    core_claim = summary[1]
    future_question = summary[2].rstrip(".")
    if not future_question.endswith("?"):
        future_question = f"{future_question}?"
    lines = [
        f"**집현전-Claw 기술 블로그 브리핑 — {run_date}**",
        f"대표 이미지 설명: {topic_text}를 데이터 흐름과 현장 의사결정 보드로 은유한 추상 일러스트",
        "",
        "**3줄 요약**",
        f"1. {summary[0]}",
        f"2. {summary[1]}",
        f"3. {summary[2]}",
        "",
        "**왜 지금인가**",
        theme,
        "",
        "**핵심 주장**",
        core_claim,
        "",
        "**논증 구조**",
        f"관찰: {topic_text} 신호가 공개 원문에서 반복됩니다. 메커니즘: 수집된 근거는 방법·평가·적용 조건의 차이를 드러냅니다. 긴장: 도입자는 정확도, 비용, 지연시간, 검증 가능성을 함께 부담합니다. 판단: 출처가 확인된 항목부터 좁게 읽어야 합니다.",
        "",
        "**산업/현장 해석**",
        f"{first_title} 같은 항목은 개인 영웅담보다 조직의 도구 선택, 평가 체계, 운영 비용 배분 문제로 읽어야 합니다.",
        "",
        "**앞으로 볼 질문**",
        future_question,
        "",
        "**카드뉴스·Discord 재사용안**",
        f"아래 {len(cards)}개 카드는 이 글의 주장과 근거를 Discord에서 재사용할 수 있게 나눈 섹션입니다.",
        "",
        f"선별 {len(cards)}건 / 수집 {total_count}건",
    ]
    return "\n".join(lines)

def render_card_news_messages(payload: dict[str, Any], *, max_cards: int = 8) -> list[str]:
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    cards = _select_cards(items, max_cards=max_cards)
    run_date = _clean(payload.get("date") or date.today().isoformat())

    header = _render_article_header(cards, run_date=run_date, item_count=len(items))
    messages = [header]
    topic_index: dict[str, int] = {}

    for item in cards:
        raw_title = _raw_title(item)
        title = _title(item)
        topic = _clean(item.get("primary_topic_display") or GENERIC_TOPIC, limit=60)
        source = _source_name(item)
        url = _clean(item.get("url"))
        bucket = _confidence_bucket(item.get("topic_confidence"))
        raw_reasons = item.get("topic_reasons") or []
        reasons: list[str] = []
        if isinstance(raw_reasons, list):
            for raw in raw_reasons:
                cleaned = _clean(raw, limit=40)
                if cleaned and cleaned not in reasons:
                    reasons.append(cleaned)
                if len(reasons) >= 2:
                    break
        kind = _richness(item, raw_title=raw_title)
        if kind == "rich":
            idx = topic_index.get(topic, 0)
            topic_index[topic] = idx + 1
            frame = 2 if idx % 2 == 1 else 1
            messages.append(
                _render_rich_card(
                    item,
                    title=title,
                    topic=topic,
                    source=source,
                    url=url,
                    bucket=bucket,
                    reasons=reasons,
                    frame=frame,
                )
            )
        elif kind == "lean":
            messages.append(
                _render_lean_card(
                    item,
                    title=title,
                    topic=topic,
                    source=source,
                    url=url,
                    bucket=bucket,
                    reasons=reasons,
                )
            )
        else:
            messages.append(
                _render_skeletal_card(title=title, topic=topic, url=url, bucket=bucket)
            )
    return messages


def _is_card_news_bot_message(message: dict[str, object]) -> bool:
    content = str(message.get("content") or "")
    author = message.get("author")
    author_is_bot = isinstance(author, dict) and bool(author.get("bot"))
    return author_is_bot and CARD_NEWS_TITLE in content


async def _purge_previous_card_news_messages(
    client: httpx.AsyncClient,
    messages_url: str,
    *,
    headers: dict[str, str],
    limit: int = 50,
) -> int:
    response = await client.get(f"{messages_url}?limit={limit}", headers=headers)
    response.raise_for_status()
    deleted = 0
    for message in response.json():
        if not isinstance(message, dict) or not _is_card_news_bot_message(message):
            continue
        message_id = str(message.get("id") or "")
        if not message_id:
            continue
        await _delete_message_with_rate_limit(client, f"{messages_url}/{message_id}", headers=headers)
        deleted += 1
    return deleted


async def _delete_channel_with_rate_limit(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
) -> None:
    while True:
        response = await client.delete(url, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return
        retry_after = float(response.json().get("retry_after", 1.0))
        await asyncio.sleep(retry_after)


async def _purge_previous_card_news_threads(
    client: httpx.AsyncClient,
    active_threads_url: str,
    *,
    headers: dict[str, str],
) -> int:
    response = await client.get(active_threads_url, headers=headers)
    response.raise_for_status()
    purged = 0
    threads = response.json().get("threads", [])
    if not isinstance(threads, list):
        return 0
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        name = str(thread.get("name") or "")
        thread_id = str(thread.get("id") or "")
        if not thread_id or not any(marker in name for marker in CARD_NEWS_THREAD_NAME_MARKERS):
            continue
        try:
            await _delete_channel_with_rate_limit(
                client,
                f"https://discord.com/api/v10/channels/{thread_id}",
                headers=headers,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {403, 404}:
                raise
            continue
        else:
            purged += 1
    return purged


async def _create_forum_card_news_thread(
    client: httpx.AsyncClient,
    forum_url: str,
    *,
    headers: dict[str, str],
    name: str,
    content: str,
    hero_image_path: Path | None = None,
) -> str:
    payload: dict[str, Any] = {
        "name": _clean(name, limit=90),
        "auto_archive_duration": 1440,
        "message": {
            "content": content,
            "allowed_mentions": {"parse": []},
            "flags": DISCORD_SUPPRESS_EMBEDS_FLAG,
        },
    }
    if hero_image_path and hero_image_path.exists():
        message_payload = payload["message"]
        if isinstance(message_payload, dict):
            message_payload["attachments"] = [{"id": 0, "filename": hero_image_path.name}]
        response = await client.post(
            f"{forum_url}/threads",
            headers=headers,
            data={"payload_json": json.dumps(payload, ensure_ascii=False)},
            files={"files[0]": (hero_image_path.name, hero_image_path.read_bytes(), "image/png")},
        )
    else:
        response = await client.post(f"{forum_url}/threads", headers=headers, json=payload)
    response.raise_for_status()
    thread_id = str(response.json().get("id") or "")
    if not thread_id:
        raise NewsletterPostConfigError("Discord forum thread creation returned no thread id")
    return thread_id


async def run() -> None:
    _load_dotenv(Path.cwd() / ".env")
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise NewsletterPostConfigError("missing required env var: DISCORD_BOT_TOKEN")
    channel_raw = os.environ.get("DISCORD_CARD_NEWS_CHANNEL_ID", DEFAULT_CARD_NEWS_CHANNEL_ID).strip()
    os.environ["DISCORD_CARD_NEWS_CHANNEL_ID"] = channel_raw
    channel_id = _required_snowflake("DISCORD_CARD_NEWS_CHANNEL_ID")
    source = Path(os.environ.get("DISCORD_CARD_NEWS_SOURCE", str(_latest_archive_path()))).expanduser()
    max_cards = int(os.environ.get("DISCORD_CARD_NEWS_MAX_CARDS", "8"))
    hero_image_raw = os.environ.get("DISCORD_CARD_NEWS_HERO_IMAGE_PATH", "").strip()
    hero_image_path = Path(hero_image_raw).expanduser() if hero_image_raw else None
    purge_previous = os.environ.get("DISCORD_PURGE_PREVIOUS_CARD_NEWS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    payload = _load_archive(source)
    messages = render_card_news_messages(payload, max_cards=max_cards)
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        channel_response = await client.get(f"https://discord.com/api/v10/channels/{channel_id}", headers=headers)
        channel_response.raise_for_status()
        channel_data = channel_response.json()
        channel_type = int(channel_data.get("type", 0))
        guild_id = str(channel_data.get("guild_id") or "")
        purged = 0
        target_channel_id = channel_id
        thread_id = ""
        if channel_type in FORUM_CHANNEL_TYPES:
            forum_url = f"https://discord.com/api/v10/channels/{channel_id}"
            if purge_previous and guild_id:
                purged = await _purge_previous_card_news_threads(
                    client,
                    f"https://discord.com/api/v10/guilds/{guild_id}/threads/active",
                    headers=headers,
                )
            thread_id = await _create_forum_card_news_thread(
                client,
                forum_url,
                headers=headers,
                name=f"{_clean(payload.get('date') or date.today().isoformat())} 기술 브리핑 카드뉴스",
                content=messages[0],
                hero_image_path=hero_image_path,
            )
            target_channel_id = int(thread_id)
            messages_to_post = messages[1:]
        else:
            url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            if purge_previous:
                purged = await _purge_previous_card_news_messages(client, url, headers=headers)
            messages_to_post = messages
        post_url = f"https://discord.com/api/v10/channels/{target_channel_id}/messages"
        for message in messages_to_post:
            await _post_message_with_rate_limit(
                client,
                post_url,
                headers=headers,
                content=message,
                suppress_embeds=True,
            )
    thread_note = f" thread={thread_id}" if thread_id else ""
    print(f"posted card news to channel={channel_id}{thread_note} source={source} messages={len(messages)} purged={purged}")


def main() -> None:
    try:
        asyncio.run(run())
    except (NewsletterPostConfigError, httpx.HTTPError) as exc:
        print(f"card news post failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
