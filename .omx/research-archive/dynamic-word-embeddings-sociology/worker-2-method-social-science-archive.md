# Worker 2 Archive: Dynamic Word Embeddings for Sociology / SOUL Wiki

Worker: `worker-2`  
Date: 2026-05-02 KST  
Request type: comprehensive research — DOI/URL, journal/venue, year, method, datasets, influence/relevance, evidence-vs-interpretation notes, and SOUL/Jiseong LLM Wiki recommendations.

## Direct Answer

For SOUL/Jiseong Wiki integration, treat dynamic word embeddings as a **method family for tracing corpus-relative meaning trajectories**, not as direct evidence of social reality. Use Hamilton/Bamler/Rudolph for method foundations, Kozlowski/Stoltz/Rodman for social-science interpretation, Garg/Charlesworth/Nelson for social-group use cases, and Dubossarsky/Kutuzov/Arseniev-Koehler as guardrails.

## Source Cards

| Priority | Paper | Venue / year | DOI / URL | Method | Dataset(s) | Influence / relevance | Evidence vs interpretation |
|---|---|---:|---|---|---|---|---|
| P1 | Hamilton, Leskovec & Jurafsky, “Diachronic Word Embeddings Reveal Statistical Laws of Semantic Change” | ACL 2016 | https://aclanthology.org/P16-1141/ ; https://doi.org/10.18653/v1/P16-1141 | Diachronic PPMI/SVD/word2vec with alignment; semantic-change laws. | Historical corpora across languages/time. | Foundational semantic-drift benchmark. | Evidence: ACL metadata/DOI/pages. Interpretation: use for concept trajectory pages, but pair with Dubossarsky controls. |
| P1 | Bamler & Mandt, “Dynamic Word Embeddings” | ICML/PMLR 2017 | https://proceedings.mlr.press/v70/bamler17a.html | Probabilistic time-stamped language model; latent trajectories; skip-gram smoothing/filtering. | Three time-stamped corpora in paper. | Best “continuous drift” model anchor. | Evidence: PMLR official abstract. Interpretation: metaphor/model for SOUL interest drift. |
| P1 | Rudolph & Blei, “Dynamic Embeddings for Language Evolution” | WWW 2018 | https://doi.org/10.1145/3178876.3185999 ; https://www.cs.columbia.edu/~blei/papers/RudolphBlei2018.pdf | Dynamic Bernoulli/exponential-family embeddings with Gaussian-random-walk latent variables. | U.S. Senate 1858–2009; ACM abstracts 1951–2014; arXiv ML 2007–2015. | Closest to political + research-paper archive use. | Evidence: paper PDF states DOI, datasets, model. Interpretation: good template for SOUL paper/note trajectories. |
| P1 | Dubossarsky, Weinshall & Grossman, “Outta Control” | EMNLP 2017 | https://aclanthology.org/D17-1118/ ; https://doi.org/10.18653/v1/D17-1118 | Control-condition critique of semantic-change laws; identifies frequency/model artifacts. | Methodological controls. | Mandatory caveat source. | Evidence: ACL abstract. Interpretation: every drift claim needs null/control checks. |
| P1 | Kutuzov et al., “Diachronic word embeddings and semantic shifts: a survey” | COLING 2018 | https://aclanthology.org/C18-1117/ | Survey taxonomy for semantic-shift detection methods/challenges/applications. | Literature survey. | Wiki taxonomy gateway. | Evidence: ACL abstract/metadata. Interpretation: use to structure method categories. |
| P1 | Garg, Schiebinger, Jurafsky & Zou, “Word embeddings quantify 100 years of gender and ethnic stereotypes” | PNAS 2018 | https://doi.org/10.1073/pnas.1720347115 | Temporal embedding association metrics linked to external demographic/occupation data. | 100 years text data; U.S. Census/occupations. | Canonical quantitative-social-science stereotype-change example. | Evidence: DOI/PNAS metadata cross-checked via indexed pages. Interpretation: template for linking Korean concept drift to external covariates. |
| P1 | Kozlowski, Taddy & Evans, “The Geometry of Culture” | American Sociological Review 2019 | https://journals.sagepub.com/doi/10.1177/0003122419877135 | Word2vec cultural dimensions via contrast axes; projection/validation; decade comparison. | Millions of Google Ngram books over 20th century. | Strongest sociology/culture bridge. | Evidence: SAGE article metadata/abstract. Interpretation: diachronic cultural embedding analysis, not a pure dynamic model. |
| P1 | Rodman, “A Timely Intervention” | Political Analysis 2020 | https://doi.org/10.1017/pan.2019.23 | Four time-sensitive word2vec implementations; small-corpus best practices incl. bootstrap/pretraining. | 161 years newspaper coverage; equality discourse. | Crucial for sparse historical corpora. | Evidence: Cambridge page. Interpretation: operational guidance for SOUL Korean-history corpora. |
| P1 | Stoltz & Taylor, “Cultural cartography with word embeddings” | Poetics 2021 | https://doi.org/10.1016/j.poetic.2021.101567 | Fixed vs variable embedding-space navigation for cultural analysis. | Immigration discourse case. | Best conceptual bridge for “meaning space” in cultural sociology. | Evidence: ScienceDirect abstract. Interpretation: useful for Wiki explanation of term/document movement. |
| P2 | Nelson, “Leveraging the alignment between machine learning and intersectionality” | Poetics 2021 | https://doi.org/10.1016/j.poetic.2021.101539 | Word embeddings for intersectional subjectivities/institutions plus close reading. | 19th-century U.S. South first-person narratives. | Strong evidence-vs-close-reading model. | Evidence: ScienceDirect metadata/abstract. Interpretation: use for social category pages with explicit ethical caveats. |
| P2 | Daenekindt & Schaap, “Using word embedding models to capture changing media discourses” | Journal of Computational Social Science 2022 | https://doi.org/10.1007/s42001-022-00182-8 | Word embeddings + Concept Mover’s Distance for legitimacy/gender discourse over time/genre. | 23,992 Pitchfork reviews, 1999–2021. | Transferable media/cultural-hierarchy pipeline. | Evidence: Springer open-access page. Interpretation: apply to Korean cultural-review/media corpora after controls. |
| P2 | Charlesworth, Caliskan & Banaji, “Historical representations of social groups across 200 years of word embeddings from Google Books” | PNAS 2022 | https://doi.org/10.1073/pnas.2121798119 | Long-run embeddings for social-group stereotype content and valence. | 850B English Google Books words, 1800–1999; 14 groups; 14,000 associates; 600 traits. | Broadest persistence/change social-group template. | Evidence: DOI/PNAS metadata cross-checked via indexed pages. Interpretation: separate changing associates from stable valence. |
| P2 | Arseniev-Koehler, “Theoretical Foundations and Limits of Word Embeddings” | Sociological Methods & Research 2024 | https://doi.org/10.1177/00491241221140142 | Theoretical account of what embeddings can/cannot capture about coherent, relational, static meaning. | Theory/methods article. | Needed limitation source. | Evidence: SAGE abstract. Interpretation: include as caveat on all sociological embedding claims. |
| P2 | Stoltz & Taylor, “Concept Mover’s Distance” | Journal of Computational Social Science 2019 | https://doi.org/10.1007/s42001-019-00048-6 | Word Mover’s Distance adaptation to measure text engagement with focal concepts. | Method paper. | Useful for SOUL document-to-concept scoring. | Evidence: Springer/RePEc metadata. Interpretation: product signal only after validation. |

## Evidence Standards

Treat as evidence: DOI/URL, venue/year, stated corpora, official abstract method claims, measured model outputs, and external validation data.  
Treat as interpretation: claims that a semantic trajectory “proves” social change, direct transfer from English findings to Korean corpora, and personal-interest inferences from note drift.

Minimum controls before a durable Wiki claim: period/source balance, frequency/polysemy checks, null or shuffled-time controls, multi-seed/bootstrap stability, close reading of high-drift terms, and external covariate validation when possible.

## SOUL / Wiki Recommendations

1. `Methods/Diachronic Word Embeddings.md` — Hamilton, Bamler, Rudolph, Kutuzov; alignment, joint dynamic models, sparse time bins, evaluation.
2. `Sociology/Computational Cultural Sociology with Embeddings.md` — Kozlowski, Stoltz/Taylor, Arseniev-Koehler; relational meaning and limits.
3. `Use Cases/Social Group Representations and Stereotype Change.md` — Garg, Charlesworth, Nelson; social category caution and close reading.
4. `Use Cases/Political and Historical Concept Drift.md` — Rodman and Rudolph/Blei; small-corpus newspaper/political records workflow.
5. `SOUL Design/Embedding Drift Signals.md` — store corpus slice, model version, seed, vocabulary filters, source genre, confidence, human note, claim type.

## Reusable Takeaway

Dynamic word embeddings are best archived as a controlled interpretive instrument: they reveal how associations move inside a corpus/model over time; sociological claims require corpus context, controls, and close reading.
