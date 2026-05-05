# 집현전-Claw 브리핑 최종 코드 리뷰/검증 — worker-1 — 2026-05-05

## 리뷰 대상

- Apps Script rendering/telemetry commits: `e632475`, `5f03c25` (`integrations/google-apps-script/newsletter_archive_to_discord.gs`)
- Python taxonomy parity tests: `f844193` (`skills/paper-recommender/project/tests/test_newsletter_ingest.py`)
- Acceptance reference: `.omx/reports/briefing-build-plan-acceptance-worker-1-20260505.md`

## 판정

**조건부 PASS.** 이번 변경은 기존 briefing string interface를 유지하면서 Apps Script relay path에 labeled 3-line summary 출력, topic당 최대 2개 item 렌더, relay telemetry 저장/노출을 추가했고 Python taxonomy parity fixture를 보강했다. 로컬 syntax/test/smoke 검증은 통과했다.

## 확인한 개선점

- `renderBriefing_`는 기존 문자열 반환 호환을 유지하고, 새 `renderBriefingWithTelemetry_`가 `{ briefing, telemetry }`를 반환한다.
- `runNewsletterArchive`는 `saveLatestBriefing_(briefing, rendered.telemetry)`로 relay pull 상태에 telemetry를 저장한다.
- 렌더러는 topic별 `entry.detailed[0]` 단일 대표에서 `BRIEFING_MAX_ITEMS_PER_TOPIC = 2`까지 출력하도록 개선됐다.
- 출력 라벨은 `핵심 요약`, `기술 포인트`, `의미/근거`, `출처 링크`로 명시되어 3-line role contract와 더 잘 맞는다.
- `doGet`은 기존 `briefing/generated_at/item_count/query`를 유지하면서 `telemetry`를 추가해 backward compatibility를 유지한다.
- Python tests는 Apps Script parity intent fixture와 false-positive guard를 추가했다.

## 남은 리스크

- Telemetry는 `item_count/topic_count/rendered_topic_count/rendered_item_count/detailed_item_count/topic_counts/truncated` 중심이며, acceptance matrix의 `urlCandidates/blocked/detailFetchAttempted/detailFetchSucceeded/dropped`까지는 아직 완전하지 않다.
- Apps Script는 topic round-robin이 아니라 selected topic별 최대 2개 렌더 방식이므로, long-tail topic fairness는 topic sorting에 계속 의존한다.
- 3-line summary는 라벨 출력은 개선됐지만 내부 생성은 여전히 공개 article text 기반 sentence selection/fallback이다. 완전한 paraphrase/rewrite 품질 게이트는 후속 과제다.
- GAS/Python taxonomy parity는 Python fixture로 intent를 고정했지만 GAS runtime fixture suite는 아직 없다.
- Live Gmail, Apps Script deployment, Discord publish는 수행하지 않았다.

## 검증 증거

- PASS `node --check` on temporary copy of `integrations/google-apps-script/newsletter_archive_to_discord.gs` at worker-2 HEAD.
- PASS Apps Script render smoke appended to temporary JS copy: `{"rendered_topic_count":3,"rendered_item_count":6,"hasLabels":true}`.
- PASS `git diff --check 36a95b2..5f03c25` in worker-2 worktree.
- PASS `cd worker-2/skills/paper-recommender/project && uv run --with pytest pytest -q tests/test_newsletter_ingest.py tests/test_gmail_newsletter_briefing.py` → `19 passed in 0.05s`.
- PASS `cd worker-2/skills/discord-openclaw-bridge/project && uv run --with pytest pytest -q tests/test_post_newsletter.py tests/test_briefing.py` → `7 passed in 0.08s`.
- PASS `cd worker-2/skills/paper-recommender/project && uv run --with ruff ruff check ../newsletter_ingest.py ../gmail_newsletter_briefing.py tests/test_newsletter_ingest.py tests/test_gmail_newsletter_briefing.py` → `All checks passed!`.
- PASS `python3 -m compileall -q skills/paper-recommender/newsletter_ingest.py skills/paper-recommender/gmail_newsletter_briefing.py skills/paper-recommender/project/src`.
- PASS changed-files concrete secret scan over `36a95b2..5f03c25` → no concrete Discord webhook/relay/OpenClaw/GitHub/Google token-like values.

## Subagent note

Task-4 required a review probe. Review probe `019df59d-3187-75a0-bd7a-6f1a01b6f930` was spawned but stalled past bounded waits. Per leader instruction `9d7c1a1e-f53b-4cda-b734-5fb0e8dc14c7`, completion uses local review evidence above instead of waiting further.
