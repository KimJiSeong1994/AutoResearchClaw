# 집현전-Claw 브리핑 품질 빌드 계획 및 인수 기준 — worker-1 — 2026-05-05

## 1. 목적과 범위

이 문서는 `briefing-build-review-20260505T094700KST.md` 지시에 따른 **deep build plan -> implementation/reflection -> code review** 실행 기준이다. worker-1의 소유 범위는 구현 파일 변경이 아니라, 구현 작업자가 따라야 할 계획·스펙·인수 기준을 고정하고 이후 리뷰가 판정할 수 있는 증거 목록을 명확히 하는 것이다.

대상 품질 목표는 다음 네 가지다.

1. 뉴스레터 렌더 다양성: 많은 수집 항목이 소수 토픽/대표 1개로 압축되지 않도록 한다.
2. 관측성: collected/url candidates/blocked/detailed/rendered/dropped 및 topic-level count를 상태나 debug 객체에서 확인 가능하게 한다.
3. 토픽 분류 호환성: 기존 string topic API를 유지하면서 Python/GAS 모두 primary/secondary/confidence/reasons 형태의 상세 결과로 확장한다.
4. 3줄 요약 계약: 각 렌더 항목은 `핵심`, `기술 포인트`, `의미/근거`, `출처 링크`를 갖고, 비공개 메일 본문·긴 원문 인용·토큰을 게시하지 않는다.

## 2. 근거 입력

- 필수 브리핑: `/Users/jiseong/git/AutoResearchClaw/.omx/context/briefing-build-review-20260505T094700KST.md`
- 기존 종합 로드맵: `.omx/reports/briefing-quality-synthesis-roadmap-20260505.md`
- 기존 worker-1 심층 리뷰: `.omx/reports/briefing-quality-review-worker-1-20260505.md`
- 기존 taxonomy 설계 문서:
  - `skills/paper-recommender/docs/newsletter-topic-taxonomy-plan.md`
  - `skills/paper-recommender/docs/newsletter-topic-taxonomy-implementation-design.md`
  - `skills/paper-recommender/docs/current-topic-rules-briefing-evidence.md`

## 3. 빌드 순서

### Phase A — 기준 고정

구현 전 다음 fixture/계측 기준을 먼저 고정한다.

- 200개 이상 뉴스레터 synthetic fixture 또는 pure-helper smoke 입력을 준비한다.
- fixture에는 적어도 다음 케이스를 포함한다.
  - `research paper` vs `search` 오분류 방지
  - RAG/agent/GitHub repo/code 항목의 primary/secondary topic 분리
  - benchmark/market/pricing/privacy regulation 항목
  - Colab, 로그인, 구독, 이미지/utility URL 차단
  - 공개 article text 부족 또는 fetch 실패 항목
- 변경 전 렌더 결과에서 rendered topics/items, topic max share, dropped count를 기록할 수 있어야 한다.

### Phase B — 구현 우선순위

1. **Apps Script 렌더 다양성 + telemetry**
   - `renderBriefing_`가 topic별 `entry.detailed[0]`만 게시하는 구조를 topic round-robin 또는 topic cap 기반으로 바꾼다.
   - topic별 최소 1개, 예산이 허용하면 최대 2-3개를 게시한다.
   - 전체 rendered item 예산과 char budget 탈락 count를 debug/report에 남긴다.
   - topic boundary가 `### ` 기준 Discord splitting과 호환되도록 topic 단위 섹션을 유지한다.

2. **Python/GAS taxonomy parity**
   - Python `classify_topic_result`는 `primary`, `secondary`, `confidence`, `reasons`를 반환한다.
   - 기존 `classify_topic -> str` 호출자는 깨지지 않아야 한다.
   - GAS도 `classifyTopic_` wrapper 호환을 유지하고 내부 상세 분류를 노출한다.
   - reasons/debug에는 normalized term/field/score만 저장하고 원문 메일 본문을 저장하지 않는다.

3. **3줄 요약 contract**
   - 렌더 항목마다 `핵심`, `기술 포인트`, `의미/근거`, `출처 링크` 라인이 존재해야 한다.
   - boilerplate/login/cookie/unsubscribe/marketing-only 문장은 summary 후보에서 제외한다.
   - 공개 원문이 부족한 항목은 “공개 원문 부족/메일 컨텍스트 기반” 상태를 내부 telemetry에 남기고, 가능하면 렌더 우선순위를 낮춘다.

4. **리뷰 및 반영**
   - worker-4 리뷰는 logic/privacy/Discord formatting/tests/operational risk를 검토한다.
   - 작은 결함은 리뷰 lane에서 고치되, 소유 파일 충돌이 있으면 leader에게 scope 확대를 요청한다.

## 4. 인수 기준

### P0/P1 필수 기준

- 수집 200개 이상 fixture에서 rendered topics가 5개 이상이다. 단, detailed topic 수가 5개 미만이면 상태/debug에 그 이유가 보인다.
- detailed topic이 3개 이상일 때 한 topic이 전체 rendered item의 40%를 초과하지 않는다.
- topic별 telemetry는 최소 `total`, `detailed`, `rendered`, `dropped`를 포함한다.
- run-level telemetry는 최소 `collected`, `urlCandidates`, `blocked`, `detailFetchAttempted`, `detailFetchSucceeded`, `rendered`, `dropped`를 포함한다.
- Discord markdown은 topic boundary split-safe 형태를 유지하고, 링크 줄이 중간에서 깨지지 않는다.

### 호환성 기준

- Python 기존 `classify_topic(...) -> str` 호출과 기존 briefing heading/bullet shape는 유지된다.
- GAS 기존 top-level trigger/relay/posting public interface는 유지된다.
- 기존 Colab/private/utility URL 차단은 약화되지 않는다.

### 요약 품질 기준

- 게시되는 각 item은 정확히 다음 역할 라인을 포함한다: `핵심`, `기술 포인트`, `의미/근거`, `출처 링크`.
- summary line은 지나치게 짧은 generic fallback, cookie/login/unsubscribe, 장문 원문 복사를 포함하지 않는다.
- 근거 부족 항목은 조용히 고품질 항목보다 낮은 우선순위가 되거나, 내부 상태에서 근거 부족으로 집계된다.

### 보안/개인정보 기준

- 새 코드/문서에는 실제 Discord webhook, relay token, OpenClaw gateway token, Gmail 원문 본문이 포함되지 않는다.
- 공개 Discord 출력은 공개 URL, 제목, 짧은 한국어 paraphrase, 제한된 metadata 중심이어야 한다.
- debug/reasons 객체에도 원문 메일 본문이나 secret 값은 저장하지 않는다.

## 5. 검증 매트릭스

| 영역 | 권장 검증 | PASS 조건 |
| --- | --- | --- |
| Apps Script syntax | `.gs`를 임시 `.js`로 복사 후 `node --check` | syntax error 0 |
| Apps Script helper smoke | pure helper fixture smoke 또는 기존 README 방식 | rendered topics/items/counts 기대값 충족 |
| Python taxonomy | `uv run --with pytest pytest -q skills/paper-recommender/project/tests/test_newsletter_ingest.py` | 기존 호환 + 상세 결과 fixture 통과 |
| Discord splitting | post_newsletter/bridge 관련 테스트 | topic boundary split 유지, broken link 0 |
| 보안 | `git diff --check` 및 token-like grep | whitespace error 0, 실제 secret 0 |
| 회귀 | 관련 daily/newsletter 테스트 | KST date/raw 우선/Colab filter/fallback warning 유지 |

## 6. 리뷰 체크리스트

- [ ] Topic round-robin/cap 로직이 char budget 초과 시 topic 단위로 안전하게 degrade하는가?
- [ ] `rendered`와 `dropped` count가 실제 출력과 일치하는가?
- [ ] 분류 상세 결과가 원문 본문을 저장하지 않고 normalized evidence만 남기는가?
- [ ] 기존 string classifier API와 GAS trigger/relay entrypoint가 깨지지 않았는가?
- [ ] 3줄 요약은 source-grounded paraphrase이며 장문 복사나 boilerplate가 아닌가?
- [ ] Discord split 후 `###` topic heading, bullet, source link가 깨지지 않는가?
- [ ] 테스트가 fixture 다양성, privacy filter, summary contract를 모두 고정하는가?

## 7. reflection 기준

구현 worker는 완료 보고에 다음을 포함해야 한다.

- 변경 파일 목록과 각 변경의 품질 목표 매핑
- telemetry 예시 또는 fixture output에서 collected/detailed/rendered/dropped count
- rendered topic 수, rendered item 수, max topic share
- 3줄 summary contract PASS 증거
- 보안/개인정보 grep 결과
- 실패했거나 보류한 acceptance와 그 이유

## 8. stop condition

worker-2/worker-3 구현, worker-4 리뷰/검증, 필요한 소규모 fix가 모두 terminal 상태가 된 뒤, 최종 보고서는 한국어로 변경 파일·테스트·리뷰 결과·잔여 리스크를 요약해야 한다. 이 문서의 인수 기준 중 P0/P1 필수 기준이 충족되지 않으면 “완료”가 아니라 “부분 완료/리스크 있음”으로 보고한다.
