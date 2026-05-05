# 집현전-Claw 브리핑 품질 고도화 종합 로드맵

- 기준일: 2026-05-05 KST
- 작성자: worker-4 / executor
- 산출물 범위: read-only 품질 분석 기반 문서 산출. production secret, 토큰, 원문 메일 본문, 비공개 EC2 파일 내용은 포함하지 않음.
- 입력 컨텍스트: `.omx/context/briefing-quality-review-20260505T091800KST.md`의 최근 증거 스냅샷, 로컬 repo 코드/테스트/README.

## 1. 결론

현재 브리핑 시스템은 이미 “한국어 3줄 요약 + 출처 링크 + Discord 섹션 분할 + privacy boundary”라는 기본 골격을 갖췄다. 다만 233개 뉴스레터 항목이 3개 토픽만 노출되는 현상과 일부 clipped/excerpt형 요약은 **수집량 문제가 아니라 선별·다양성·증거 품질 계층이 부족한 문제**로 보는 것이 타당하다.

가장 높은 ROI의 다음 단계는 다음 순서다.

1. **토픽별 1개만 노출하는 렌더링을 “토픽별 대표 2-3개 + 전체 다양성 예산”으로 개선**한다.
2. **토픽 분류를 단일 best label에서 multi-label 점수 + source-kind 보정 + novelty/diversity rank로 확장**한다.
3. **3줄 요약을 excerpt 선택이 아니라 역할별 합성 contract로 검증**한다: 핵심, 기술 포인트, 의미/근거가 모두 있어야 게시한다.
4. **운영 관측성을 item/topic/message/char/drop reason 단위로 남겨 품질 저하를 알림 가능한 상태로 만든다.**

## 2. 현재 근거 요약

### Daily research trends briefing

- 컨텍스트 스냅샷상 2026-05-05 daily report 제목 날짜는 KST와 일치하고, Discord posting은 2 messages로 완료됐다.
- `skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/briefing.py`는 adjacent `raw.json`이 있으면 markdown fallback보다 raw report를 우선 사용한다. 테스트도 `test_weekly_report_prefers_adjacent_raw_json`, `test_weekly_report_title_uses_kst_date`로 이를 고정한다.
- 같은 파일의 raw 렌더러는 cluster를 최대 3개까지 보여주고 각 cluster에 `핵심 요약`, `기술 포인트`, `출처 링크`를 제공한다.
- `skills/paper-recommender/project/src/paper_recommender/trend_report.py`는 LLM 합성 결과를 `valid_ids`와 `min_evidence_per_cluster`로 검증하고, 실패 시 deterministic fallback을 사용한다.

판단: Daily briefing의 기본 근거성은 비교적 좋다. 남은 핵심 과제는 “3개 cluster 제한이 충분한가”, “왜 이 SOUL 사용자에게 중요한가가 기술적으로 충분히 구체적인가”, “fallback/coverage caveat가 Discord에서 충분히 보이는가”다.


### Existing taxonomy/implementation docs

Subagent repository-map findings identified three existing planning artifacts that should be treated as upstream evidence, not duplicated from scratch:

- `skills/paper-recommender/docs/newsletter-topic-taxonomy-plan.md`: already defines a Primary + Secondary taxonomy direction, score-based matching, known false-positive risks, and staged rollout.
- `skills/paper-recommender/docs/newsletter-topic-taxonomy-implementation-design.md`: translates that taxonomy into Python/GAS implementation contracts, backward-compatible classifier APIs, fixture requirements, and rollout risks.
- `skills/paper-recommender/docs/current-topic-rules-briefing-evidence.md`: records current `TOPIC_RULES` behavior, regression cases, and why substring/order bias is insufficient for long-term trend tracking.

Implication: this roadmap should prioritize integration and acceptance metrics over inventing a second taxonomy. The canonical implementation lane should reuse the existing taxonomy docs, then make Newsletter Apps Script and Python ingest converge through fixture parity.

### Newsletter archive briefing

- 컨텍스트 스냅샷상 최근 newsletter report는 233개 항목을 수집했지만 Discord에는 3개 published topics만 보였다.
- `integrations/google-apps-script/newsletter_archive_to_discord.gs`는 `TOPIC_RULES` 7개와 `TOPIC_SCORE_THRESHOLD = 2`로 topic을 정하고, `renderBriefing_`에서 `topics.forEach`를 돌지만 각 topic마다 `entry.detailed[0]` 하나만 게시한다. 나머지는 “추가 항목 N개”로 숨는다.
- 같은 스크립트는 `BRIEFING_RENDER_CHAR_LIMIT = 7600`, `MAX_ARTICLE_FETCHES = 35`, `MIN_ARTICLE_TEXT_CHARS = 220`를 둔다. 233개 항목 중 실제 공개 원문 상세 fetch/3줄 요약 가능 항목이 제한될 수밖에 없다.
- `isBlockedNewsletterItem_`은 `colab.research.google.com`, 제목/설명/context의 Colab 신호, `c.gle` Colab 단축 링크를 차단한다.
- `buildSummaryLines_`는 공개 article description/text를 기반으로 3문장을 선택하지만, 본질적으로 기존 문장을 고르는 방식이므로 clipped/excerpt처럼 보일 수 있다.
- `skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/post_newsletter.py`는 `### ` topic boundary 기준으로 Discord message를 분할하므로, topic-wise splitting 선호는 이미 구현되어 있다.

판단: Newsletter 품질 문제의 1차 원인은 topic classifier 자체보다 **렌더링/랭킹 단계가 topic별 단일 대표만 보여주고, article detail fetch 예산과 character budget이 제한되어 long-tail을 압축**하는 데 있다.


### Standalone Python newsletter ingest path

The local-export newsletter path already has a more testable Python surface than Apps Script:

- `skills/paper-recommender/newsletter_ingest.py` has `_TOPIC_RULES`, `classify_topic_result`, `classify_topic`, `group_items_by_topic`, and `render_topic_briefing`.
- `publish_items` explicitly writes metadata/extracted URLs only and marks privacy as `metadata-and-extracted-urls-only; full email bodies omitted`.
- The Python renderer already supports `max_items_per_topic=3`, while the Apps Script renderer currently posts only the first detailed item per topic. This is a useful concrete parity target for the Apps Script path.

Implication: implement and test taxonomy/diversity behavior in Python first, then mirror in GAS after fixture behavior is stable.

## 3. 우선순위 로드맵

### P0. 품질 계측과 안전 경계 고정

목표: 개선 전후 비교가 가능하도록 raw 품질 지표를 남긴다.

작업:

- Newsletter Apps Script 결과에 다음 count를 저장/표시한다.
  - 수집 전체 항목 수
  - URL 후보 수
  - blocked Colab/private utility URL 수
  - article detail fetch 시도/성공/실패 수
  - topic별 total/detailed/rendered count
  - render char budget으로 탈락한 topic/item 수
- Daily briefing에는 raw report 기준 cluster 수, candidates 수, fallback 여부, coverage caveat 노출 여부를 Discord body에 짧게 유지한다.
- secret boundary를 명문화한다: Discord에는 메일 본문 원문, 토큰, relay URL/token, OpenClaw gateway token을 게시하지 않는다.

Acceptance criteria:

- 뉴스레터 1회 실행 후 “233 collected / N detailed / M rendered / K dropped by char budget”처럼 병목이 보인다.
- Colab 제거 count가 0이 아니면 운영 메모에 필터링 사실만 보이고 URL 원문은 필요 이상 노출하지 않는다.
- repo grep에서 webhook/token 실제값이 없어야 한다.

### P1. Newsletter 다양성 랭킹 개선

목표: 많은 source가 1-3개 topic으로 납작해지는 현상을 줄이고, 연구자가 빠르게 볼 수 있는 대표 항목 수를 늘린다.

작업:

1. `renderBriefing_`에서 topic별 `entry.detailed[0]`만 쓰는 구조를 바꾼다.
   - topic별 최소 1개, 최대 2-3개를 노출한다.
   - 전체 budget은 `maxRenderedItems`로 제한하되 topic round-robin 방식으로 뽑는다.
2. topic sorting을 `detailed.length` 단일 우선에서 다음 혼합 점수로 바꾼다.
   - detailed count
   - source diversity(host/domain 수)
   - paper/research-post/code 같은 source kind diversity
   - 최근성(receivedAt)
   - user-preferred topic weight
3. topic label을 단일 best만 저장하지 말고 top-2 scores를 debug metadata로 저장한다.
   - Discord에는 label만 보여도 되지만 raw/debug에는 score를 남긴다.

Acceptance criteria:

- 200개 이상 수집 fixture에서 rendered topics가 최소 5개 이상이거나, 실제 detailed topic 수가 그보다 작으면 그 이유가 count로 보인다.
- 한 topic이 전체 rendered item의 40%를 초과하지 않는다. 단, detailed topic이 2개 이하인 경우는 예외.
- `research paper`가 `search`로 오분류되지 않고 `논문/리서치` fallback 또는 더 구체적인 research topic으로 간다.


### P1.5. Python/GAS taxonomy parity

목표: 로컬 export 기반 Python ingest와 Apps Script Gmail archive가 같은 topic 결과를 내도록 drift를 줄인다.

작업:

- 기존 `newsletter-topic-taxonomy-plan.md`와 `newsletter-topic-taxonomy-implementation-design.md`를 canonical spec로 삼아 fixture를 만든다.
- Python `classify_topic_result`에 `primary`, `secondary`, `confidence`, `reasons` 형태의 상세 결과를 추가하되, 기존 `classify_topic -> str` 호환은 유지한다.
- GAS `classifyTopic_`는 wrapper로 유지하고, 내부에는 `classifyTopicDetail_` 형태를 추가한다.
- `research/search`, `benchmark/market`, `RAG agent`, `GitHub repo for RAG`, `OpenAI pricing`, `healthcare privacy regulation` fixture를 Python/GAS 양쪽에서 같은 기대값으로 검증한다.

Acceptance criteria:

- Python fixture와 GAS fixture의 primary label이 일치한다.
- 기존 briefing heading/bullet shape는 깨지지 않는다.
- raw/debug reasons에는 normalized term/field만 남고 원문 메일 본문은 저장하지 않는다.

### P2. 3줄 요약 품질 contract 강화

목표: “메일/웹 excerpt 3문장”이 아니라 기술 브리핑으로 읽히는 3줄을 만든다.

작업:

- 각 item summary를 내부적으로 다음 schema로 만든다.
  - `core`: 무엇을 발표/제안/검증했는가
  - `technical`: 모델, 데이터셋, 방법, benchmark, architecture 중 어떤 기술 근거가 있는가
  - `meaning`: 성능/한계/연구자 관점 의미와 왜 추적해야 하는가
  - `evidence_source`: article_text, meta_description, url_context, mail_summary 중 무엇을 썼는가
- 현재 `selectRoleSummaryLines_`가 문장 선택에 실패할 때는 generic fallback을 게시하지 말고 “원문 공개 텍스트 부족”으로 raw에만 남긴다.
- LLM을 사용할 경우에도 원문 전문 재게시가 아니라 짧은 paraphrase로 제한하고 source URL을 붙인다.

Acceptance criteria:

- 각 게시 item은 항상 `핵심`, `기술 포인트`, `의미/근거`, `출처 링크`를 갖는다.
- summary line이 45자 미만 generic 문장 또는 boilerplate/cookie/login 문장인 경우 게시되지 않는다.
- 공개 article text가 없으면 “메일 제목 기반 추정”과 “공개 원문 기반 요약”이 구분된다.

### P3. Daily briefing의 연구 추적성 강화

목표: Daily research trends가 단순 cluster 제목 나열이 아니라 후속 연구 행동으로 이어지게 한다.

작업:

- Daily Discord body에 cluster별 “왜 이 사용자 SOUL/Profile에 중요한가”를 1줄로 유지하되, paper/source 2개 링크를 안정적으로 붙인다.
- `trend_report.validate_trend_report`의 min evidence 기준을 acceptance metric으로 노출한다.
- cluster가 3개를 초과할 때는 “기타 후보 cluster 수”와 “선택 이유”를 raw note에 남긴다.

Acceptance criteria:

- 각 daily cluster가 최소 2개 evidence ID를 갖거나, evidence 부족 caveat가 명시된다.
- fallback 사용 시 Discord body에 fallback basis가 보인다.
- report date/title은 KST 기준으로 고정된다.

### P4. 운영 신뢰성/스케줄 보장

목표: “실행됐다”가 아니라 “품질 기준을 만족해 게시됐다”를 확인한다.

작업:

- daily 08:00 KST, newsletter 08:15 KST 실행 결과를 status JSON에 남긴다.
- Discord posting 결과 messages count, source path, rendered char count, HTTP failure를 structured log로 남긴다.
- relay_pull 모드에서 Apps Script `LATEST_BRIEFING_AT`, `item_count`, `query`를 EC2 puller가 기록하고 stale threshold를 알린다.

Acceptance criteria:

- 마지막 성공 시간이 26시간 이상 오래되면 health-check가 실패한다.
- newsletter item_count가 급락하거나 rendered/detailed 비율이 임계값 미만이면 경고한다.
- Discord message splitting은 topic boundary를 보존하고, 링크 줄이 중간에서 어색하게 잘리지 않는다.

## 4. 구현 순서 제안

1. 문서/fixture 먼저: 대표 newsletter fixture를 만들어 233개 수집, 7개 topic, Colab noise, 공개 article text 부족 케이스를 재현한다.
2. Apps Script pure helper 테스트를 Node smoke test로 고정한다.
3. `renderBriefing_` round-robin 다양성 랭킹과 count telemetry를 추가한다.
4. 3줄 요약 schema/품질 gate를 강화한다.
5. EC2/Discord posting health log를 structured status로 보강한다.
6. Daily trend report는 acceptance metric 노출과 source link 안정화 위주로 작게 개선한다.

## 5. 위험과 대응

- 저작권/개인정보 위험: 원문 메일 본문과 article 전문은 Discord에 게시하지 않는다. 공개 URL, 짧은 paraphrase, metadata 중심으로 제한한다.
- 토픽 과다 분할 위험: topic을 늘리는 대신 rendered item budget과 topic cap을 둔다.
- LLM hallucination 위험: LLM 합성은 공개 article text/metadata snippet 안에서만 하며 evidence_source를 남긴다.

- Taxonomy drift 위험: 기존 `skills/paper-recommender/docs/*taxonomy*.md`를 canonical source로 두고 Python/GAS fixture parity를 acceptance gate로 삼는다.
- Apps Script 운영 위험: Node는 `.gs`를 직접 실행하지 못하므로 README에 있는 `node --check` 임시 파일 검증과 pure helper smoke를 유지한다.
- EC2 secret 노출 위험: 보고서와 로그에는 env var 이름만 쓰고 값은 쓰지 않는다.

## 6. 검증 계획

- 정적 검증: Apps Script `node --check` copy, Python `compileall` 또는 pytest collection.
- 단위 테스트: newsletter splitter/topic helper/paper-recommender RSS/source tests, briefing raw render tests.
- fixture E2E: synthetic newsletter items 200개 이상에서 topic diversity, rendered ratio, summary schema를 검증한다.
- 운영 smoke: Discord dry-run 또는 local render에서 message count, char length, section boundary, source links를 확인한다.
- 회귀 확인: KST date, raw.json 우선 렌더링, Colab filter, no secret grep.

## 7. 이번 리뷰의 명시적 한계

- EC2의 실제 `/home/ubuntu/.openclaw/workspace/reports/*latest.md` 파일 내용과 live cron log는 이 작업에서 직접 출력하지 않았다.
- 분석은 제공된 2026-05-05 KST 컨텍스트 스냅샷과 로컬 repo 구현을 근거로 했다.

- Subagent repository-map probe integrated these findings: existing taxonomy docs, Python `newsletter_ingest.py` classifier/rendering path, Apps Script mirrored taxonomy/rendering path, no-secrets boundaries in README/GAS comments/redaction helpers, and relevant regression tests.
- production secret 값은 확인하거나 게시하지 않았다.
