# SOUL / Jiseong LLM Wiki Integration Recommendations

Date: 2026-05-02 KST  
Worker: `worker-2`

## Outcome-first recommendation

Integrate this literature as a **method-and-evidence layer** for SOUL. Use embedding drift for archive organization, concept-history pages, and recommendation explanations only when the claim type and validation state are explicit.

## Proposed Wiki nodes

### 1. Methods / Diachronic Word Embeddings
Core citations: Hamilton 2016 (https://aclanthology.org/P16-1141/), Bamler & Mandt 2017 (https://proceedings.mlr.press/v70/bamler17a.html), Rudolph & Blei 2018 (https://www.cs.columbia.edu/~blei/papers/RudolphBlei2018.pdf), Kutuzov 2018 (https://aclanthology.org/C18-1117/).

Suggested YAML:
```yaml
method_family: diachronic_word_embeddings
alignment: procrustes | joint_dynamic | initialization | not_applicable
corpus_slices: [period, source, genre]
controls_required: [frequency, polysemy, corpus_size, random_seed, null_terms]
claim_type: evidence | interpretation | hypothesis
```

### 2. Sociology / Computational Cultural Sociology
Core citations: Kozlowski et al. 2019 (https://journals.sagepub.com/doi/10.1177/0003122419877135), Stoltz & Taylor 2021 (https://doi.org/10.1016/j.poetic.2021.101567), Arseniev-Koehler 2024 (https://doi.org/10.1177/00491241221140142).

Warning block: embedding-space relations are evidence about a corpus/model, not direct evidence about society.

### 3. Use Cases / Social Group Representations
Core citations: Garg et al. 2018 (https://doi.org/10.1073/pnas.1720347115), Charlesworth et al. 2022 (https://doi.org/10.1073/pnas.2121798119), Nelson 2021 (https://doi.org/10.1016/j.poetic.2021.101539).

Integration rule: store group-label choices, exclusions, ambiguous labels, historical period, and ethical cautions.

### 4. Use Cases / Political and Historical Concept Drift
Core citation: Rodman 2020 (https://doi.org/10.1017/pan.2019.23). Candidate Korean/SOUL corpora: newspapers, National Assembly records, magazines, textbook editions, legal texts, presidential speeches, labor archives, and personal research notes. Pair vector neighbors with snippets and historical timelines.

### 5. SOUL Design / Embedding Drift Signals
Recommended fields:
```yaml
concept: "민주화"
slice_start: 1980
slice_end: 1989
nearest_neighbors: []
drift_score: null
confidence: low | medium | high
control_passed: false
human_close_reading: required
interpretation_note: "Hypothesis only until controls pass."
sources: []
```

## Reading order

1. Kutuzov 2018 survey.
2. Hamilton 2016 + Dubossarsky 2017 caveat.
3. Rodman 2020 for small corpora.
4. Kozlowski 2019 + Stoltz/Taylor 2021 for sociology.
5. Garg 2018 / Charlesworth 2022 / Nelson 2021 for group representations.
6. Rudolph/Blei 2018 or Bamler/Mandt 2017 for joint dynamic models.
