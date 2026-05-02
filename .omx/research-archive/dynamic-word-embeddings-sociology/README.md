# Dynamic Word Embeddings in Sociology Journals — Research Archive

- 생성일: 2026-05-02 KST
- 범위: dynamic word embeddings, diachronic/temporal word embeddings, semantic shift detection, contextual embeddings, word-embedding-based cultural/sociological semantic change.
- 우선순위: 사회학 핵심 저널(ASR, Sociological Science, SMR/Sociological Methodology, Poetics)을 Tier S/A로 두고, Political Analysis/PNAS/Psychological Science/Scientific Data/JCSS는 방법론·영향력 때문에 adjacent tier로 둔다.
- 포함 원칙: exact NLP label보다 역사/시계열 코퍼스에서 embedding space·concept association·semantic salience 변화를 다루는지 기준으로 포함했다.

## Executive synthesis

사회학 저널에서 DWE 계열 연구는 NLP식 `lexical semantic change detection`보다 **문화적 의미의 기하학**, **사회 범주/계급/젠더 이데올로기의 시간적 궤적**, **텍스트 기반 문화사회학 방법론**으로 번역되어 나타난다.

## Tier table

| Tier | Paper | Journal | Year | DOI | Fit |
|---|---|---:|---:|---|---|
| S | The Geometry of Culture: Analyzing the Meanings of Class through Word Embeddings | American Sociological Review | 2019 | 10.1177/0003122419877135 | core sociology journal; historical/dynamic word embeddings |
| S | Stereotypical Gender Associations in Language Have Decreased Over Time | Sociological Science | 2020 | 10.15195/v7.a1 | core sociology-adjacent open journal; diachronic bias/social change |
| A | Cultural cartography with word embeddings | Poetics | 2021 | 10.1016/j.poetic.2021.101567 | sociology/cultural journal; explicit variable/fixed embedding spaces for change |
| A | Theoretical Foundations and Limits of Word Embeddings: What Types of Meaning can They Capture? | Sociological Methods & Research | 2024 | 10.1177/00491241221140142 | sociology theory/methods journal; limits/theoretical foundations |
| A | Symbols of class: A computational analysis of class distinction-making through etiquette, 1922-2017 | Poetics | 2022 | 10.1016/j.poetic.2022.101734 | cultural sociology journal; historical dynamic class salience |
| A | Leveraging the alignment between machine learning and intersectionality: Using word embeddings to measure intersectional experiences of the nineteenth century U.S. South | Poetics | 2021 | 10.1016/j.poetic.2021.101539 | Poetics/cultural sociology; historical embeddings for intersectionality |
| B | Contextual Embeddings in Sociological Research: Expanding the Analysis of Sentiment and Social Dynamics | Sociological Methodology | 2025 | 10.1177/00811750241260729 | sociology methods journal; contextual embeddings for affect/social dynamics |
| B | A Timely Intervention: Tracking the Changing Meanings of Political Concepts with Word Vectors | Political Analysis | 2020 | 10.1017/pan.2019.23 | adjacent political methodology; direct dynamic word-vector time-series method |
| B | Word embeddings quantify 100 years of gender and ethnic stereotypes | PNAS | 2018 | 10.1073/pnas.1720347115 | adjacent high-impact social science; embedding social stereotypes over historical time |
| B | Gender Stereotypes in Natural Language: Word Embeddings Show Robust Consistency Across Child and Adult Language Corpora of More Than 65 Million Words | Psychological Science | 2021 | 10.1177/0956797620963619 | psychology/social cognition adjacent; stereotype embeddings across social language corpora |
| B | DUKweb, diachronic word representations from the UK Web Archive corpus | Scientific Data | 2021 | 10.1038/s41597-021-01047-x | method resource for social/cultural studies; diachronic embedding dataset |
| B | Concept Mover’s Distance: measuring concept engagement via word embeddings in texts | Journal of Computational Social Science | 2019 | 10.1007/s42001-019-00048-6 | computational social science method; concept engagement not time-specific |

## Annotated sources

### 1. The Geometry of Culture: Analyzing the Meanings of Class through Word Embeddings
- Authors: Austin C. Kozlowski, Matt Taddy, James A. Evans
- Journal/year: American Sociological Review (2019)
- DOI/URL: https://doi.org/10.1177/0003122419877135 / https://journals.sagepub.com/doi/10.1177/0003122419877135
- Tier/fit: S — core sociology journal; historical/dynamic word embeddings
- Method: word2vec; cultural dimensions; Google Ngrams and decade-sliced JSTOR sociology corpus; survey validation
- Domain: class, affluence, education, cultivation, status, cultural meaning
- Evidence: ASR article analyzes millions of books over 100 years; Sage page notes decade windows and JSTOR sociology corpus in appendix/online text.
- Interpretation: Flagship sociology paper translating DWE into cultural-meaning trajectories; SOUL analogue: stable cultural dimensions with drifting markers.

### 2. Stereotypical Gender Associations in Language Have Decreased Over Time
- Authors: Jason J. Jones, Mohammad Ruhul Amin, Jessica Kim, Steven Skiena
- Journal/year: Sociological Science (2020)
- DOI/URL: https://doi.org/10.15195/v7.a1 / https://sociologicalscience.com/tag/gender-ideology/
- Tier/fit: S — core sociology-adjacent open journal; diachronic bias/social change
- Method: decade-level word embeddings on English-language books, 1800-2000; gender-domain association measures
- Domain: gender ideology, stereotypes, career/family/science/arts, social change
- Evidence: Sociological Science/NSF pages list volume 7, pages 1-35, DOI, and word embeddings over millions of digitized books.
- Interpretation: Direct example of embedding-based social-change hypothesis testing.

### 3. Cultural cartography with word embeddings
- Authors: Dustin S. Stoltz, Marshall A. Taylor
- Journal/year: Poetics (2021)
- DOI/URL: https://doi.org/10.1016/j.poetic.2021.101567 / https://www.sciencedirect.com/science/article/abs/pii/S0304422X21000504
- Tier/fit: A — sociology/cultural journal; explicit variable/fixed embedding spaces for change
- Method: fixed embedding space vs variable embedding space; immigration discourse case
- Domain: cultural sociology, immigration discourse, social marking, media fields, echo chambers, diffusion/change
- Evidence: ScienceDirect abstract presents fixed/variable embedding methods and U.S. immigration discourse application.
- Interpretation: Best conceptual bridge from DWE mechanics to sociological theory of meaning.

### 4. Theoretical Foundations and Limits of Word Embeddings: What Types of Meaning can They Capture?
- Authors: Alina Arseniev-Koehler
- Journal/year: Sociological Methods & Research (2024)
- DOI/URL: https://doi.org/10.1177/00491241221140142 / https://journals.sagepub.com/doi/abs/10.1177/00491241221140142
- Tier/fit: A — sociology theory/methods journal; limits/theoretical foundations
- Method: theoretical/methodological assessment of word embeddings, structural linguistics, semiotics, cultural sociology
- Domain: meaning measurement, structuralism, semiotics, cultural sociology
- Evidence: Sage page lists SMR 53(4):1753-1793 and abstract on coherence, relationality, static-system assumptions.
- Interpretation: Quality-control paper for avoiding naive drift interpretation.

### 5. Symbols of class: A computational analysis of class distinction-making through etiquette, 1922-2017
- Authors: Andrea Voyer, Zachary D. Kline, Madison Danton
- Journal/year: Poetics (2022)
- DOI/URL: https://doi.org/10.1016/j.poetic.2022.101734 / https://www.sciencedirect.com/science/article/pii/S0304422X22001164
- Tier/fit: A — cultural sociology journal; historical dynamic class salience
- Method: word embeddings over etiquette manuals, 1922-2017; salience of class concepts
- Domain: class distinction, etiquette, meritocracy, status symbols, cultural closure
- Evidence: ScienceDirect page lists Poetics 94, 101734 and highlights embeddings for class concepts over 1922-2017.
- Interpretation: Strong time-axis sociology application for concept salience trajectories.

### 6. Leveraging the alignment between machine learning and intersectionality: Using word embeddings to measure intersectional experiences of the nineteenth century U.S. South
- Authors: Laura K. Nelson
- Journal/year: Poetics (2021)
- DOI/URL: https://doi.org/10.1016/j.poetic.2021.101539 / https://www.sciencedirect.com/science/article/abs/pii/S0304422X21000115
- Tier/fit: A — Poetics/cultural sociology; historical embeddings for intersectionality
- Method: word embeddings for inductive, cultural, intersectional analysis of historical text
- Domain: race, gender, slavery, Civil War, intersectionality
- Evidence: ScienceDirect highlights state word embeddings visualize intersectional experiences of slavery and the Civil War.
- Interpretation: Historical social-category meaning-space case; useful adjacent DWE application.

### 7. Contextual Embeddings in Sociological Research: Expanding the Analysis of Sentiment and Social Dynamics
- Authors: Moeen Mostafavi, Michael D. Porter, Dawn T. Robinson
- Journal/year: Sociological Methodology (2025)
- DOI/URL: https://doi.org/10.1177/00811750241260729 / https://journals.sagepub.com/doi/10.1177/00811750241260729
- Tier/fit: B — sociology methods journal; contextual embeddings for affect/social dynamics
- Method: BERTNN; contextual BERT embeddings to expand affective lexicons
- Domain: affect control theory, sentiment, social dynamics, new concepts
- Evidence: Sage page lists DOI and abstract on BERTNN estimating affective meanings using contextual usage.
- Interpretation: Contextualized semantic position method for new concepts; adjacent to dynamic contextual embeddings.

### 8. A Timely Intervention: Tracking the Changing Meanings of Political Concepts with Word Vectors
- Authors: Emma Rodman
- Journal/year: Political Analysis (2020)
- DOI/URL: https://doi.org/10.1017/pan.2019.23 / https://www.cambridge.org/core/journals/political-analysis/article/timely-intervention-tracking-the-changing-meanings-of-political-concepts-with-word-vectors/DDF3B5833A12E673EEE24FBD9798679E
- Tier/fit: B — adjacent political methodology; direct dynamic word-vector time-series method
- Method: four time-sensitive word2vec implementations; small-corpus time series; bootstrap resampling and pretraining
- Domain: political concepts, equality, newspaper coverage over 161 years
- Evidence: Cambridge page lists Political Analysis 28(1):87-111 and tests time-sensitive word vectors on 161 years of newspaper coverage.
- Interpretation: Not sociology journal, but essential for small-corpus temporal semantics like SOUL logs.

### 9. Word embeddings quantify 100 years of gender and ethnic stereotypes
- Authors: Nikhil Garg, Londa Schiebinger, Dan Jurafsky, James Zou
- Journal/year: PNAS (2018)
- DOI/URL: https://doi.org/10.1073/pnas.1720347115 / https://www.pnas.org/doi/10.1073/pnas.1720347115
- Tier/fit: B — adjacent high-impact social science; embedding social stereotypes over historical time
- Method: historical word embeddings; stereotype associations over 20th century; comparison to census/occupation and social trends
- Domain: gender, ethnicity, stereotypes, social history
- Evidence: PNAS DOI and established high-impact diachronic embedding application to stereotypes.
- Interpretation: Adjacent anchor for comparing textual stereotype drift with social indicators.

### 10. Gender Stereotypes in Natural Language: Word Embeddings Show Robust Consistency Across Child and Adult Language Corpora of More Than 65 Million Words
- Authors: Tessa E. S. Charlesworth, Victor Yang, Thomas C. Mann, Benedek Kurdi, Mahzarin R. Banaji
- Journal/year: Psychological Science (2021)
- DOI/URL: https://doi.org/10.1177/0956797620963619 / https://journals.sagepub.com/doi/pdf/10.1177/0956797620963619
- Tier/fit: B — psychology/social cognition adjacent; stereotype embeddings across social language corpora
- Method: word embeddings over child/adult conversations, books, movies, TV; stereotype quantification
- Domain: gender stereotypes, collective representations, language corpora
- Evidence: Sage page abstract states 65+ million words across child/adult and media corpora.
- Interpretation: Corpus/source comparison benchmark for social meanings.

### 11. DUKweb, diachronic word representations from the UK Web Archive corpus
- Authors: Adam Tsakalidis, Pierpaolo Basile, Marya Bazzi, Mihai Cucuringu, Barbara McGillivray
- Journal/year: Scientific Data (2021)
- DOI/URL: https://doi.org/10.1038/s41597-021-01047-x / https://pubmed.ncbi.nlm.nih.gov/34654827/
- Tier/fit: B — method resource for social/cultural studies; diachronic embedding dataset
- Method: diachronic word representations from UK Web Archive corpus
- Domain: social/cultural studies resource, web archive, lexical semantic change
- Evidence: PubMed abstract says lexical semantic change matters for social/cultural studies and diachronic embeddings are standard resources.
- Interpretation: Reusable DWE resource for contemporary social semantic change.

### 12. Concept Mover’s Distance: measuring concept engagement via word embeddings in texts
- Authors: Dustin S. Stoltz, Marshall A. Taylor
- Journal/year: Journal of Computational Social Science (2019)
- DOI/URL: https://doi.org/10.1007/s42001-019-00048-6 / https://ideas.repec.org/a/spr/jcsosc/v2y2019i2d10.1007_s42001-019-00048-6.html
- Tier/fit: B — computational social science method; concept engagement not time-specific
- Method: word mover’s distance plus embeddings to measure concept engagement
- Domain: cultural sociology, text analysis, concept measurement
- Evidence: RePEc/Springer entry lists Journal of Computational Social Science 2(2):293-313 and DOI.
- Interpretation: Can become dynamic when applied to time-sliced documents; candidate for LLM Wiki edge strength.

## Method map for sociology-facing DWE

| Sociology question | Embedding design | Best exemplars | Risk control |
|---|---|---|---|
| Do meanings of class/gender categories shift over history? | decade-sliced embeddings + semantic dimensions | Kozlowski et al.; Jones et al.; Voyer et al. | validate with surveys/social indicators; avoid treating corpus composition as social change |
| How can culture theory use embedding space? | fixed vs variable spaces; cultural dimensions; concept mover distance | Stoltz & Taylor; Arseniev-Koehler | explicit theory of meaning; document static-system assumptions |
| Can small social-science corpora support time-series semantics? | bootstrapped/pretrained time-sensitive word2vec | Rodman | human gold standard; resampling; uncertainty reporting |
| How do contextual meanings and affective meanings vary? | BERT/contextual embeddings; affective lexicon expansion | Mostafavi et al.; Charlesworth et al. | separate source/corpus effect from actual social difference |

## Jiseong/SOUL / LLM Wiki implications

- Treat a research persona as a **cultural meaning object**: not just keywords, but axes such as method/theory/domain/data/affect.
- Use sociology papers as a warning that stable high-level dimensions can coexist with drifting markers.
- Expose two edge types: `dynamic-semantic-method` and `sociological-meaning-application`.
- Add validation fields to future SOUL drift artifacts: corpus slice, alignment method, bootstrapping, human/evidence anchor, and alternative explanation.

## Reading order

1. Kozlowski, Taddy & Evans 2019 — sociology flagship and cultural dimensions.
2. Jones et al. 2020 — diachronic gender ideology in Sociological Science.
3. Stoltz & Taylor 2021 — variable/fixed embedding spaces for cultural cartography.
4. Rodman 2020 — small-corpus time-series best practices.
5. Arseniev-Koehler 2024 — theoretical limits and assumptions.
6. Voyer et al. 2022 / Nelson 2021 — class/intersectionality historical applications.
7. Mostafavi et al. 2025 — contextual embeddings in sociological methodology.

## Backlog

- [ ] Query OpenAlex/Semantic Scholar citation counts for all 12 sources and freeze a citation snapshot.
- [ ] Create Obsidian PaperWiki pages under `pages/20 Dynamic Semantics and Representation Learning` and concept edges under `90 Concepts and Methods` if vault integration is requested.
- [ ] Add `sociological semantic drift` concept hub connecting class/gender/immigration/stereotype trajectories.
- [ ] Build BibTeX/RIS from DOI list.
