# Worker 1 Foundational / Citation Map: Dynamic Word Embeddings

- 작성자: `worker-1` / researcher
- 작성일: 2026-05-02 KST
- 범위: dynamic word embeddings / diachronic word embeddings / temporal word embeddings의 **foundational·citation 영향력 자료** 중심 보강.
- 병합 위치: `.omx/research-archive/dynamic-word-embeddings/README.md`의 핵심 자료 목록에 대한 Worker 1 appendix.
- Evidence 기준: 공식 논문 페이지(ACL Anthology, PMLR, ACM/WWW, Cambridge, PNAS), DOI/arXiv, OpenAlex 인용 스냅샷(2026-05-02 조회).

## Evidence vs. interpretation

- **Evidence**: 제목, 저자, 연도, venue, DOI/arXiv/URL, 공식 abstract/metadata에 나타난 기여, OpenAlex cited_by_count.
- **Interpretation**: Jiseong/집현전/OpenClaw/SOUL 연구자 페르소나·개인화 추천에 대한 연결, 읽기 우선순위.
- **주의**: 인용 수는 DB별로 달라지는 스냅샷이다. 방법론 세부·벤치마크·도구는 Worker 2 영역과 병합 필요.

## Core reading order (Worker 1)

1. Harris (1954) / Firth (1957) — distributional semantics의 개념 원류.
2. Traugott & Dasher (2001/2002) — 의미 변화의 이론적 규칙성.
3. Sagi, Kaufmann & Clark (2009/2011/2012) — LSA 기반 pre-neural bridge.
4. Kim et al. (2014) → Kulkarni et al. (2015) → Hamilton et al. (2016) — 초기 temporal/diachronic embedding 핵심 축.
5. Bamler & Mandt (2017) → Rudolph & Blei (2018) → Yao et al. (2018) — dynamic trajectory / alignment 모델링.
6. Dubossarsky et al. (2017) — 법칙/거리 측정의 bias-control 경고.
7. Kutuzov et al. (2018) / Tang (2018) — survey/review gateway.

## Annotated foundational cards

### P1. Harris (1954), “Distributional Structure”

- DOI/URL: https://doi.org/10.1080/00437956.1954.11659520
- Evidence: distributional analysis의 고전. 언어 요소를 주변 분포로 분석하는 관점을 정식화.
- Interpretation: SOUL/연구자 관심사를 고정 키워드가 아니라 동반 문맥의 분포로 정의하는 출발점.
- 읽기 우선순위: P1-background.

### P1. Firth (1957), “A Synopsis of Linguistic Theory, 1930–1955”

- URL: https://books.google.com/books/about/A_Synopsis_of_Linguistic_Theory_1930_195.html?id=T8LDtgAACAAJ
- Evidence: contextual meaning / “company it keeps” 계열의 대표적 원류.
- Interpretation: 개인 연구자의 관심어도 논문·메모·인용 문맥 속에서 의미가 정해진다는 archive 서문용 근거.
- 읽기 우선순위: P1-background.

### P1. Traugott & Dasher (2001/2002), “Regularity in Semantic Change”

- DOI/URL: https://doi.org/10.1017/CBO9780511486500
- OpenAlex cited_by_count: 1701 (2026-05-02 조회)
- Evidence: 의미 변화가 recurring pragmatic/inferential paths를 따른다는 historical semantics 고전.
- Interpretation: embedding 궤적을 “거리”로만 보지 않고 사회·담화적 의미 변동으로 해석하게 해준다.
- 읽기 우선순위: P1-theory anchor.

### P1. Sagi, Kaufmann & Clark (2009/2011/2012), LSA 기반 의미 변화 추적

- ACL 2009 URL: https://aclanthology.org/W09-0214/
- Chapter DOI/URL: https://doi.org/10.1515/9783110252903.161
- Evidence: Latent Semantic Analysis/semantic density로 시간에 따른 word meaning 변화를 통계적으로 추적.
- Interpretation: 작은 한국어 역사 코퍼스나 sparse 개인 로그에서는 neural embedding 이전의 matrix/LSA baseline이 유용할 수 있다.
- 읽기 우선순위: P1-bridge.

### P1. Kim, Chiu, Hanaki, Hegde & Petrov (2014), “Temporal Analysis of Language through Neural Language Models”

- DOI/URL: https://doi.org/10.3115/v1/W14-2517 / https://aclanthology.org/W14-2517/
- OpenAlex cited_by_count: 279 (2026-05-02 조회)
- Evidence: Google Books Ngram으로 연도별 word vector를 얻고 1900–2009 사이 변화 단어와 변화 시점을 탐지.
- Interpretation: 날짜별 SOUL/키워드 벡터 이동량을 novelty나 interest drift 신호로 쓰는 초기 모델.
- 읽기 우선순위: P1.

### P1. Kulkarni, Al-Rfou, Perozzi & Skiena (2015), “Statistically Significant Detection of Linguistic Change”

- DOI/URL: https://doi.org/10.1145/2736277.2741627
- Author page: https://www.alrfou.com/publication/semantic_shift/
- OpenAlex cited_by_count: 393 (2026-05-02 조회)
- Evidence: property time series + change point detection으로 statistically significant linguistic shift를 탐지.
- Interpretation: SOUL drift를 “거리 변화”가 아니라 유의한 전환점으로 판정하는 설계에 필요.
- 읽기 우선순위: P1.

### P1. Hamilton, Leskovec & Jurafsky (2016), “Diachronic Word Embeddings Reveal Statistical Laws of Semantic Change”

- DOI/URL: https://doi.org/10.18653/v1/P16-1141 / https://aclanthology.org/P16-1141/
- arXiv: https://arxiv.org/abs/1605.09096
- OpenAlex cited_by_count: 847 (2026-05-02 조회)
- Evidence: PPMI/SVD/word2vec를 역사 코퍼스에 적용하고 frequency/polysemy 기반 semantic change laws를 제안.
- Interpretation: 한국 현대사 핵심 개념어와 연구자 관심어의 시간 궤적을 정량화하는 최우선 기준 논문.
- 읽기 우선순위: P1-top.

### P1. Bamler & Mandt (2017), “Dynamic Word Embeddings”

- URL: https://proceedings.mlr.press/v70/bamler17a.html
- arXiv: https://arxiv.org/abs/1702.08359
- Evidence: latent diffusion process와 variational inference(skip-gram smoothing/filtering)로 time-stamped corpus의 word/context trajectory를 joint training.
- Interpretation: SOUL을 discrete snapshots가 아니라 continuous latent trajectory로 보는 가장 직접적인 dynamic embedding 참고점.
- 읽기 우선순위: P1.

### P1. Dubossarsky, Weinshall & Grossman (2017), “Outta Control: Laws of Semantic Change and Inherent Biases in Word Representation Models”

- DOI/URL: https://doi.org/10.18653/v1/D17-1118 / https://aclanthology.org/D17-1118/
- OpenAlex cited_by_count: 138 (2026-05-02 조회)
- Evidence: proposed laws of semantic change가 representation artifact일 수 있음을 control condition과 analytical proof로 경고.
- Interpretation: 한국어 역사 코퍼스/개인 로그는 시기별 크기·장르·수집 편향이 크므로 semantic drift 해석 전 bias-control이 필수.
- 읽기 우선순위: P1-safety.

### P2. Szymanski (2017), “Temporal Word Analogies”

- DOI/URL: https://doi.org/10.18653/v1/P17-2071 / https://aclanthology.org/P17-2071/
- Evidence: “word at time A is like word at time B”라는 temporal analogy로 lexical replacement를 탐지.
- Interpretation: “1980년대의 X가 오늘의 Y와 같다”는 설명 가능한 역사 개념 추천 문구를 만들 때 유용.
- 읽기 우선순위: P2.

### P1/P2. Yao, Sun, Ding, Rao & Xiong (2018), “Dynamic Word Embeddings for Evolving Semantic Discovery”

- DOI/URL: https://doi.org/10.1145/3159652.3159703
- arXiv: https://arxiv.org/abs/1703.00607
- Evidence: time-aware word vector를 학습하면서 alignment problem을 동시에 해결하는 WSDM 2018 dynamic statistical model.
- Interpretation: 시기별 독립 embedding을 사후 정렬하는 대신 모델 안에서 정렬 가능성을 높이는 접근으로, 장기 한국 현대사/논문 코퍼스에 중요.
- 읽기 우선순위: P1/P2.

### P1. Rudolph & Blei (2018), “Dynamic Embeddings for Language Evolution”

- DOI/URL: https://doi.org/10.1145/3178876.3185999
- PDF: https://www.cs.columbia.edu/~blei/papers/RudolphBlei2018.pdf
- OpenAlex cited_by_count: 133 (2026-05-02 조회)
- Evidence: exponential family embeddings를 sequential latent variables로 확장하고 U.S. Senate, ACM abstracts, arXiv ML papers를 분석.
- Interpretation: 연구 논문 코퍼스 자체를 dynamic embedding 대상으로 삼았다는 점에서 OpenClaw 연구추천 맥락과 특히 가깝다.
- 읽기 우선순위: P1.

### P1-gateway. Kutuzov, Øvrelid, Szymanski & Velldal (2018), “Diachronic word embeddings and semantic shifts: a survey”

- URL: https://aclanthology.org/C18-1117/
- arXiv DOI: https://doi.org/10.48550/arXiv.1806.03537
- Evidence: diachronic word embeddings/semantic shift detection의 용어, 방법 축, challenge, application을 정리한 COLING 2018 survey.
- Interpretation: Worker 2/3가 taxonomy와 archive 분류 체계를 잡을 때 gateway로 사용.
- 읽기 우선순위: P1-gateway.

### P1/P2-gateway. Tang (2018), “A state-of-the-art of semantic change computation”

- DOI/URL: https://doi.org/10.1017/S1351324918000220
- Cambridge Core: https://www.cambridge.org/core/journals/natural-language-engineering/article/stateoftheart-of-semantic-change-computation/CCD69C7C2306B0E4D246B456E236EFAF
- OpenAlex cited_by_count: 43 (2026-05-02 조회)
- Evidence: semantic change computation을 corpus, sense characterization, change modeling, evaluation, visualization 등으로 정리한 review.
- Interpretation: archive의 상위 분류 체계와 future backlog를 세우는 데 실무적.
- 읽기 우선순위: P1/P2.

### P2. Schlechtweg, Hätty, Del Tredici & Schulte im Walde (2019), “A Wind of Change”

- DOI/URL: https://doi.org/10.18653/v1/P19-1072 / https://aclanthology.org/P19-1072/
- OpenAlex cited_by_count: 87 (2026-05-02 조회)
- Evidence: time/domain lexical semantic change detection and evaluation benchmark 논문.
- Interpretation: SOUL drift 추천을 평가 가능한 task로 바꾸려면 benchmark/evaluation 사고가 필요하다.
- 읽기 우선순위: P2.

## Handoff notes

- Hamilton류 law는 반드시 Dubossarsky et al. (2017)의 bias-control 경고와 함께 제시.
- `alignment`, `joint dynamic trajectory`, `change-point`, `control condition`, `temporal analogy`, `survey gateway` 태그를 sources metadata에 유지 권장.
- SOUL/OpenClaw 적용 문장은 evidence가 아니라 interpretation/hypothesis로 표시해야 한다.

## Subagent evidence

- Subagents spawned: 2 (`019de861-1fd6-7de2-b10d-c40cac41e41f` foundational 2014–2018 search; `019de861-1fea-7cd2-875a-bc74a80ed4e4` broader historical/survey anchors).
- Findings integrated: Hamilton/Kim/Kulkarni/Bamler/Rudolph/Yao/Dubossarsky/Szymanski plus Harris/Firth/Traugott/Sagi/Kutuzov/Tang/Schlechtweg anchor set.
- Serial searches before spawn: 0 after claim; subagents were spawned before substantive serial repo search per broad-task delegation note.
