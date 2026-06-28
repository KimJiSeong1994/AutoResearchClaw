#!/usr/bin/env bash
# Generate the daily Jiphyeonjeon briefing artifact and post it to Discord.
#
# Kept as a committed runner so cron survives workspace cleanup/deploy cycles.
# This runner intentionally uses the daily newsletter archive briefing, not the
# weekly research-trends report, so the Discord "daily briefing" changes with
# the KST newsletter archive date.
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export TZ="${TZ:-Asia/Seoul}"

WORKSPACE="${HERMES_WORKSPACE:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
BRIDGE_PROJECT="$WORKSPACE/skills/discord-openclaw-bridge/project"
PAPER_SKILL="$WORKSPACE/skills/paper-recommender"
LOG_DIR="$WORKSPACE/logs"
LOG_FILE="$LOG_DIR/daily-jiphyeonjeon-briefing.log"
LOCK_DIR="${DAILY_BRIEFING_LOCK_DIR:-$WORKSPACE/.locks/daily-jiphyeonjeon-briefing.lock}"

mkdir -p "$LOG_DIR" "$(dirname "$LOCK_DIR")"
exec >>"$LOG_FILE" 2>&1

printf "\n[%s] daily jiphyeonjeon briefing start\n" "$(date +%Y-%m-%dT%H:%M:%S%z)"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another daily jiphyeonjeon briefing run is already active: $LOCK_DIR"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if [[ -f "$WORKSPACE/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$WORKSPACE/.env"
  set +a
fi
if [[ -f "$BRIDGE_PROJECT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$BRIDGE_PROJECT/.env"
  set +a
fi

RUN_DATE="${NEWSLETTER_DATE:-$(date +%F)}"

if [[ ! -x "$PAPER_SKILL/scripts/newsletter-archive-briefing.sh" ]]; then
  echo "ERROR: missing newsletter archive builder: $PAPER_SKILL/scripts/newsletter-archive-briefing.sh" >&2
  exit 2
fi
if [[ ! -x "$BRIDGE_PROJECT/.venv/bin/discord-openclaw-post-briefing" ]]; then
  echo "ERROR: missing Discord bridge briefing publisher venv entrypoint" >&2
  exit 2
fi

export NEWSLETTER_DATE="$RUN_DATE"
export NEWSLETTER_WIKI_ROOT="${NEWSLETTER_WIKI_ROOT:-$WORKSPACE/wiki}"
export NEWSLETTER_REPORT_PATH="${NEWSLETTER_REPORT_PATH:-$WORKSPACE/reports/newsletter-briefing-latest.md}"
export NEWSLETTER_ARCHIVE_SOURCE="${NEWSLETTER_ARCHIVE_SOURCE:-$NEWSLETTER_WIKI_ROOT/raw/newsletters/$RUN_DATE/items.json}"
export DISCORD_BRIEFING_SOURCE="${DISCORD_BRIEFING_SOURCE:-$WORKSPACE/reports/daily-trends-latest.md}"
# PaperWiki KG interest recommendation, pushed here by the local sync-results.sh
# step 1.9. Appended to the daily briefing only when its date marker matches today.
export KG_RECOMMEND_SNIPPET="${KG_RECOMMEND_SNIPPET:-$WORKSPACE/reports/kg-interest-recommend.md}"
WAIT_SECONDS="${DAILY_BRIEFING_WAIT_SECONDS:-600}"
WAIT_INTERVAL="${DAILY_BRIEFING_WAIT_INTERVAL_SECONDS:-5}"
NEWSLETTER_LOCK_DIR="${NEWSLETTER_ARCHIVE_LOCK_DIR:-$WORKSPACE/.locks/newsletter-archive-and-cardnews.lock}"

if [[ "${DAILY_BRIEFING_DRY_RUN:-0}" == "1" ]]; then
  echo "dry-run: would use daily newsletter briefing for NEWSLETTER_DATE=$NEWSLETTER_DATE"
  echo "dry-run: NEWSLETTER_ARCHIVE_SOURCE=$NEWSLETTER_ARCHIVE_SOURCE"
  echo "dry-run: NEWSLETTER_REPORT_PATH=$NEWSLETTER_REPORT_PATH"
  echo "dry-run: would post Discord daily Jiphyeonjeon briefing from $DISCORD_BRIEFING_SOURCE"
  printf "[%s] daily jiphyeonjeon briefing dry-run complete\n" "$(date +%Y-%m-%dT%H:%M:%S%z)"
  exit 0
fi

report_matches_run_date() {
  [[ -s "$NEWSLETTER_REPORT_PATH" ]] && grep -Fqx -- "작성일: \`$NEWSLETTER_DATE\`" "$NEWSLETTER_REPORT_PATH"
}

archive_and_report_ready() {
  [[ -s "$NEWSLETTER_ARCHIVE_SOURCE" ]] && report_matches_run_date
}

wait_for_archive_runner() {
  local elapsed=0
  while [[ -d "$NEWSLETTER_LOCK_DIR" ]]; do
    if (( elapsed >= WAIT_SECONDS )); then
      return 1
    fi
    sleep "$WAIT_INTERVAL"
    elapsed=$((elapsed + WAIT_INTERVAL))
  done
  return 0
}

elapsed=0
while ! archive_and_report_ready; do
  if (( elapsed >= WAIT_SECONDS )); then
    break
  fi
  sleep "$WAIT_INTERVAL"
  elapsed=$((elapsed + WAIT_INTERVAL))
done

if archive_and_report_ready; then
  echo "daily briefing reusing fresh newsletter archive: $NEWSLETTER_ARCHIVE_SOURCE"
else
  if ! wait_for_archive_runner; then
    echo "ERROR: newsletter archive runner lock did not clear: $NEWSLETTER_LOCK_DIR" >&2
    exit 2
  fi
  if archive_and_report_ready; then
    echo "daily briefing reusing newsletter archive after lock cleared: $NEWSLETTER_ARCHIVE_SOURCE"
  else
    if ! mkdir "$NEWSLETTER_LOCK_DIR" 2>/dev/null; then
      echo "ERROR: newsletter archive runner lock appeared before fallback build: $NEWSLETTER_LOCK_DIR" >&2
      exit 2
    fi
    trap 'rmdir "$NEWSLETTER_LOCK_DIR" 2>/dev/null || true; rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
    echo "daily briefing building newsletter archive for NEWSLETTER_DATE=$NEWSLETTER_DATE"
    "$PAPER_SKILL/scripts/newsletter-archive-briefing.sh"
    rmdir "$NEWSLETTER_LOCK_DIR" 2>/dev/null || true
    trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  fi
fi

if [[ ! -s "$NEWSLETTER_REPORT_PATH" ]]; then
  echo "ERROR: daily newsletter briefing source was not created: $NEWSLETTER_REPORT_PATH" >&2
  exit 2
fi
if ! report_matches_run_date; then
  echo "ERROR: daily newsletter briefing source does not match NEWSLETTER_DATE=$NEWSLETTER_DATE: $NEWSLETTER_REPORT_PATH" >&2
  exit 2
fi

mkdir -p "$(dirname "$DISCORD_BRIEFING_SOURCE")"
python3 - <<'PY_DAILY_BRIEFING'
import json
import os
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

run_date = os.environ["NEWSLETTER_DATE"]
archive_path = Path(os.environ["NEWSLETTER_ARCHIVE_SOURCE"]).expanduser()
output_path = Path(os.environ["DISCORD_BRIEFING_SOURCE"]).expanduser()
GRAPH_EMBEDDING_FAMILY_RE = re.compile(
    r"\b(?:dynamic|temporal|heterogeneous|multiplex|evolving)\s+(?:graph|network)\s+"
    r"(?:embedding|representation(?:\s+learning)?)\b"
    r"|\b(?:graph|network)\s+representation\s+learning\b",
    re.IGNORECASE,
)


def clean(value: object, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("​", " ").split())
    text = re.sub(r"[*_`]+", "", text).strip()
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip(" ,.;") + "…"
    return text


def canonical_url(value: object) -> str:
    raw = unescape(str(value or "")).strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    parsed = urlsplit(raw)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def item_title(item: dict[str, object]) -> str:
    return clean(item.get("article_title") or item.get("title") or item.get("subject") or "제목 없음", limit=110)


def item_summary(item: dict[str, object]) -> str:
    summary_lines = item.get("summary_lines")
    if isinstance(summary_lines, list):
        joined = clean(" / ".join(str(line) for line in summary_lines if line), limit=190)
        if joined:
            return joined
    for key in ("summary", "article_description", "description", "content_summary", "public_excerpt", "excerpt", "note"):
        value = clean(item.get(key), limit=190)
        if value:
            return value
    title = item_title(item)
    return f"공개 아카이브에 수집된 `{title}` 항목을 원문 기준으로 확인할 필요가 있습니다."


def normalized_title(value: object) -> str:
    return re.sub(r"\W+", " ", clean(value, limit=180).lower()).strip()


def semantic_family(item: dict[str, object]) -> str:
    text = " ".join(
        clean(item.get(key), limit=240)
        for key in ("article_title", "title", "public_excerpt", "article_description", "summary", "description")
    )
    if GRAPH_EMBEDDING_FAMILY_RE.search(text):
        return "graph_representation_learning"
    return ""


def display_title(item: dict[str, object]) -> str:
    if item.get("_semantic_key") == "graph_representation_learning" or semantic_family(item) == "graph_representation_learning":
        return "그래프 표현학습 최신 벤치마크 묶음"
    return item_title(item)


def display_summary(item: dict[str, object]) -> str:
    if item.get("_semantic_key") == "graph_representation_learning" or semantic_family(item) == "graph_representation_learning":
        return "동적·이종 그래프 표현학습 계열 공개 링크를 하나의 후속 읽기 후보로 묶었습니다."
    return item_summary(item)


def technical_intro(item: dict[str, object]) -> str:
    title = display_title(item)
    summary = display_summary(item)
    return f"`{title}`는 {summary} 이 항목은 단순 링크 공유가 아니라 기술 개념·적용 맥락·후속 검토 지점을 함께 읽어야 하는 후보입니다."


def context_note(item: dict[str, object]) -> str:
    source = source_label(item)
    if item.get("_semantic_key") == "graph_representation_learning" or semantic_family(item) == "graph_representation_learning":
        return "그래프 표현학습은 추천·검색·지식그래프·이상탐지에서 데이터 구조를 어떻게 벡터화할지 다루는 기반 기술입니다."
    return f"`{source}`에서 포착된 공개 신호로, 연구 결과·제품 업데이트·인프라 변화가 실제 워크플로에 어떤 영향을 주는지 확인할 필요가 있습니다."


def practice_note(item: dict[str, object]) -> str:
    text = " ".join(
        clean(item.get(key), limit=240).lower()
        for key in ("article_title", "title", "public_excerpt", "article_description", "summary", "description")
    )
    if "rag" in text or "retrieval" in text:
        return "검색 품질, 평가셋, 인용 가능성, 운영 모니터링 지표를 분리해 읽어야 합니다."
    if "agent" in text or "copilot" in text:
        return "자동화 범위, 실패 복구, 권한 경계, 사람 검토 지점을 함께 설계해야 합니다."
    if "graph" in text:
        return "데이터 스키마, 시간 변화, 벤치마크 재현성, downstream task 적합성을 확인해야 합니다."
    return "기술 소개는 가능성보다 제약을 함께 봐야 하며, 실제 도입 전 데이터·비용·운영 리스크를 분리해 점검해야 합니다."


def source_label(item: dict[str, object]) -> str:
    for key in ("source", "sender", "newsletter", "source_name", "venue"):
        value = clean(item.get(key), limit=70)
        if value:
            return value
    url = canonical_url(item.get("url"))
    return urlsplit(url).netloc if url else "unknown-source"

payload = json.loads(archive_path.read_text(encoding="utf-8"))
all_items = payload.get("items") if isinstance(payload, dict) else []
if not isinstance(all_items, list):
    all_items = []


def item_date(item: dict[str, object]) -> str:
    for key in ("received_at", "published_at", "date", "created_at"):
        value = clean(item.get(key), limit=40)
        if re.match(r"^\d{4}-\d{2}-\d{2}", value):
            return value[:10]
    return ""

dated_items = [item for item in all_items if isinstance(item, dict) and item_date(item) == run_date]
undated_items = [item for item in all_items if isinstance(item, dict) and not item_date(item)]
items = dated_items if dated_items else undated_items
stale_count = len([item for item in all_items if isinstance(item, dict) and item_date(item) and item_date(item) != run_date])

unique: list[dict[str, object]] = []
seen: set[str] = set()
seen_semantic: set[str] = set()
for raw in items:
    if not isinstance(raw, dict):
        continue
    title = item_title(raw)
    url = canonical_url(raw.get("url"))
    title_key = normalized_title(title)
    semantic_key = semantic_family(raw)
    key = title_key or url
    if not key:
        continue
    if key in seen or (semantic_key and semantic_key in seen_semantic):
        match_key = key if key in seen else semantic_key
        for existing in unique:
            if existing.get("_dedupe_key") == key or existing.get("_semantic_key") == match_key:
                related_urls = existing.setdefault("_related_urls", [])
                if isinstance(related_urls, list) and url and url not in related_urls:
                    related_urls.append(url)
                break
        continue
    seen.add(key)
    if semantic_key:
        seen_semantic.add(semantic_key)
    item = dict(raw)
    item["_dedupe_key"] = key
    item["_semantic_key"] = semantic_key
    item["_related_urls"] = [url] if url else []
    unique.append(item)

selected = unique[:7]
lead = selected[:3]
lines = [
    "**집현전 데일리 뉴스레터 — 기술 블로그 브리핑**",
    f"작성일: `{run_date}`",
    f"기반 아카이브: `{archive_path.name}` · 전체 {len(all_items)}개 / 당일 {len(items)}개 / 이전 날짜 제외 {stale_count}개 / 주제 기준 핵심 묶음 {len(unique)}개",
    "개인정보 경계: 메일 본문/비밀값 없이 데일리 아카이브의 공개 제목·요약·출처 링크만 사용",
    "",
    "> 오늘의 3줄 요약",
]
if lead:
    for idx, item in enumerate(lead, start=1):
        lines.append(f"> {idx}. {display_title(item)} — {display_summary(item)}")
else:
    lines += [
        "> 1. 오늘 날짜에 해당하는 신규 공개 후보가 없습니다.",
        "> 2. 이전 날짜 누적 항목은 반복 방지를 위해 제외했습니다.",
        "> 3. 다음 실행에서 신규 수집분을 다시 점검합니다.",
    ]
lines += [
    "",
    "## 읽는 법",
    "- 오늘의 항목은 블로그 포스팅처럼 `기술 소개 → 왜 중요한가 → 실무/연구 포인트 → 원문` 순서로 정리합니다.",
    "- 각 설명은 공개 제목·요약·출처 링크에 근거한 소개이며, 원문 확인 전 과도한 결론을 내리지 않습니다.",
    "",
    "## 오늘의 핵심 항목 — 기술 블로그형 소개",
]
if not selected:
    lines.append("- 공개 링크 후보 없음")
for idx, item in enumerate(selected, start=1):
    title = display_title(item)
    summary = display_summary(item)
    url = canonical_url(item.get("url"))
    source = source_label(item)
    related_urls = item.get("_related_urls")
    extra_count = max(0, len(related_urls) - 1) if isinstance(related_urls, list) else 0
    lines += [
        f"### {idx}. {title}",
        f"- 기술 소개: {technical_intro(item)}",
        f"- 왜 중요한가: {context_note(item)}",
        f"- 실무/연구 포인트: {practice_note(item)}",
        f"- 원문 링크: {url or '공개 링크 없음'}",
    ]
    if extra_count:
        lines.append(f"- 관련 링크: 같은 제목으로 수집된 추가 공개 링크 {extra_count}개")
# PaperWiki KG interest recommendation (pushed from local sync-results.sh step
# 1.9). Date-gated: only appended when the snippet's marker matches run_date, so
# a stale snippet from a failed earlier push is never posted. The `## ` heading
# makes it its own message chunk in post_briefing's splitter.
kg_snippet_path = os.environ.get("KG_RECOMMEND_SNIPPET", "").strip()
if kg_snippet_path:
    snippet_file = Path(kg_snippet_path).expanduser()
    if snippet_file.exists():
        try:
            snippet_text = snippet_file.read_text(encoding="utf-8")
        except OSError:
            snippet_text = ""
        snippet_lines = snippet_text.splitlines()
        marker_idx = next((i for i, ln in enumerate(snippet_lines) if ln.strip()), None)
        if marker_idx is not None:
            marker = re.match(
                r"<!--\s*kg-recommend date:\s*(\d{4}-\d{2}-\d{2})\s*-->\s*$",
                snippet_lines[marker_idx].strip(),
            )
            if marker and marker.group(1) == run_date:
                section = "\n".join(snippet_lines[marker_idx + 1 :]).strip()
                if section:
                    lines += ["", section]

lines += [
    "",
    "## 운영 메모",
    "- 이 브리핑은 주간 research-trends가 아니라 당일 raw newsletter archive에서 직접 생성됩니다.",
    "- received_at/published_at이 작성일과 다른 누적 항목은 제외합니다.",
    "- 같은 제목·반복 주제군은 핵심 묶음으로 합쳐 반복 게시 체감을 줄입니다.",
]
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
PY_DAILY_BRIEFING
echo "daily briefing source rendered from daily archive: $DISCORD_BRIEFING_SOURCE"

cd "$BRIDGE_PROJECT"
.venv/bin/discord-openclaw-post-briefing

printf "[%s] daily jiphyeonjeon briefing done\n" "$(date +%Y-%m-%dT%H:%M:%S%z)"
