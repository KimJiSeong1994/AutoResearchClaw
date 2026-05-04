# Research: OpenClaw paper-SOUL 기반 LinkedIn/Medium/논문·기업블로그·뉴스 최신 동향 리포트 방안

- 기준일: 2026-05-04
- Worker: worker-1 / researcher
- Request Type: Comprehensive research
- 산출물 성격: 구현 전/구현용 리서치 설계서. 근거(문서·코드·공식 약관)와 추론(권장 아키텍처·우선순위)을 분리한다.

## 1. Direct Answer

OpenClaw에는 이미 `paper-recommender` 중심의 개인화 기반이 있다. 이를 확장하는 가장 안전한 방안은 **SOUL/프로필/북마크에서 seed topic을 만들고, 공식 API·RSS·OAI-PMH·bulk 데이터만 수집한 뒤, OpenClaw embedding/LLM으로 클러스터링·심층 리서치·한국어 리포트를 생성하는 파이프라인**이다.

핵심 결론:

1. **LinkedIn은 자동 스크래핑 대상에서 제외**한다. 공식 API 승인/계약 범위, 사용자가 직접 제공한 URL, 또는 공개 링크의 메타데이터 수준만 다룬다.
2. **Medium은 공식 RSS를 우선 채널**로 둔다. 신규 API 통합은 보수적으로 “불가/제한”으로 취급하고 RSS URL 단위로 수집한다.
3. **논문 메타데이터는 arXiv + Crossref + OpenAlex + Semantic Scholar 조합**이 적합하다. 단, Semantic Scholar는 public display attribution 요구를 별도 필드로 기록한다.
4. **기업 블로그/뉴스는 RSS/공식 API/사이트별 약관 확인 후 최소 수집**한다. 본문 전문 재게시가 아니라 제목·요약·URL·수집일·근거 링크 중심으로 리포트한다.
5. 최종 리포트는 “근거”와 “해석/추론”을 분리하고, 모든 항목에 `원문 URL`, `수집 경로`, `수집일`, `식별자(DOI/arXiv/OpenAlex/S2/Crossref/URL)`를 붙인다.

## 2. Repo / OpenClaw 근거 매핑

### 2.1 현재 개인화 데이터 모델

근거 파일:

- `skills/paper-recommender/README.md`
- `skills/paper-recommender/project/config.example.yaml`
- `skills/paper-recommender/project/src/paper_recommender/config.py`
- `skills/paper-recommender/project/src/paper_recommender/state.py`
- `skills/paper-recommender/project/src/paper_recommender/profile.py`
- `skills/paper-recommender/project/src/paper_recommender/soul.py`
- `skills/paper-recommender/project/src/paper_recommender/pipeline.py`
- `skills/paper-recommender/project/src/paper_recommender/daily_research.py`

현재 repo의 `paper-recommender`는 다음 신호를 이미 가진다.

| 신호 | 현재 위치/흐름 | 리포트 확장 의미 |
|---|---|---|
| 집현전 bookmarks | README의 Profile/Candidates 설명, `pipeline.py`, `daily_research._build_seed_topics()` | 사용자의 최근 논문 관심사를 seed topic으로 사용 |
| structured profile | `state/profile.json` | 관심분야/키워드/methodology 기반 검색 쿼리 생성 |
| narrative profile | `state/profile.md` | 신규 SOUL bootstrap 및 리포트 설명문 생성 |
| per-user SOUL | `state/souls/{user_id}.md` | 장기 관심사, recurring obsessions, blind spots를 클러스터 선택 기준으로 사용 |
| feedback markers | README `[read]`, `[dislike: ...]`, `pipeline._collect_feedback()` | 사용자가 읽은/싫어한 항목을 suppress/filter/evolve 신호로 사용 |
| A/B log | `state/ab_log.jsonl` | SOUL vs keyword가 추천을 실제로 바꾸는지 Jaccard로 검증 |

구체 schema 근거:

- `state/profile.json`: `source`, `interests`, `keywords`, `methodology_focus`, `bookmark_count`, `built_at`.
- `state/profile.md`: `Research focus`, `Methodology stance`, `Recurring themes`, `Exploration frontier`.
- `state/souls/{user_id}.md`: `Research trajectory`, `Methodology stance`, `Recurring obsessions`, `Blind spots`, `Suppress keywords`, `Changelog`.
- `state/soul_meta.json`: user별 `last_update`, `last_bookmark_id`.
- runtime state/log: `seen.json`, `ab_log.jsonl`, `runs.jsonl`, `feedback_log.jsonl`, `weekly_seen.json`, `weekly_reports.jsonl`, `deep_seen.json`, `last_run_status.json`.
- `CandidateItem`: `source`, `title`, `url`, `abstract`, `authors`, `year`, `venue`, `arxiv_id`, `doi`, `tags`, `score`, `fetched_at`.

### 2.2 현재 수집/리서치 파이프라인

`config.example.yaml`의 `daily_research`는 이미 multi-source daily research를 정의한다.

현재 활성 소스:

- `arxiv`
- `hackernews`
- `jiphyeonjeon`
- `huggingface_papers`

현재 옵션/미래 후보:

- `google_newsletters`: 로컬 mbox export 기반. Gmail 실시간 접근이 아니라 로컬 export 경계로 설계됨.
- 주석상 Phase B.5 후보: `semantic_scholar`, `github_trending`, `rss`.

`daily_research.py` 흐름:

1. `JiphyClient.list_bookmarks()` + `profile.seed_topics`로 seed topics 구성
2. `_build_adapters()`로 source adapter 구성
3. `fetch_all_sources()`로 source별 병렬 fetch 및 실패 격리
4. `_merge_and_dedupe()`로 arXiv ID / DOI / normalized title dedupe
5. OpenClaw-compatible embeddings로 clustering
6. SOUL profile 기반 LLM cluster selection
7. `run_deep_for_clusters()`로 AutoResearchClaw 심층 실행
8. `daily-research.md`, `daily-research-papers.md`, `daily-research-raw.json`, `last_run_status.json` 생성

관련 command/test 표면:

- commands: `project/scripts/run_daily.sh`, `project/scripts/run_weekly.sh`, `project/scripts/run_daily_research.sh`, `skills/paper-recommender/run-once.sh`, `sync-results.sh`, `install-cron.sh`, `status.sh`, `health-check.sh`.
- tests: `test_rerank_and_artifacts.py`, `test_daily_research.py`, `test_daily_note.py`, `test_config_daily_research.py`, `test_state_deep_seen.py`, `test_cli_daily_research.py`, `test_source_jiphyeonjeon_adapter.py`, `test_sources_protocol.py`.

### 2.3 현재 구현 갭

근거:

- `sources/__init__.py`는 adapter contract만 제공하고 comments에 `semantic_scholar`, `github_trending`, `rss`를 후보로 언급한다.
- 실제 adapter 파일은 `arxiv.py`, `hackernews.py`, `huggingface_papers.py`, `jiphyeonjeon.py`, `google_newsletters.py`뿐이다.

갭:

1. Medium RSS 전용 adapter 없음.
2. 일반 RSS/Atom adapter 없음.
3. OpenAlex/Crossref/Semantic Scholar adapter 없음.
4. LinkedIn은 약관상 “수집 adapter”가 아니라 승인 API/수동 URL/공식 export only policy layer가 필요.
5. `CandidateItem`에는 현재 `collection_method`, `license`, `attribution_required`, `source_terms_url` 같은 compliance metadata가 없다.

## 3. Official Docs Evidence

### 3.1 LinkedIn

- [LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement) — 2025-11-03 효력. 스크래핑/복사 수단, 비인가 봇·자동화 접근, 무단 복제·배포를 금지하는 조항이 있다.
- [LinkedIn API Terms of Use](https://www.linkedin.com/legal/l/api-terms-of-use) — API/Content 사용은 승인·약관·문서 범위 내에서만 가능하고, 필요한 최소 Content만 요청해야 하며, LinkedIn data/content의 제3자 제공·재배포·reports/scores 특정 용도 사용 제한을 둔다.
- [LinkedIn Developer Product Catalog](https://developer.linkedin.com/product-catalog) 및 [Community Management API Overview](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview?view=li-lms-2026-01) — 커뮤니티 관리/회사 페이지 API는 vetted product 성격이고 승인·tier가 필요하다.

판단: LinkedIn은 “웹 자동수집 소스”가 아니라 **승인 API/사용자 제공 링크/수동 큐레이션/서면 허가 채널**로 취급해야 한다.

### 3.2 Medium

- [Medium RSS feeds](https://help.medium.com/hc/en-us/articles/214874118-Using-RSS-feeds-of-profiles-publications-and-topics) — profile, publication, custom domain, tagged page, topic page RSS URL scheme을 공식 제공한다. Paywall story는 RSS full story로 제공되지 않는다.
- [Medium API Terms of Use](https://help.medium.com/hc/en-us/articles/214151487-Medium-API-Terms-of-Use) — API 사용 조건, UGC 권리, cache/store 제한, 서버 안정성/rate limiting 가능성을 명시한다.
- [Medium Terms of Service](https://policy.medium.com/medium-terms-of-service-9db0094a1e0f) — 타인의 콘텐츠는 권리가 있을 때만 copy/download/share해야 한다.

판단: Medium은 **RSS 우선**, 본문 전문 저장 금지/최소 저장, 원문 링크 중심 요약이 안전하다.

### 3.3 arXiv

- [arXiv API Terms of Use](https://info.arxiv.org/help/api/tou.html) — descriptive metadata는 CC0로 자유롭게 재사용 가능하지만, e-print 원문 재배포는 논문별 라이선스/권리자 허가가 필요하다. legacy API/OAI-PMH/RSS는 3초당 1요청·단일 연결 제한을 둔다.
- [arXiv API User Manual](https://info.arxiv.org/help/api/user-manual.html) — API endpoint/query/Atom 응답 사용법을 제공한다.

현재 repo 반영: `sources/arxiv.py`는 `https://export.arxiv.org/api/query`를 쓰고, `_REQUEST_DELAY_SEC = 3.0`으로 ToS의 3초 지연을 반영한다.

### 3.4 Semantic Scholar

- [Semantic Scholar API Overview](https://www.semanticscholar.org/product/api) — Academic Graph/Recommendations/Datasets API를 제공하고 rate limit/API key 권장사항을 설명한다.
- [Semantic Scholar API License](https://www.semanticscholar.org/product/api/license) — S2 Data는 해당 데이터 라이선스와 third-party content license를 준수해야 하며, Semantic Scholar API/Data 기여분에는 attribution을 요구한다.

판단: Semantic Scholar는 논문 추천/인용 관계 보강에 유용하지만, 공개 리포트에는 attribution 필드를 반드시 남긴다.

### 3.5 OpenAlex

- [OpenAlex API Overview](https://developers.openalex.org/api-reference/introduction) — base URL, free API key, Works/Authors/Sources/Topics 등 entity, DOI/ORCID/ROR/PMID lookup을 설명한다.
- [OpenAlex About](https://help.openalex.org/hc/en-us/articles/24396686889751-About-us) — dataset은 CC0이며 무료 재사용 가능하다고 설명한다.

판단: OpenAlex는 법적 장벽이 낮은 scholarly metadata backbone으로 적합하다. DOI/OpenAlex ID/수집일을 리포트에 남긴다.

### 3.6 Crossref

- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) — 공개 REST API, Crossref member/trusted sources가 deposited한 metadata, JSON 응답, DOI 기반 조회를 설명한다.
- [Crossref Access and Authentication](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/) — public/polite/plus pool, polite `mailto`/User-Agent, 429 backoff, rate/concurrency limit을 설명한다.

판단: Crossref는 DOI metadata 검증·보강용으로 적합하다. production에는 polite pool을 기본으로 둔다.

### 3.7 News / company blogs / RSS 일반

- [RFC 9309 Robots Exclusion Protocol](https://www.rfc-editor.org/rfc/rfc9309) — robots.txt는 crawler 규칙이다. 접근 허가서로 해석하면 안 된다.
- [NewsAPI Terms](https://newsapi.org/terms) 및 [NewsAPI Docs](https://newsapi.org/docs) — API key, plan 제한, 저작권/attribution/재게시 제한을 둔다. Developer plan은 development/testing 용도다.

판단: 일반 뉴스/기업 블로그는 공식 RSS/Atom/API 우선, 없으면 사이트별 ToS와 robots.txt를 확인하고 제목·URL·짧은 요약 중심으로 제한한다.

## 4. 권장 수집 전략 매트릭스

| Source class | 권장 채널 | 금지/주의 | 저장 허용 기본값 | 리포트 표기 |
|---|---|---|---|---|
| LinkedIn posts/articles | 승인 API, 사용자 제공 URL, 수동 큐레이션 | 비인가 scraping/bot/copy 금지 | URL, 작성자/제목/게시일 등 최소 metadata만 | “LinkedIn 웹 대량수집 없음; 사용자 제공/승인 API 범위” |
| Medium | RSS feed | paywall 전문 저장, UGC 장기 cache, 신규 API 가정 | RSS item title/link/date/summary snippet | Medium RSS URL + 수집일 |
| arXiv | API/OAI-PMH/RSS/bulk | 3초/요청 단일 연결 준수, PDF 재배포 주의 | metadata/abstract/link/arXiv ID | arXiv ID + abs URL + license note |
| Semantic Scholar | API/Datasets | attribution, third-party license 확인 | metadata/citation counts/links | S2 URL + attribution note |
| OpenAlex | API/snapshot | API key/quota 준수 | CC0 metadata | OpenAlex ID/DOI + 수집일 |
| Crossref | REST API polite pool | rate/concurrency/backoff | DOI metadata | DOI + Crossref API + 수집일 |
| 기업 블로그 | RSS/Atom/API | ToS/robots 위반 HTML crawling 금지 | title/link/date/snippet | site/feed URL + fetched_at |
| 뉴스 | 공식 API/RSS/라이선스 | 전문 재게시, 무료 dev plan production 사용 | headline/link/source/date/snippet | provider/source/original URL |
| Hacker News / HF Daily Papers | 현재 repo adapter | 공식 약관/endpoint 안정성 확인 필요 | hot-list metadata | source URL + adapter source |

## 5. 한국어 최종 리포트 구조 제안

```markdown
# [YYYY-MM-DD] OpenClaw Research Trend Brief

## 0. 기준/범위
- 기준일, 수집 기간, 사용한 source classes
- 법적 경계: LinkedIn scraping 없음 / RSS·API·OAI 중심

## 1. Executive Summary
- 오늘의 핵심 동향 3-5개
- 사용자 SOUL과의 연결: 왜 이 사용자가 봐야 하는가

## 2. Evidence Table
| Claim | Evidence type | Sources | Freshness | Confidence |
|---|---|---|---|---|

## 3. Trend Clusters
### Cluster A: ...
- 근거: 논문/블로그/뉴스/Medium RSS 항목
- 해석: 업계/학계에서 의미하는 바
- 반례/불확실성
- 추천 액션: 읽기/북마크/딥리뷰/추적 키워드

## 4. Source Notes & Compliance
- LinkedIn: 수집 없음/승인 API/사용자 제공 URL 여부
- Medium: RSS URL 목록
- arXiv/OpenAlex/Crossref/S2 attribution

## 5. Next Watchlist
- 다음 주 추적할 query/topic/company/paper
- SOUL 업데이트 후보
```

## 6. 자동화 아키텍처 제안

### 6.1 최소 변경 v1

현재 `daily_research`를 유지하고 source adapter만 확장한다.

1. `RSSAdapter`
   - 입력: `daily_research.sources.rss_feeds`
   - 출력: `CandidateItem(source="rss", title, url, abstract/snippet, authors, year, venue, tags)`
   - 방어: feed URL allowlist, item body cap, HTML strip, `fetched_at`, feed URL 기록
2. `MediumRSSAdapter`
   - Medium 공식 RSS URL scheme만 생성/허용
   - paywall full story 없음을 전제로 title/link/summary만 저장
3. `OpenAlexAdapter`
   - seed topics로 `/works?search=...&filter=from_publication_date` 또는 topic filters
   - DOI/OpenAlex ID/source fields 저장
4. `CrossrefAdapter`
   - DOI enrichment 또는 query.bibliographic fallback
   - polite `mailto`와 identifying User-Agent 필수
5. `SemanticScholarAdapter`
   - 추천/인용/abstract 보강
   - `attribution_required=true`, `source_terms_url` 저장
6. `LinkedInPolicyChannel`
   - 자동 fetch adapter가 아님
   - 사용자가 제공한 URL/승인 API 결과만 ingest
   - 모든 LinkedIn 항목에 `collection_method=manual_or_authorized_api` 기록

### 6.2 데이터 모델 확장

`CandidateItem`에 다음 metadata를 추가하는 것이 좋다.

```python
collection_method: str | None  # rss/api/oai/manual/authorized_api/bulk
source_terms_url: str | None
license: str | None
attribution_required: bool = False
original_url: str | None
fetched_at: datetime
```

추론: 이 필드들은 기능에는 필수는 아니지만, 최종 리포트의 재현성·법적 검토·출처 표기를 자동화한다.

### 6.3 검증 로직

리포트 생성 전 `compliance_gate`를 둔다.

- LinkedIn item인데 `collection_method`가 `manual` 또는 `authorized_api`가 아니면 fail
- Medium item인데 `collection_method != rss`이면 warning/fail
- Semantic Scholar item이면 attribution note 필수
- Crossref production query이면 `mailto`/User-Agent 없을 때 fail
- arXiv adapter delay가 3초 미만이면 fail
- 뉴스 provider가 dev-only plan인데 production flag이면 fail

## 7. 검증 체크리스트

### Source compliance

- [ ] LinkedIn: 자동 scraping 없음. 승인 API/사용자 제공 URL만 사용.
- [ ] Medium: 공식 RSS URL scheme만 사용.
- [ ] arXiv: 3초/요청, 단일 연결, PDF 재배포 없음.
- [ ] Crossref: polite `mailto`, User-Agent, 429 backoff.
- [ ] OpenAlex: API key/quota 확인, CC0 metadata 표기.
- [ ] Semantic Scholar: attribution 및 data license note 포함.
- [ ] 뉴스/블로그: RSS/API/ToS/robots 확인, 전문 재게시 없음.

### Data quality

- [ ] DOI/arXiv/OpenAlex/S2/URL dedupe.
- [ ] source별 fetched_at 저장.
- [ ] 동일 claim에 최소 2개 독립 근거 또는 confidence 낮춤.
- [ ] LLM 요약과 원문 evidence table 분리.
- [ ] paywall/원문 접근 제한 표시.

### Pipeline health

- [ ] `last_run_status.json`의 `candidate_count`, `cluster_count`, `deep_success_count`, `used_fallback` 확인.
- [ ] `daily-research-raw.json`에 source_stats와 cluster items 포함.
- [ ] `daily-research.md`와 `daily-research-papers.md` 생성 확인.
- [ ] SOUL 기반 cluster selection이 keyword baseline과 다른지 `ab_log.jsonl`로 관찰.

## 8. 실행 로드맵

### 0-1일: 정책 안전선 고정

- LinkedIn scraping 금지 정책을 README/config docs에 명시.
- Medium RSS만 허용하는 source policy 작성.
- `CandidateItem` compliance metadata 설계.

### 1-3일: 수집 adapter v1

- Generic RSS/Atom adapter 구현.
- Medium RSS adapter는 generic RSS의 preset으로 구현.
- OpenAlex/Crossref metadata enrichment 추가.

### 3-5일: 논문/업계 교차검증

- Semantic Scholar adapter 추가.
- Crossref/OpenAlex/S2/arXiv ID dedupe 강화.
- 기업 블로그 RSS allowlist 구성.

### 1주: 리포트 품질 게이트

- Evidence table + interpretation 분리 template 적용.
- compliance_gate 테스트 추가.
- source별 실패/쿼터/429 backoff 상태를 `last_run_status.json`에 추가.

### 2주: 운영 평가

- 5-14일 daily runs 관찰.
- SOUL vs keyword Jaccard, source coverage, deep success ratio, false-positive clusters 평가.
- 너무 noisy한 source는 query expansion 또는 allowlist를 조정.

## 9. Caveats / Ambiguity Flags

- LinkedIn API의 접근 범위는 제품/tier/승인 상태에 따라 달라진다. 승인 없이 feed/post 검색 API가 있다고 가정하면 안 된다.
- Medium RSS는 공식이지만 paywall full story는 RSS에 포함되지 않는다. 본문 전문 분석을 기대하면 안 된다.
- OpenAlex/Crossref는 metadata가 개방적이지만 abstract/full text의 권리는 원천 source별로 다를 수 있다.
- Semantic Scholar는 데이터별 third-party license가 섞일 수 있어 공개 리포트 attribution이 필수다.
- NewsAPI 같은 상용 news aggregator는 plan별 production 제한과 재게시 제한이 있으므로 무료 dev key를 cron production에 쓰면 안 된다.

## 10. Subagent Evidence

- Subagents spawned: 2
  - `019df188-93b7-76a0-97ce-f5f8eba2d237`: repo/OpenClaw mapping. Integrated schema/file/pipeline/test-command details into section 2.
  - `019df188-ecf9-77c2-b8c3-27d817ef73cd`: external legal/source evidence. Integrated LinkedIn/Medium/arXiv/Semantic Scholar/OpenAlex/Crossref/RSS guidance.
- Subagent model requested: `gpt-5.4-mini`
- Findings integrated:
  - LinkedIn: avoid unauthorized scraping; use API/permission/manual URL only.
  - Medium: RSS-first; API/new integrations constrained.
  - arXiv/OpenAlex/Crossref/Semantic Scholar: metadata/API paths and attribution/license cautions.
- Serial searches before spawn: 0 substantive repo searches after task claim; subagents were spawned immediately after initial task claim setup.

## 11. Reusable Takeaway

OpenClaw의 기존 `paper-recommender`/`daily_research`는 SOUL 기반 개인화와 multi-source clustering/deep-research skeleton을 이미 갖고 있다. 다음 단계는 새 LLM 프롬프트가 아니라 **합법적 source adapter + compliance metadata + evidence/interpretation 분리 리포트 템플릿**이다. LinkedIn은 수집하지 말고 승인/수동 URL 채널로만 다루며, Medium은 RSS, 논문은 arXiv/OpenAlex/Crossref/Semantic Scholar, 기업·뉴스는 RSS/API allowlist로 제한하면 2026-05-04 기준으로 가장 안전하고 구현 가능한 설계가 된다.
