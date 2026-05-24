---
name: jiphyeonjeon-reporter-article-post
description: 집현전-기자가 수집된 아티클·동향 리포트를 근거 추적 가능한 한국어 기술 블로그 초안으로 반복 생성·검토할 때 사용한다. 공개 초안과 내부 근거 appendix를 분리하고, 환각 방지를 위한 evidence table, claim layering, source confidence, 카드뉴스/디스코드 재사용 블록, dry-run publication boundary를 포함한다.
---

# 집현전-기자 Article Post

수집된 아티클, 뉴스레터, 동향 리포트, source-policy 리서치를 바탕으로 집현전 블로그용 한국어 기술 소개 초안을 만든다. 이 스킬은 **작성 + 내부 근거 보존 + 공개 초안 정리 + 검증**을 하나의 반복 가능한 절차로 고정한다.

## 입력

필수:
- Primary report: 수집 아티클/동향 리포트 경로 1개 이상.
- Topic/title 후보.
- Public source URLs 또는 report 안의 surfaced URLs.

권장:
- Supporting source-policy report.
- Low-confidence/excluded source 목록.
- Target draft path.
- Internal evidence appendix path.

누락된 사실은 추정하지 말고 `확인 필요` 또는 caveat로 남긴다.

## 작성 원칙

1. **Article-content first**: 에이전트/게시 아키텍처 자기소개가 아니라 수집 아티클 내용에서 출발한다.
2. **Public vs internal split**: 공개 초안에는 `.omx`, `workspace/`, 로컬 path:line, PRD/test-spec 경로를 넣지 않는다. 내부 추적성은 appendix에만 둔다.
3. **Two-layer claim model**: 공개 이벤트/기사 주장과 작성자의 해석을 분리한다.
4. **Source confidence**: claim마다 URL, 검증일/상태, confidence, 근거 수준을 둔다.
5. **No hallucination**: 출처 없는 수치, 과도한 일반화, “보장/확정/완료” 표현을 피한다.
6. **Low-confidence sources**: metadata unavailable, provider unavailable, confidence 낮음 자료는 핵심 근거에서 제외하거나 명시적으로 caveat한다.
7. **Publication boundary**: 이 스킬은 초안과 검토 산출물을 만든다. 실제 게시 또는 live API write는 별도 승인/게시 스킬의 dry-run 이후에만 가능하다.

## 산출물 구조

공개 초안은 다음 구조를 따른다.

```markdown
---
title: "..."
slug: "..."
excerpt: "..."
author: "집현전-기자"
tags: ["..."]
reading_time_min: 8
published: false
---

# 제목

대표 이미지 설명: ...

> 3줄 요약
> 1. 핵심 변화
> 2. 산업/조직/연구 의미
> 3. 남는 쟁점

## 먼저 밝히는 근거 범위
## 왜 지금 이 이슈인가
## 주요 용어
## 핵심 주장
## 논증 구조
## 근거 표
## 산업사회학적·현장기반 해석
## 기술적으로 무엇을 봐야 하나
## 앞으로 볼 질문
## 한계와 주의
## 카드뉴스 재사용안
## 디스코드 브리핑 재사용안
## 출처
```

내부 appendix는 공개 초안과 분리해 다음을 보존한다.

```markdown
# Internal Evidence Appendix

| Major claim | Local evidence | Surfaced public URL | Confidence / verification | Layer |
|---|---|---|---|---|
```

## 워크플로우

1. **근거 수집**
   - primary report에서 주장, 수치, 사례, URL, 날짜를 추출한다.
   - supporting report는 source-policy, 수집 경계, confidence 판단에만 사용한다.
   - 저신뢰 자료는 excluded/caveated 목록에 둔다.

2. **Claim map 작성**
   - 각 주요 claim을 `public/event`, `public analysis`, `source policy`, `interpretation` 중 하나로 분류한다.
   - 공개 초안에는 public URL만 넣고, 내부 appendix에는 local path:line을 넣는다.

3. **초안 작성**
   - 첫 화면에서 결론과 독자 가치를 제시한다.
   - 기술 용어는 “주요 용어”에서 짧게 정의한다.
   - 반론과 한계를 반드시 포함한다.
   - 카드뉴스/디스코드 재사용 블록을 포함한다.

4. **검토/수정**
   - 논리성: thesis → evidence → interpretation 흐름.
   - 가독성: 한국어 기술 독자에게 설명되는가.
   - 사실성: claim마다 URL과 confidence가 있는가.
   - 공개성: 내부 경로와 비공개 정보가 공개 초안에 없는가.

5. **검증**
   - `scripts/validate_article_post.py`를 실행한다.
   - 실패하면 초안을 수정하고 다시 실행한다.

## 검증 명령

```bash
python3 .codex/skills/jiphyeonjeon-reporter-article-post/scripts/validate_article_post.py \
  --draft workspace/blog-drafts/<draft>.md \
  --appendix .omx/reports/<appendix>.md
```

검증은 다음을 확인한다.
- 공개 초안 필수 섹션 존재.
- 공개 초안에 내부 path leakage 없음.
- 비밀값/webhook/production publish 주장 없음.
- `published: false` 존재.
- appendix에는 local evidence가 존재.
- 저신뢰 자료가 핵심 근거로 쓰이지 않도록 caveat/exclusion 문구 존재.
