# 집현전-Claw 브리핑 품질 심층 리뷰 — worker-1 — 2026-05-05

## 판정 요약

현재 브리핑 파이프라인은 날짜/KST, Discord 분할, Colab 제거, 공개 링크 기반 3줄 요약의 뼈대는 갖췄지만, 연구자가 빠르게 기술 변화와 연구 흐름을 추적하기에는 “토픽 다양성”, “원문 기반 기술 요약의 밀도”, “운영 품질 계측”이 아직 약하다. 특히 뉴스레터 아카이브는 233개 수집 항목 대비 노출 토픽이 3개로 보인다는 스냅샷이 있어, 수집량이 정보 다양성으로 전환되지 못하는 병목이 가장 크다.

## 근거

- 요구 컨텍스트: `.omx/context/briefing-quality-review-20260505T091800KST.md`는 일간 브리핑이 2026-05-05 KST 제목/날짜로 보정되었고 Discord 메시지 2개, 뉴스레터는 233개 수집 항목이지만 공개 토픽은 3개이며 Discord 메시지 3개라고 기록한다.
- 뉴스레터 Apps Script는 Gmail `newer_than:7d`, `COLLECT_ALL_MAIL=true`, `INCLUDE_ALL_URLS=true`, `FETCH_ARTICLE_DETAILS=true`, `MAX_ARTICLE_FETCHES=35`를 기본값으로 둔다 (`integrations/google-apps-script/newsletter_archive_to_discord.gs:20-35`). 이는 넓은 수집에는 유리하지만 233개 중 상세 fetch 상한이 낮아 다수 항목이 메타데이터/컨텍스트 기반으로 남을 수 있다.
- 뉴스레터 토픽 규칙은 7개 대분류와 단일 best-topic 선택 구조다 (`integrations/google-apps-script/newsletter_archive_to_discord.gs:59-104`, `:929-944`). 현재 렌더러는 토픽별 `entry.detailed[0]` 한 항목만 표시한다 (`integrations/google-apps-script/newsletter_archive_to_discord.gs:239-264`). 따라서 토픽 내부 다양성과 장기꼬리 항목이 구조적으로 사라진다.
- URL 랭킹은 고가치 호스트/경로/구독·로그인 패널티 중심 휴리스틱이다 (`integrations/google-apps-script/newsletter_archive_to_discord.gs:357-379`). 좋은 1차 필터지만, 기사 novelty, 연구성, 사용자 SOUL 적합도, 중복 클러스터 균형은 직접 반영하지 않는다.
- 기사 상세 추출은 공개 HTML/text만 fetch하고 사설/로컬/Colab/로그인성 URL을 회피한다 (`integrations/google-apps-script/newsletter_archive_to_discord.gs:393-430`, `:902-911`). 개인정보/저작권 경계는 적절하나, 실패 이유와 원문 기반 여부가 출력 품질 지표로 노출되지 않는다.
- 3줄 요약은 core/technical/impact 역할별 문장 추출과 fallback 문장 생성을 사용한다 (`integrations/google-apps-script/newsletter_archive_to_discord.gs:576-621`, `:653-719`). 그러나 문장 추출형이라 “발췌 같은 줄”이 남을 수 있고, 방법·데이터·결과를 한국어로 재합성하는 단계가 부족하다.
- 일간 연구 브리핑은 seed topic 생성, multi-source fetch, clustering, top cluster selection, deep bridge, note/raw/status 기록의 단계가 있다 (`skills/paper-recommender/project/src/paper_recommender/daily_research.py:175-230`, `:253-326`). `last_run_status.json`은 후보 수, 클러스터 수, deep 성공, fallback, 소스 통계를 남긴다 (`skills/paper-recommender/project/src/paper_recommender/daily_research.py:331-357`).
- Discord daily 렌더러는 raw.json 우선, KST 날짜, SOUL/profile basis, 최대 3개 클러스터와 링크 2개를 표시한다 (`skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/briefing.py:211-281`). 날짜/근거성은 개선됐지만, “각 content item 3-line core summary + source link” 선호에는 클러스터 단위 2줄 요약만 제공한다.
- Discord posting은 daily/newsletter 모두 분할 전송을 지원하고 메시지 수를 출력한다 (`skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/post_briefing.py:49-60`, `skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/post_newsletter.py:47-89`, `:92-116`). 다만 전송 결과의 항목 수, 누락/절단/토픽 수는 별도 상태 파일로 남기지 않는다.
- 관련 테스트는 KST 제목, raw.json 우선, 토픽 경계 분할, truncation, fallback warning 등을 검증한다 (`skills/discord-openclaw-bridge/project/tests/test_briefing.py:16-84`, `skills/discord-openclaw-bridge/project/tests/test_post_newsletter.py:37-57`, `skills/paper-recommender/project/tests/test_daily_note.py:38-83`). 품질/다양성/원문성 acceptance test는 아직 명시적이지 않다.

## 잔여 품질 갭

1. 뉴스레터 토픽 다양성 손실
   - 233개 항목이 3개 공개 토픽으로 압축되는 현상은 단순 분류 문제뿐 아니라 렌더 단계의 “토픽당 1개만 표시” 정책 때문이다.
   - 단일 label 분류는 `LLM/에이전트`, `검색/RAG/지식그래프`, `오픈소스/코드`처럼 겹치는 AI 기술 항목을 한 대분류로만 귀속한다.

2. 원문 기반 요약의 합성력 부족
   - 현재 뉴스레터 요약은 문장 선택형이라 공개 기사 문장을 짧게 골라 붙이는 경향이 남는다.
   - “무엇이 새롭나 / 기술적으로 왜 중요한가 / 연구자가 무엇을 확인해야 하나”의 3줄 형식이 안정적으로 보장되지 않는다.

3. 일간 연구 브리핑의 item-level 부족
   - daily Discord는 클러스터 단위 핵심 요약·기술 포인트·링크를 보여주지만, 각 논문/기사별 3줄 요약과 링크 구조는 newsletter 쪽보다 약하다.
   - 동적 그래프 학습 같은 당일 편향 토픽이 유용하더라도, “왜 오늘 이 클러스터가 선택됐는지”와 “반복/누락 여부”가 사용자에게 보이지 않는다.

4. 운영 관측성 불균형
   - daily는 `last_run_status.json`이 있지만 newsletter는 수집 수, fetch 성공률, fetch 실패 원인, 필터링 수, 토픽 entropy, 렌더 누락 수, Discord chunk 수를 동일한 상태 모델로 남기지 않는다.
   - Discord 전송 성공은 메시지 수 출력 중심이며, 내용 품질 SLA와 연결되지 않는다.

5. 개인정보/저작권 안전성과 품질의 연결 부족
   - Gmail 본문은 게시하지 않는 경계가 있으나, “요약 line이 공개 URL 원문 기반인지, 메일 snippet/context 기반인지”가 출력에 드러나지 않는다.
   - 공개 원문 fetch 실패 시 안전한 fallback은 필요하지만, fallback 비율이 높으면 품질 저하로 취급해야 한다.

## 고도화 방향

### P0: 뉴스레터 다양성 회복

- 토픽 분류를 단일 label에서 `primary_topic`, `secondary_topics`, `content_type`, `source_type`, `novelty_score`, `technical_depth_score`로 분리한다.
- 렌더링을 “토픽당 1개”에서 “상위 토픽 5~8개, 토픽당 2~3개, 전체 최대 N개”로 바꾼다.
- 토픽별 대표 선정은 count 순이 아니라 MMR 방식으로 사용자 SOUL 적합도와 토픽 다양성을 동시에 최적화한다.
- Acceptance:
  - 100개 이상 수집 시 표시 토픽 5개 이상 또는 topic entropy 1.5 이상.
  - 전체 렌더 항목 중 동일 토픽 비중 45% 이하.
  - 각 표시 토픽에 최소 1개 source link와 3줄 summary 존재.

### P1: 3줄 요약 계약 강화

- 요약 contract를 고정한다.
  1. 핵심: 발표/논문/도구가 무엇을 주장하거나 공개했는가.
  2. 기술: 방법, 데이터, 아키텍처, 평가, 코드, 한계 중 최소 1개 구체 신호.
  3. 추적 포인트: 연구자가 볼 링크/벤치마크/재현성/제품 영향.
- 공개 원문이 충분하면 LLM 또는 deterministic rewrite 단계에서 한국어 합성 요약을 생성하고, 문장 추출은 evidence 후보로만 사용한다.
- 공개 원문이 부족하면 “공개 원문 부족, 메일 컨텍스트 기반” 태그를 내부 상태에 남기고 렌더 우선순위를 낮춘다.
- Acceptance:
  - 표시 항목 95% 이상이 정확히 3줄 + 끊기지 않은 source link.
  - summary line의 평균 길이 60~180자.
  - boilerplate/login/unsubscribe/marketing-only 문장 포함률 2% 이하.

### P2: daily research 브리핑을 논문 추적형으로 확장

- Discord daily의 각 클러스터 아래 대표 논문/아티클 2~3개를 `핵심/기술/확인할 링크` 3줄로 붙인다.
- daily raw payload의 candidate/cluster/deep success 정보를 이용해 “새로움”, “SOUL 연결 이유”, “전일 대비 변화”를 표시한다.
- dynamic graph처럼 한 분야로 집중된 날에는 의도된 집중인지, 소스 편향인지, seed topic 편향인지 설명한다.
- Acceptance:
  - daily report에 후보 수, 클러스터 수, deep 성공률, fallback 여부, top source 분포가 표시된다.
  - 대표 클러스터 3개 이하라도 각 클러스터 내 item link가 최소 2개 제공된다.
  - deep 실패/fallback 시 Discord 본문에 품질 caveat가 표시된다.

### P3: 운영 상태와 알림 SLA 통합

- newsletter도 daily와 같은 `last_run_status.json`을 남긴다: collected, unique_urls, blocked_colab, fetched_ok, fetched_failed_by_reason, detailed_items, rendered_items, rendered_topics, dropped_due_limit, discord_messages, char_budget_used.
- Discord posting 결과를 상태 파일과 로그에 구조화한다.
- cron 보장은 KST 기준 report date, latest symlink freshness, channel delivery timestamp, duplicate-send guard를 함께 검증한다.
- Acceptance:
  - 08:10 KST까지 daily/newsletter 각각 latest report와 Discord delivery status가 갱신된다.
  - fetch 성공률, topic entropy, rendered item count가 임계값 미만이면 경고가 남는다.
  - 동일 report hash 재전송 방지 또는 명시적 retry idempotency가 있다.

## 단계별 로드맵

1. 1일차: 계측 추가와 실패 원인 가시화
   - newsletter 상태 JSON을 daily 상태 모델과 맞춘다.
   - current run 기준 rendered topics/items/fetch 성공률/topic entropy를 계산한다.
   - 코드 변경 전후 비교용 golden fixture를 만든다.

2. 2~3일차: 토픽/랭킹 개편
   - hierarchical taxonomy와 multi-label scorer를 도입한다.
   - MMR 대표 선정을 추가해 토픽당 2~3개를 안정적으로 표시한다.
   - Colab/utility filter regression test를 유지한다.

3. 4~5일차: 요약 품질 강화
   - 3줄 summary contract를 코드와 테스트에 명시한다.
   - 공개 원문 기반 rewrite를 추가하거나, 최소한 추출문을 역할별 한국어 bullet로 재작성한다.
   - copyright/privacy: 원문 장문 인용 금지, 25단어 이상 연속 복사 방지, source link 유지.

4. 1주차 말: daily/newsletter 통합 품질 게이트
   - daily와 newsletter 모두 acceptance metrics를 상태 파일에 기록한다.
   - Discord 본문에 caveat와 source/fallback 표시를 추가한다.
   - cron freshness와 delivery idempotency smoke test를 만든다.

## 리스크와 대응

- Apps Script 실행시간/UrlFetch quota: fetch 상한을 무작정 올리지 말고 URL pre-ranking, per-host budget, cached details를 적용한다.
- LLM 요약 비용/지연: 표시 후보에만 rewrite를 적용하고, low-confidence/fallback 항목은 제외 또는 낮은 우선순위로 둔다.
- 개인정보: Gmail 본문/발신자 전문을 게시하지 않고, 공개 URL 원문과 짧은 컨텍스트만 사용한다. 내부 상태에도 토큰/메일 전문은 저장하지 않는다.
- 저작권: 장문 발췌 대신 한국어 합성 요약, 짧은 제목/링크 중심으로 유지한다.

## 검증 계획

- Fixture 기반 단위 테스트
  - 233개 유사 fixture에서 rendered topics >= 5, rendered items >= 12, topic max share <= 45%.
  - Colab/로그인/구독/이미지 URL 필터 유지.
  - 3줄 summary contract와 source link line break 없음 검증.
- Golden report 비교
  - 변경 전후 Discord markdown을 저장해 토픽 수, 항목 수, 링크 수, truncation 여부를 diff한다.
- 운영 smoke
  - dry-run으로 상태 JSON 생성, latest 파일 freshness, Discord split chunk 수, report hash를 확인한다.
- 수동 품질 샘플링
  - 표시 항목 20개 중 공개 원문 기반 합성 요약 18개 이상, excerpt-like/boilerplate 1개 이하를 목표로 한다.

## 병렬 리뷰 프로브 통합 사항

- Subagent `019df590-a13f-7540-81bb-5743fb39e952`는 뉴스레터 렌더러의 토픽당 대표 1개 노출과 daily 렌더러의 클러스터 3개 제한이 사용자 선호의 “각 content item 3줄 요약 + 링크” 계약과 충돌한다고 지적했다. 이 내용은 P0/P2 로드맵에 반영했다.
- Discord splitter가 긴 chunk를 문자 단위로 fallback 절단할 수 있어 링크/문장 파손 위험이 있다는 지적을 P3의 `truncation_count`, broken markdown link 0건 acceptance에 반영했다.
- 고정 7개 토픽 + single best-topic 구조가 233개 수집 항목의 3개 토픽 collapse와 직접 연결된다는 지적을 taxonomy 개편의 핵심 근거로 반영했다.
- article fetch/filter/drop 카운트가 외부 지표로 남지 않는다는 지적을 newsletter `last_run_status.json` 지표 목록에 반영했다.
- 최종 acceptance 기준에는 공개 원문 기반 3줄 요약 90~95%, top topic 점유율 45~50% 이하, Colab/private URL 0건, truncation/broken link 0건을 포함했다.
