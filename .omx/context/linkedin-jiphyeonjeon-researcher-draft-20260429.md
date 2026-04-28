# LinkedIn draft: Jiphyeonjeon from a researcher perspective

## Source scope reviewed

- `.omx/context/jiphyeonjeon-posts-agent-review-20260429.md`
- `.omx/context/jiphyeonjeon-post-search-agent-beyond-single-query-65bcbe5c30fd.md`
- `.omx/context/jiphyeonjeon-post-paper-network-graph-hidden-connections-f954b2866fb4.md`
- `.omx/context/jiphyeonjeon-post-auto-highlight-ai-scholarly-annotation-f6a5ccb4ce6b.md`
- `.omx/context/jiphyeonjeon-post-curriculum-generator-jiphyeonjeon-9fdf6c688749.md`
- `.omx/context/jiphyeonjeon-post-jiphyeonjeon-agent-mcp-tool-surface-a7c9e3d4b821.md`
- `.omx/context/jiphyeonjeon-post-daily-recommendations-research-persona-dailyrec2026.md`

## Polished Korean LinkedIn draft

논문 연구에서 가장 자주 끊기는 순간은 “무엇을 검색할까”보다 “오늘은 어디서 다시 시작할까”라고 느낍니다.

집현전(jiphyeonjeon.kr)을 보며 흥미로웠던 점은 기능을 하나씩 더하는 방식이 아니라, 연구자의 흐름을 이어 주는 작은 도구들을 연결한다는 점이었습니다.

검색 에이전트는 한 번의 쿼리로 끝내지 않고 부족한 관점을 다시 찾습니다. 네트워크 그래프는 인용이 아직 없는 최신 논문도 제목·키워드 유사도로 주변 문헌과 연결합니다. 오토하이라이트는 요약보다 한 단계 더 나아가 평가-근거-제안 구조로 읽을 지점을 좁혀 줍니다. 커리큘럼 생성기는 그럴듯한 가짜 목록 대신 검증된 논문 안에서 학습 순서를 만듭니다.

최근 Agent/MCP 설계와 매일 추천 글에서 보이는 방향도 같습니다. Agent Key, capability 협상, `paper_id` 기반 조회처럼 작고 검증 가능한 도구 표면을 만들고, SOUL·OpenClaw·로컬 baseline을 통해 오늘의 연구 세션을 시작할 후보를 남깁니다.

아직 “완성된 연구 자동화”라기보다, 연구 맥락이 끊기지 않게 만드는 기반에 가깝습니다. 그래서 더 설득력 있습니다. 좋은 에이전트는 연구자를 대신하는 모델이 아니라, 이미 쌓인 검색·북마크·리뷰·추천의 맥락을 안전하게 이어받는 도구라고 생각합니다.

## Alternative hooks

1. 연구에서 정말 어려운 질문은 “무엇을 검색할까”가 아니라 “오늘은 어디서 다시 시작할까”일 때가 많습니다.
2. 좋은 논문 도구는 더 많은 논문을 보여주는 데서 끝나지 않고, 다음 읽기 결정을 더 작고 검증 가능하게 만들어야 합니다.
3. 집현전의 흥미로운 지점은 AI 기능의 과시보다 연구 맥락을 끊기지 않게 만드는 도구 표면에 있습니다.

## Evidence bullets tied to source posts

- Search agent: saved post describes 5 academic databases, multi-turn search, gap analysis, rubric evaluation, and fallback behavior; this supports the draft's “one query is not enough” claim.
- Network graph: saved post explains citation gaps for new papers and uses title plus keyword/category similarity for edges; this supports the “connect papers even before citations exist” claim.
- Auto-highlight: saved post frames the feature around structured peer-review-style comments, not simple summarization; this supports the “evaluation-evidence-suggestion” claim.
- Curriculum generator: saved post documents a four-step pipeline where LLMs design structure but verified OpenAlex/Semantic Scholar results constrain paper selection; this supports the “no plausible fake list” claim.
- Agent and daily recommendations: Agent post documents Agent Key, `/api/version` capability flags, and `paper_id` lookup; daily recommendations post frames SOUL/OpenClaw/local baseline as a daily research-session entry point rather than a perfect recommender.

## Guardrails applied

- Avoided claims that Jiphyeonjeon has a completed full MCP Agent; the Agent post says the current scope is API/auth/tool-surface groundwork.
- Avoided claiming exhaustive literature coverage or perfect recommendation quality; the source posts explicitly discuss fallback paths, benchmark gaps, cold start, sparse feedback, privacy, and future work.
- Kept the LinkedIn draft under 1,200 characters by script verification.
