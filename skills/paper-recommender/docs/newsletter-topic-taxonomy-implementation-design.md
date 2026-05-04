# Score-based newsletter topic taxonomy: implementation design and acceptance criteria

## Decision target

Implement a score-based topic taxonomy for newsletter briefings that keeps the current researcher-facing briefing shape stable while replacing ordered substring first-match classification with a deterministic `Primary + Secondary labels` result.

This artifact translates the existing planning docs into an implementation handoff for the Python classifier, Google Apps Script parity work, review, and rollout.

## Current touchpoints inspected

### Python newsletter ingest path

- `skills/paper-recommender/newsletter_ingest.py`
  - `_TOPIC_RULES`: duplicated label/needle list using ordered first-match semantics.
  - `classify_topic(item)`: currently classifies from `title + kind + url` only, returns one Korean display label string.
  - `group_items_by_topic(items)`: groups by the scalar `classify_topic` result and sorts by `(-count, label)`.
  - `render_topic_briefing(...)`: renders stable Markdown sections and item bullets; tests assert the title, `핵심 요약`, `기술 포인트`, `출처 링크`, and privacy boundary.
  - `publish_items(...)`: persists extracted item metadata and URL only; full email body is not written.
- `skills/paper-recommender/gmail_newsletter_briefing.py`
  - Reuses `select_items`, `publish_items`, and `render_topic_briefing`; any classifier signature change must keep these imports compatible.
- `skills/paper-recommender/project/src/paper_recommender/sources/google_newsletters.py`
  - Separate daily-research source adapter scans body in memory for seed-topic filtering, but does not share the standalone briefing taxonomy.

### Google Apps Script newsletter archive path

- `integrations/google-apps-script/newsletter_archive_to_discord.gs`
  - `TOPIC_RULES`: duplicated ordered label/needle list.
  - `collectNewsletterItems_(...)`: sets `item.topic` from subject/body or subject/article detail/url, and keeps snippet/article text in memory for rendering decisions.
  - `classifyTopic_(text)`: ordered substring first-match returning one display label.
  - `groupByTopic_(items)`: groups by `item.topic`.
  - `renderBriefing_(items, query)`: preserves Discord-facing Korean heading/bullet structure and enforces `DISCORD_SAFE_CHAR_LIMIT` through `appendWithinLimit_`.
  - Script Properties and relay token handling must remain untouched; taxonomy work must not change secret names or delivery modes.

### Existing docs and tests

- `skills/paper-recommender/docs/newsletter-topic-taxonomy-plan.md`: target taxonomy, scoring direction, false-positive risks, staged rollout, and desired result shape.
- `skills/paper-recommender/docs/current-topic-rules-briefing-evidence.md`: current TOPIC_RULES behavior, latest briefing evidence, and immediate regression cases.
- `skills/paper-recommender/project/tests/test_newsletter_ingest.py`: ingest/privacy and one topic briefing happy path; no direct score classifier, false-positive, secondary label, grouping-order, or Python/GAS parity fixtures.
- `skills/paper-recommender/project/tests/test_gmail_newsletter_briefing.py`: OAuth/message decode coverage only; no taxonomy contract tests.

## Implementation design

### 1. Shared taxonomy spec

Add a source-controlled spec at `skills/paper-recommender/topic_taxonomy.json` as the canonical hand-editable contract.

Recommended top-level shape:

```json
{
  "version": 1,
  "primary_labels": [
    {
      "id": "data_retrieval_knowledge",
      "display": "데이터/RAG/지식검색",
      "order": 30,
      "threshold": 3.0,
      "terms": [
        {"term": "rag", "match": "token", "weight": 4.0, "secondary": ["rag"]},
        {"term": "semantic search", "match": "phrase", "weight": 4.0, "secondary": ["semantic_search"]},
        {"term": "github.com", "match": "host", "weight": 1.0, "secondary": ["open_source"]}
      ]
    }
  ],
  "secondary_labels": [
    {"id": "rag", "display": "RAG", "order": 100, "category": "technology"}
  ],
  "fallbacks": {
    "paper": {"id": "research_paper_general", "display": "논문/리서치"},
    "default": {"id": "other_tech_report", "display": "기타 테크 리포트"}
  }
}
```

Spec requirements:

- `id` is the stable machine key; `display` is what existing briefing headings can show.
- `order` is the deterministic tie-break after score and group count.
- `threshold` may be per-label or global; start with one global threshold if simpler.
- `terms[].match` is one of `phrase`, `token`, or `host`; weak arbitrary substring matching is off by default.
- `terms[].secondary` maps matched rules to secondary labels without persisting raw body snippets.
- Negative guards should be encoded as exact blocked tokens/phrases or handled by matcher semantics; examples: `search` must not match `research`, `market` must not match `benchmark`.

### 2. Python classifier contract

Introduce a small typed result while keeping `classify_topic(item) -> str` backward-compatible.

Recommended internal API:

```python
@dataclass(frozen=True)
class TopicClassification:
    primary: str
    primary_display: str
    secondary: tuple[str, ...]
    confidence: float
    reasons: tuple[str, ...]


def classify_topic_detail(item: dict[str, str]) -> TopicClassification: ...
def classify_topic(item: dict[str, str]) -> str:
    return classify_topic_detail(item).primary_display
```

Scoring behavior:

1. Build field-separated input from `title`, `kind`, `url`, and optional non-persisted `snippet` / `classification_text` when present.
2. Apply phrase/token/host matchers with field weights:
   - title phrase/token: strongest signal.
   - URL host and `kind`: source/evidence signal, not usually enough alone to override a strong topical title.
   - snippet/classification text: medium signal, only if already available in memory.
3. Sum scores per primary label.
4. Select the highest score above threshold; break ties by explicit label `order`.
5. If no primary clears threshold, use `kind.startswith("paper")` or paper host fallback before default fallback.
6. Emit up to seven secondary labels ordered by score then secondary `order`.
7. Store `reasons` as rule IDs or normalized term IDs such as `title:rag`, `host:github.com`; never store raw body text.

Compatibility boundary:

- Existing callers that expect a string continue to use `classify_topic`.
- `group_items_by_topic` groups by `classify_topic_detail(item).primary_display` or the legacy `topic` field during transition.
- `render_topic_briefing` keeps existing headings and bullets, and may append `tags=` to the `기술 포인트` line once tests lock the current output shape.
- Raw published items may add machine fields (`primary_topic`, `secondary_topics`, `topic_confidence`) only if body/privacy tests prove no raw body text is persisted.

### 3. Google Apps Script parity contract

GAS should follow Python only after the Python spec/classifier behavior is stable.

Safe staged approach:

1. Keep current `topic` scalar and briefing layout unchanged.
2. Mirror the same primary IDs/display names and match terms in `TOPIC_RULES_V2` or generated constants.
3. Add `classifyTopicDetail_(text, kind, url)` returning an object:
   - `{primary, primaryDisplay, secondary, confidence, reasons}`.
4. Keep `classifyTopic_(text)` as a wrapper returning `primaryDisplay` for existing code.
5. If tags are rendered, use short secondary IDs/display names only; do not add article body excerpts or secret property values to Discord output.
6. Validate with a local `node --check` syntax check and fixture comparison if a GAS test harness is added.

### 4. Fixture and regression plan

Add shared fixture cases before or alongside implementation. Minimum fixture set:

| Case | Input signal | Expected primary | Expected secondary / notes |
| --- | --- | --- | --- |
| `research_not_search` | `Research methods paper` + arXiv URL | `research_paper_general` or stronger concrete research topic only if explicit signal exists | Must not become `data_retrieval_knowledge` from `search` inside `research`. |
| `benchmark_not_market` | `Safety benchmark suite for frontier models` | `safety_governance_regulation` or `ai_infra_mlops` by explicit tie-break | Must not become market/strategy from `market` inside `benchmark`. |
| `rag_agent_tiebreak` | `RAG agent with tool use` | deterministic between retrieval and agents per spec order | Secondary should include both `rag` and `agent`. |
| `github_rag_repo` | GitHub URL + RAG repo title | topical primary based on title (`data_retrieval_knowledge`) | Secondary includes `open_source`; GitHub host alone does not dominate. |
| `pricing_enterprise` | `OpenAI pricing and enterprise partnership` | `market_ecosystem_strategy` | Model/company words must not force foundation-model primary. |
| `healthcare_privacy_regulation` | healthcare + privacy + regulation | `safety_governance_regulation` | `healthcare` can be secondary; regulation/risk primary wins. |
| `paper_fallback` | paper URL with weak topic signal | `research_paper_general` | Keeps old paper fallback. |
| `default_fallback` | weak non-paper post | `other_tech_report` | Keeps old fallback. |

Python tests should cover:

- `classify_topic_detail` score, tie-break, and secondary output.
- `classify_topic` legacy string compatibility.
- `group_items_by_topic` count-desc and explicit label-order tie-break.
- `render_topic_briefing` keeps existing Korean bullets and privacy boundary, and renders tags only from sanitized secondary labels if enabled.

GAS parity tests can start as fixture snapshots or a documented manual parity check if no Apps Script test runner exists.

## Acceptance criteria

### Functional acceptance

- Python classification no longer uses arbitrary substring first-match for primary assignment.
- `classify_topic(item)` remains backward-compatible and returns the primary display label string.
- A detail API exposes `primary`, `primary_display`, `secondary`, `confidence`, and sanitized `reasons`.
- Known false positives are blocked: `research` does not trigger `search`; `benchmark` does not trigger `market`.
- Multi-signal items preserve secondary labels while grouping by one primary.
- Paper/default fallbacks remain compatible with current output.
- Existing Markdown/Discord briefing headings and bullet labels remain recognizable to current consumers.
- Full email bodies, OAuth tokens, Script Properties, webhook URLs, and relay tokens are never written to taxonomy reasons, raw wiki output, or Discord briefing text.

### Compatibility acceptance

- Existing commands using `newsletter_ingest.py` and `gmail_newsletter_briefing.py` continue to run without caller changes.
- Existing tests in `test_newsletter_ingest.py` and `test_gmail_newsletter_briefing.py` continue to pass.
- GAS delivery modes (`relay_pull`, webhook, bot token) and secret property names are unchanged.
- Discord output remains under the safe character budget; secondary tags must be clipped or omitted before causing section loss.

### Verification acceptance

- Python unit tests include the fixture cases listed above.
- Targeted Python test command passes:
  - `cd skills/paper-recommender/project && PYTHONPATH=src python3 -m pytest tests/test_newsletter_ingest.py tests/test_gmail_newsletter_briefing.py -q`
- If a new module is added under `project/src`, run the broader project tests or at least all source/taxonomy-adjacent tests.
- GAS syntax check passes through a temporary `.js` copy:
  - `node --check /tmp/newsletter_archive_to_discord.js`
- A parity check records either automated shared-fixture results or a documented manual comparison between Python and GAS outputs.

## Rollout risks and mitigations

- **Taxonomy drift:** use `topic_taxonomy.json` as source of truth and add fixture parity checks before expanding GAS behavior.
- **Output churn:** keep `classify_topic` and existing briefing bullets stable first; add secondary tags as a small tested extension.
- **Privacy leakage:** store rule IDs/term IDs only in `reasons`; never raw body, snippets, article text, tokens, or Script Properties.
- **Context mismatch:** Python has less classification context than GAS today; add optional in-memory `classification_text` only after privacy tests prove it is not persisted.
- **Discord truncation:** keep secondary tags concise and render them after core summary/source lines so truncation does not remove required fields.
- **Over-broad labels:** require threshold and tie-break fixtures before enabling new taxonomy as default.

## Worker handoff notes

- Worker 2 can implement Python first with backward-compatible wrappers and the fixture set above.
- Worker 3 should keep Apps Script parity bounded to classifier constants/wrappers unless Python behavior is already stable; no secret or delivery-mode changes are needed.
- Worker 4 should review false-positive behavior, multi-label determinism, privacy of reasons/tags, GAS/Python drift, and whether tests lock the briefing output contract.
