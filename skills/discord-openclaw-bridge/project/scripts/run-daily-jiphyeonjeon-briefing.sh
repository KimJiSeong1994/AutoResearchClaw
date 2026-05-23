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

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
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


def source_label(item: dict[str, object]) -> str:
    for key in ("source", "sender", "newsletter", "source_name", "venue"):
        value = clean(item.get(key), limit=70)
        if value:
            return value
    url = canonical_url(item.get("url"))
    return urlsplit(url).netloc if url else "unknown-source"

payload = json.loads(archive_path.read_text(encoding="utf-8"))
items = payload.get("items") if isinstance(payload, dict) else []
if not isinstance(items, list):
    items = []

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
    "**집현전 데일리 뉴스레터**",
    f"작성일: `{run_date}`",
    f"기반 아카이브: `{archive_path.name}` · 수집 {len(items)}개 / 주제 기준 핵심 묶음 {len(unique)}개",
    "개인정보 경계: 메일 본문/비밀값 없이 데일리 아카이브의 공개 제목·요약·출처 링크만 사용",
    "",
    "> 오늘의 3줄 요약",
]
if lead:
    for idx, item in enumerate(lead, start=1):
        lines.append(f"> {idx}. {display_title(item)} — {display_summary(item)}")
else:
    lines += [
        "> 1. 오늘 데일리 아카이브에 게시할 공개 후보가 없습니다.",
        "> 2. 수집 경로와 relay 상태를 확인해야 합니다.",
        "> 3. 다음 실행에서 신규 공개 링크 여부를 다시 점검합니다.",
    ]
lines += ["", "## 오늘의 핵심 항목"]
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
        f"- 핵심 내용: {summary}",
        f"- 왜 중요한가: 오늘 아카이브에서 확인된 `{source}` 기반 변화 신호입니다.",
        f"- 확인 링크: {url or '공개 링크 없음'}",
    ]
    if extra_count:
        lines.append(f"- 관련 링크: 같은 제목으로 수집된 추가 공개 링크 {extra_count}개")
lines += [
    "",
    "## 운영 메모",
    "- 이 브리핑은 주간 research-trends가 아니라 당일 raw newsletter archive에서 직접 생성됩니다.",
    "- 같은 제목·반복 주제군은 핵심 묶음으로 합쳐 반복 게시 체감을 줄입니다.",
]
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
PY_DAILY_BRIEFING
echo "daily briefing source rendered from daily archive: $DISCORD_BRIEFING_SOURCE"

cd "$BRIDGE_PROJECT"
.venv/bin/discord-openclaw-post-briefing

printf "[%s] daily jiphyeonjeon briefing done\n" "$(date +%Y-%m-%dT%H:%M:%S%z)"
