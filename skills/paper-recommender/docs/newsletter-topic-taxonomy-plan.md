# 뉴스레터 토픽 분류 개선 방향안

## 목적

AI/ML/테크 트렌드를 추적하는 연구자가 뉴스레터 브리핑을 빠르게 훑고, 같은 변화 축을 장기적으로 추적할 수 있도록 현재 단일 키워드 기반 토픽 분류를 `Primary label + Secondary labels` 구조로 확장한다.

현재 구현은 다음 두 경로에 고정 토픽 규칙을 중복 보유한다.

- `skills/paper-recommender/newsletter_ingest.py` — `_TOPIC_RULES`, `classify_topic`, `group_items_by_topic`, `render_topic_briefing`
- `integrations/google-apps-script/newsletter_archive_to_discord.gs` — `TOPIC_RULES`, `classifyTopic_`, `groupByTopic_`, `renderBriefing_`

두 경로 모두 순차 first-match substring 방식이므로 `search`가 `research`에 걸리거나, `benchmark`가 인프라/평가 양쪽에서 충돌하거나, `rag`가 RAG/LLM 계열에 중복되는 문제가 생길 수 있다.

## 설계 원칙

1. **Primary label은 핵심 변화 축 1개만 선택한다.**
   - 기술명 자체가 아니라 연구자가 추적해야 할 변화 원인을 기준으로 한다.
   - 예: “LLM”이라는 단어가 있어도 핵심이 지식 검색이면 `Data, Knowledge & Retrieval`이다.
2. **Secondary labels는 맥락 보강용으로 복수 부여한다.**
   - 기술 속성, 산업, 가치, 성숙도, 리스크를 함께 남겨 검색성과 분석성을 높인다.
3. **브리핑 그룹은 Primary 기준으로 묶고, 항목 내부에 Secondary를 노출한다.**
   - Discord/Markdown 섹션 수를 안정적으로 유지한다.
   - 세부 맥락은 항목 라인에 `tags:` 형태로 표시한다.
4. **규칙은 substring first-match에서 score 기반으로 전환한다.**
   - 토큰 경계, URL kind, 제목 가중치, 본문/스니펫 가중치를 분리한다.
   - 동점은 명시적 우선순위와 fallback 규칙으로 해결한다.
5. **Python ingest와 Google Apps Script taxonomy는 같은 spec에서 관리한다.**
   - 중복 상수 drift를 줄이기 위해 JSON/YAML spec을 원천으로 두고 각 런타임에 반영한다.

## 권장 Primary taxonomy

| Primary label | 한국어 표시명 | 포함 범위 | 대표 신호 |
| --- | --- | --- | --- |
| `foundation_models` | 파운데이션 모델/모델 연구 | LLM, VLM, diffusion/video, model release, model capability | `llm`, `language model`, `multimodal model`, `reasoning model`, `model release` |
| `agents_automation` | 에이전트/자동화 | tool use, coding agent, browser agent, workflow automation, multi-agent | `agent`, `tool use`, `workflow`, `autonomous`, `coding agent` |
| `data_retrieval_knowledge` | 데이터/RAG/지식검색 | RAG, retrieval, vector DB, search, knowledge graph, data pipeline | `rag`, `retrieval`, `vector database`, `semantic search`, `knowledge graph` |
| `ai_infra_mlops` | AI 인프라/MLOps | inference serving, deployment, evaluation pipeline, observability, latency/cost | `inference`, `serving`, `deploy`, `latency`, `monitoring`, `eval pipeline` |
| `hardware_compute` | 하드웨어/컴퓨트 | GPU/NPU/TPU, HBM, datacenter, edge compute, supply chain | `gpu`, `nvidia`, `hbm`, `accelerator`, `datacenter`, `on-device` |
| `applications_vertical` | 응용/버티컬 AI | healthcare/legal/finance/education 등 산업별 AI 적용 | `healthcare`, `legal`, `finance`, `education`, `manufacturing` |
| `human_ai_interaction` | Human-AI 인터랙션/생산성 | assistant UX, copilot, workspace, interface, user behavior | `assistant`, `copilot`, `workspace`, `ux`, `productivity` |
| `safety_governance_regulation` | 안전/거버넌스/규제 | safety, alignment, red-team, copyright, privacy, security, regulation | `safety`, `alignment`, `regulation`, `copyright`, `privacy`, `security` |
| `market_ecosystem_strategy` | 시장/생태계/전략 | funding, M&A, pricing, partnership, platform competition, enterprise adoption | `funding`, `pricing`, `partnership`, `market`, `enterprise`, `acquisition` |
| `open_source_developer_ecosystem` | 오픈소스/개발자 생태계 | GitHub repo, framework/library, OSS release, developer tooling | `github.com`, `open source`, `framework`, `library`, `developer tool` |
| `emerging_tech_beyond_ai` | AI 인접 신기술 | robotics, spatial computing, quantum, biotech computing, climate tech | `robotics`, `spatial`, `quantum`, `biotech`, `climate tech` |
| `research_paper_general` | 논문/리서치 일반 | paper URL이 있으나 위 카테고리 점수가 낮은 연구 항목 | `arxiv.org`, `openreview.net`, `doi.org` |
| `other_tech_report` | 기타 테크 리포트 | 충분한 신호가 없는 일반 테크 항목 | fallback |

## 권장 Secondary label 체계

Secondary는 3~7개를 권장한다. 정규화된 slug와 사람이 읽는 한국어 표시명을 함께 둔다.

### 기술 속성

- `llm`, `multimodal`, `generative_ai`, `agent`, `rag`, `semantic_search`, `knowledge_graph`
- `edge_ai`, `open_source`, `proprietary_model`, `synthetic_data`, `evaluation`

### 적용 산업/도메인

- `healthcare`, `finance`, `legal`, `education`, `manufacturing`, `media`, `retail`, `public_sector`
- `developer_tools`, `enterprise`, `research_lab`, `startup`

### 가치/영향

- `productivity`, `cost_reduction`, `revenue_growth`, `risk_reduction`, `personalization`
- `automation`, `decision_support`, `developer_velocity`

### 성숙도

- `research`, `prototype`, `early_adoption`, `scaling`, `mainstream`, `hype_risk`

### 리스크

- `privacy`, `security`, `bias`, `hallucination`, `copyright`, `regulatory_risk`
- `vendor_lock_in`, `compute_cost`, `supply_chain`

## 알고리즘 방향

### 1단계: 입력 신호 분리

각 항목에서 다음 필드를 분리해 score를 계산한다.

- `title`: 가장 높은 가중치. 제목의 기술/제품/회사 신호.
- `snippet` 또는 공개 기사 요약: 중간 가중치. 본문 전체를 게시하지 않되 분류 근거로는 사용 가능.
- `url` 및 `kind`: arXiv/OpenReview/GitHub/기업 블로그 등 source type 신호.
- `sender`: 뉴스레터 성격 신호. 단, sender만으로 Primary를 결정하지 않는다.

### 2단계: label별 가중 규칙

- 정확 phrase match: +4
- 토큰 경계 단어 match: +2
- URL host/kind match: +2
- title match 보너스: +1.5
- weak substring match: 기본 비활성화. 필요한 경우 allowlist에만 허용한다.
- negative guard: `research` 내부의 `search`, `benchmark` 내부의 `market`처럼 알려진 false positive는 차단한다.

### 3단계: Primary 결정

1. label별 점수 합산.
2. 최고 점수가 threshold 이상이면 Primary로 선택.
3. 동점이면 다음 우선순위를 적용한다.
   - 명확한 URL kind: `github.com`은 개발자/오픈소스, `arxiv/openreview/doi`는 논문 fallback보다 세부 기술 label 우선.
   - 행위 중심 신호: `agent/tool/workflow`가 강하면 `agents_automation`.
   - 지식 연결 신호: `rag/retrieval/vector/knowledge graph`가 강하면 `data_retrieval_knowledge`.
   - 규제/리스크 신호가 제목에 있으면 `safety_governance_regulation`.
4. threshold 미만이며 paper kind면 `research_paper_general`, 아니면 `other_tech_report`.

### 4단계: Secondary 부여

- Primary에 사용된 핵심 기술 신호도 Secondary에 남긴다.
- 산업/가치/성숙도/리스크는 독립적으로 추출한다.
- 최대 7개로 제한하고 score 순서로 정렬한다.

### 5단계: 출력 grouping

브리핑 섹션은 다음 순서로 정렬한다.

1. Primary group 내 상세 항목 수 descending.
2. 운영 우선순위 label order.
3. 한국어 표시명 alphabetical fallback.

각 항목은 다음 형태가 적합하다.

```markdown
### 데이터/RAG/지식검색
- 핵심 요약: Enterprise RAG adoption patterns
- 기술 포인트: `paper:arxiv`; primary=`data_retrieval_knowledge`; tags=`rag`, `enterprise`, `early_adoption`, `privacy`
- 출처 링크: https://arxiv.org/abs/...
```

## 주요 오분류 리스크와 대응

| 리스크 | 예시 | 대응 |
| --- | --- | --- |
| substring false positive | `research`가 `search`로 잡힘, `benchmark`가 `market`으로 잡힘 | 토큰 경계/phrase matcher 사용, weak substring 금지 |
| rule order bias | `rag`가 RAG와 LLM/agent 양쪽에 존재 | score 합산과 tie-break로 전환 |
| 제품 뉴스와 기술 트렌드 혼동 | “OpenAI pricing change”를 LLM 연구로 분류 | 가격/파트너십/시장 신호가 제목에 강하면 시장/전략 우선 |
| 버티컬 과다 분류 | “healthcare privacy rule”을 응용 AI로 분류 | 규제/리스크 신호가 핵심이면 거버넌스 우선 |
| Python/GAS drift | 두 파일의 `TOPIC_RULES`가 다르게 진화 | 공통 taxonomy spec + parity test 도입 |
| body context 손실 | Python ingest는 title/kind/url만으로 분류 | 게시하지 않는 `classification_text`/`snippet` 필드를 raw에는 최소화하거나 hash/summary로 보관 |
| LLM cluster label drift | daily research topic page가 free-form label로 분산 | canonical primary/secondary를 별도 frontmatter로 추가 |

## 테스트/검증 방향

1. `classify_topic()` 단위 테스트
   - `research paper`가 검색/RAG로 오분류되지 않는지 확인.
   - `benchmark`가 시장/전략으로 오분류되지 않는지 확인.
   - `RAG agent`처럼 중복 신호가 있는 항목에서 tie-break가 예측 가능한지 확인.
2. `group_items_by_topic()` 테스트
   - count desc + label/order tie-break가 안정적인지 확인.
   - unknown/paper fallback이 유지되는지 확인.
3. briefing render 테스트
   - Primary 섹션 + Secondary tags가 출력되는지 확인.
   - `max_items_per_topic` clipping과 remaining 메시지가 유지되는지 확인.
   - 개인정보/본문 미출력 경계가 유지되는지 확인.
4. Python/GAS parity 테스트
   - 동일 fixture에 대해 Primary/Secondary 기대값을 공유한다.
   - GAS는 clasp 없이도 순수 함수 추출 또는 fixture 기반 snapshot으로 검증한다.
5. daily research 연계 테스트
   - LLM cluster label과 canonical Primary가 함께 보존되는지 확인.
   - topic page slug collision 또는 label drift를 회귀 테스트로 잡는다.

## 단계별 구현 제안

1. **Spec 추가**
   - `skills/paper-recommender/topic_taxonomy.json` 또는 YAML에 Primary/Secondary label, match rules, display order를 정의한다.
2. **Python classifier 도입**
   - `newsletter_ingest.py`에 작은 score 기반 classifier를 추가하되 기존 표시명 fallback은 호환 유지한다.
3. **회귀 테스트 선행**
   - 현재 known false positive와 grouping order 테스트를 먼저 추가한다.
4. **브리핑 출력 확장**
   - 섹션은 Primary 표시명, 항목은 `tags=` Secondary로 확장한다.
5. **GAS parity 반영**
   - 같은 spec에서 생성하거나 수동 동기화 체크를 추가한다.
6. **운영 rollout**
   - 1주일 shadow mode로 기존 label과 새 Primary/Secondary를 함께 저장해 차이를 비교한다.
   - 오분류 샘플을 taxonomy fixture에 누적한 뒤 기본 경로를 새 classifier로 전환한다.

## 다음 실행 우선순위

1. Python `newsletter_ingest.py`에 taxonomy fixture 기반 tests를 먼저 추가한다.
2. `search/research`, `market/benchmark`, `rag/agent`, `github+llm` 충돌 케이스를 최소 회귀셋으로 고정한다.
3. classifier 결과 타입을 `primary`, `primary_display`, `secondary`, `confidence`, `reasons`로 확장한다.
4. 기존 `topic` 필드는 하위 호환을 위해 `primary_display`를 넣고, 새 필드는 선택적으로 raw/briefing에 추가한다.
5. GAS 경로는 Python spec이 안정화된 뒤 parity fixture로 따라간다.
