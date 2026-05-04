# 현재 TOPIC_RULES / classifyTopic 점검 및 최신 브리핑 근거

## 목적

이 문서는 현재 뉴스레터 브리핑 토픽 분류 구현을 빠르게 통합할 수 있도록, `TOPIC_RULES`/`classifyTopic_`/`classify_topic`의 실제 동작과 최신 브리핑 근거 확인 결과를 정리한 실행 전 방향안이다.

## 현재 구현 요약

### Google Apps Script 경로

- 파일: `integrations/google-apps-script/newsletter_archive_to_discord.gs`
- 주요 심볼:
  - `TOPIC_RULES`
  - `classifyTopic_(text)`
  - `groupByTopic_(items)`
  - `renderBriefing_(items, query)`
- 현재 고정 라벨:
  1. `검색/RAG/지식그래프`
  2. `LLM/에이전트`
  3. `멀티모달/비전`
  4. `인프라/배포`
  5. `오픈소스/코드`
  6. `AI 안전/평가`
  7. `산업/제품 동향`
- fallback 라벨:
  - `논문/리서치`: `arxiv.org`, `doi.org`, `openreview.net` 신호가 있을 때
  - `기타 테크 리포트`: 그 외
- 동작 방식:
  - lowercase 문자열에 대해 `TOPIC_RULES`를 순서대로 순회한다.
  - 어떤 needle이라도 substring으로 포함되면 첫 번째 매칭 토픽을 반환한다.
  - `collectNewsletterItems_`는 메일 subject/body 또는 공개 article detail/url을 분류 근거로 사용한다.

### Python ingest 경로

- 파일: `skills/paper-recommender/newsletter_ingest.py`
- 주요 심볼:
  - `_TOPIC_RULES`
  - `classify_topic(item)`
  - `group_items_by_topic(items)`
  - `render_topic_briefing(...)`
- 현재 고정 라벨은 Apps Script와 거의 동일하다.
- 동작 방식:
  - `title + kind + url`만 haystack으로 사용한다.
  - `body`는 privacy boundary 때문에 게시되지 않으며, 현재 classifier haystack에도 들어가지 않는다.
  - 그룹은 `(-count, label)` 기준으로 안정 정렬된다.
- 관련 테스트:
  - `skills/paper-recommender/project/tests/test_newsletter_ingest.py`는 ingest/privacy/단일 topic briefing happy path를 검증한다.
  - `skills/paper-recommender/project/tests/test_gmail_newsletter_briefing.py`는 Gmail OAuth/message decode wiring 위주이며 taxonomy/grouping 직접 검증은 없다.

## 최신 브리핑 근거 확인

확인 명령:

```bash
find . -type f \( -iname '*briefing*' -o -iname '*newsletter*latest*' -o -path '*/reports/*' \) \
  -not -path './.git/*' -not -path './.omx/team/*' -not -path './.venv/*' -print | sort | head -n 120
```

현재 worktree에서 확인된 최신 briefing/report 후보:

- `.omx/reports/openclaw-linkedin-medium-trend-research-2026-05-04.md`
- `.omx/reports/dynamic-word-embeddings-sociology-journals-summary.md`
- `.omx/reports/dynamic-word-embeddings-sociology-worker-2.md`
- `skills/paper-recommender/gmail_newsletter_briefing.py`
- `skills/paper-recommender/scripts/newsletter-archive-briefing.sh`
- `integrations/google-apps-script/newsletter_archive_to_discord.gs`

추가로 기본 런타임 경로인 `~/.openclaw/workspace/reports/newsletter-briefing-latest.md`와 `~/.openclaw/workspace/reports`를 확인했으나, 이 worker 환경에서는 읽을 수 있는 최신 뉴스레터 브리핑 파일이 발견되지 않았다.

따라서 이번 점검의 “최신 브리핑 근거”는 repo에 존재하는 2026-05-04 리포트와 현재 브리핑 생성 코드의 출력 구조를 기준으로 한다. 2026-05-04 리포트는 LinkedIn/Medium/RSS/API 기반 trend report 확장 방향을 다루며, 다음 분류 요구를 시사한다.

- 기존 7개 고정 라벨만으로는 `LinkedIn`, `Medium`, `RSS`, `OpenAlex`, `Crossref`, `Semantic Scholar`, 기업 블로그/뉴스 같은 source-class 차이를 충분히 표현하기 어렵다.
- trend report는 “근거 타입, freshness, confidence, compliance/source notes”를 요구하므로 Primary topic 외에 source/evidence/compliance 성격의 Secondary tag가 필요하다.
- `daily_research`의 LLM cluster label과 뉴스레터의 고정 TOPIC_RULES가 별도 체계로 움직이면 topic page와 briefing 섹션이 장기적으로 drift될 수 있다.

## 확인된 오분류 위험

1. **순차 first-match 편향**
   - 앞쪽 라벨이 뒤쪽 라벨보다 항상 우선한다.
   - `rag`는 RAG와 LLM/agent 맥락에 모두 등장할 수 있으나 현재는 규칙 순서에 의해 고정된다.
2. **substring false positive**
   - `search`가 `research` 내부에서 매칭될 수 있다.
   - `market`이 `benchmark` 내부에서 매칭될 수 있다.
   - `agent`가 다른 단어 내부에서 우연히 잡힐 수 있다.
3. **중복 needle 충돌**
   - `benchmark`는 인프라/배포와 AI 안전/평가 양쪽에 의미가 있다.
   - `github.com`은 오픈소스 신호지만, 항목의 핵심이 model/agent/RAG일 수 있다.
4. **Python/GAS 입력 근거 차이**
   - Apps Script는 body/detail/url을 분류 근거로 쓸 수 있다.
   - Python ingest는 `title + kind + url`만 사용하므로 짧은 제목과 풍부한 본문 맥락에서 `기타 테크 리포트`가 증가할 수 있다.
5. **source type과 topic의 혼동**
   - arXiv/OpenReview/GitHub/Medium/LinkedIn은 source 또는 evidence type이지 항상 topic은 아니다.
   - 현재 fallback은 paper source를 `논문/리서치`로 묶지만, 실제 분석 축은 RAG/agent/safety/infra일 수 있다.
6. **장기 topic page drift**
   - daily research는 LLM-generated cluster label을 사용한다.
   - newsletter briefing은 fixed topic label을 사용한다.
   - 같은 트렌드가 서로 다른 페이지/섹션에 분산될 위험이 있다.

## 개선 방향

1. `TOPIC_RULES`를 라벨별 단순 needle list가 아니라 `primary`, `display_name`, `weighted_terms`, `negative_guards`, `secondary_tags`를 가진 spec으로 전환한다.
2. `classifyTopic_`/`classify_topic`은 substring first-match가 아니라 score 기반으로 전환한다.
3. 브리핑 grouping은 Primary label 기준으로 유지하되, 각 항목에 Secondary tags를 표시한다.
4. source/evidence type은 Primary topic과 분리한다.
   - 예: `source=medium_rss`, `evidence=company_blog`, `primary=market_ecosystem_strategy`.
5. Python ingest와 Apps Script의 taxonomy parity fixture를 만든다.
6. 새 classifier는 shadow mode로 기존 topic과 새 primary/secondary를 함께 기록한 뒤 전환한다.

## 바로 필요한 회귀 케이스

- `research methods paper`는 `검색/RAG/지식그래프`로 분류되면 안 된다.
- `benchmark suite for safety eval`은 market/product가 아니라 evaluation/safety 또는 infra/eval로 가야 한다.
- `GitHub repo for RAG agent`는 source=`code`, primary는 핵심 변화 축에 따라 RAG 또는 agent로 결정되어야 한다.
- `OpenAI pricing / enterprise partnership`는 model keyword가 있어도 시장/생태계/전략으로 갈 수 있어야 한다.
- `healthcare privacy regulation`은 버티컬 응용이 아니라 거버넌스/규제로 갈 수 있어야 한다.

## 결론

현재 구현은 브리핑을 빠르게 묶기에는 충분하지만, AI/ML/테크 트렌드 연구용 장기 추적에는 keyword-first 한계가 뚜렷하다. 다음 단계는 production label을 즉시 늘리는 것이 아니라, 공통 taxonomy spec과 회귀 fixture를 먼저 만들고 score 기반 classifier를 shadow mode로 검증하는 것이다.
