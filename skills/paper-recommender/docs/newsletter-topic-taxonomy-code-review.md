# Newsletter topic taxonomy implementation review

Date: 2026-05-04  
Reviewer: OMX worker-4  
Scope: score-based topic taxonomy for newsletter briefings across Python ingest and Google Apps Script briefing paths.

## Changed files reviewed

- `skills/paper-recommender/docs/current-topic-rules-briefing-evidence.md`
- `skills/paper-recommender/docs/newsletter-topic-taxonomy-plan.md`
- `skills/paper-recommender/newsletter_ingest.py`
- `skills/paper-recommender/project/tests/test_newsletter_ingest.py`
- `skills/paper-recommender/gmail_newsletter_briefing.py`
- `skills/paper-recommender/project/tests/test_gmail_newsletter_briefing.py`
- `integrations/google-apps-script/newsletter_archive_to_discord.gs`

## Review findings

## Post-review resolution

The rollout implementation now addresses the blocking findings identified below:

- Python and Apps Script both use score-based token/phrase matching instead of ordered arbitrary substring first-match.
- Python tests lock the known false-positive cases (`research` vs `search`) and multi-signal cases (`RAG + agent`, GitHub source, VLM/video, safety/eval).
- Python `include_all_urls=True` now still filters private utility links such as unsubscribe/account/preferences URLs before raw/page output.
- Python briefing output preserves the existing Korean bullet contract while adding compact sanitized topic evidence in the `기술 포인트` line.
- Apps Script deployment guidance now requires taxonomy parity smoke checks before production trigger rollout.

Remaining rollout gap: the current Apps Script parity check is a local Node VM smoke, not a live Apps Script deployment test.

### High severity

1. **Score-based taxonomy is not implemented yet.**
   - Evidence: `skills/paper-recommender/newsletter_ingest.py:311-320` and `integrations/google-apps-script/newsletter_archive_to_discord.gs:874-882` still return the first substring match from ordered rules.
   - Why it matters: the design in `skills/paper-recommender/docs/newsletter-topic-taxonomy-plan.md` calls for weighted scoring, token/phrase boundaries, tie-breaks, and secondary labels. Current behavior cannot satisfy the acceptance goal for researcher-facing, score-based topic taxonomy.
   - Recommended fix: introduce a shared taxonomy fixture/spec plus a Python score result shape such as `primary`, `primary_display`, `secondary`, `confidence`, and `reasons`; keep the legacy `topic` field mapped to `primary_display` for compatibility.

2. **Current Python classifier has reproducible false positives from substring matching.**
   - Evidence: `skills/paper-recommender/newsletter_ingest.py:53-61` includes `search`, `repo`, `benchmark`, and `market` as raw substrings; `skills/paper-recommender/newsletter_ingest.py:312-317` searches the combined title/kind/url haystack.
   - Reproduced locally:
     - `New research paper on foundation models` -> `검색/RAG/지식그래프` because `search` matches inside `research`.
     - `OpenAI pricing and market update` with kind `research-post` -> `검색/RAG/지식그래프` because `search` matches inside `research-post` before pricing/market rules.
   - Recommended fix: use token/phrase boundary matching and keep source kind signals separate from natural-language text.

3. **Python `--include-all-urls` has weaker privacy filtering than the Apps Script path.**
   - Evidence: Python `select_items()` persists all extracted URLs when `include_all_urls=True` (`skills/paper-recommender/newsletter_ingest.py:225-240`, `:297-303`, CLI flag at `:438-442`). Apps Script filters private utility URLs before inclusion (`integrations/google-apps-script/newsletter_archive_to_discord.gs:113-114`, `:850-856`).
   - Why it matters: tokenized unsubscribe/tracking/account URLs can be written to raw/page output if a sanitized export is not actually sanitized.
   - Recommended fix: port a conservative `is_private_utility_url()` guard to Python and apply it before both default and include-all URL selection.

### Medium severity

4. **Briefing output does not expose primary/secondary taxonomy evidence.**
   - Evidence: Python briefing emits title, kind, sender, received date, and URL (`skills/paper-recommender/newsletter_ingest.py:360-371`) but no primary slug, secondary tags, confidence, or match reasons.
   - Why it matters: a researcher cannot audit why an item landed in a group or use secondary tags for longitudinal trend tracking.
   - Recommended fix: render a compact tags line like `primary=...; tags=...; confidence=...`; avoid body text in the rendered output.

5. **Python and Apps Script taxonomy behavior can drift.**
   - Evidence: both runtimes duplicate rule arrays (`skills/paper-recommender/newsletter_ingest.py:53-61`, `integrations/google-apps-script/newsletter_archive_to_discord.gs:58-66`) and classify from different evidence surfaces. Python uses title/kind/url only; Apps Script uses subject/body/article detail/url (`integrations/google-apps-script/newsletter_archive_to_discord.gs:125-162`).
   - Recommended fix: add a shared fixture with expected primary/secondary outputs and a parity check. If generating GAS constants from a shared spec is too much for the first rollout, make the fixture the compatibility gate.

6. **Apps Script can report collected items but render no detailed cards when article details are sparse.**
   - Evidence: `renderBriefing_()` counts all items (`integrations/google-apps-script/newsletter_archive_to_discord.gs:186`) but skips groups with no `detailedItems_()` (`:201-205`). The detail filter depends on public article detail / summary quality (`:523-540`).
   - Recommended fix: add a privacy-safe fallback based on title, URL, kind, and source context rather than dropping the item entirely.

### Test adequacy gaps

- Existing tests pass, but taxonomy coverage is still happy-path only.
- Missing regression cases:
  - `research` must not match `search`.
  - `report` must not match `repo`.
  - `benchmark` must not accidentally select market/infra by substring/order instead of scoring intent.
  - `RAG + agent + GitHub` should preserve deterministic primary and secondary tags.
  - Python/GAS parity fixture should cover known false positives and multi-signal examples.

## Suitability for researcher briefing

The documentation correctly identifies the target shape for researcher workflows: primary groups plus secondary tags and explicit score reasoning. The current implementation remains useful for coarse grouping, but it is not yet sufficient for a score-based researcher briefing because it hides classification evidence and loses multi-label context.

## Rollout risks

- **Label churn:** switching directly from Korean legacy topic labels to a new primary/secondary taxonomy may disrupt downstream consumers. Keep `topic` as a compatibility display field during rollout.
- **Privacy regression:** scoring over body/snippet can improve accuracy, but rendered/raw artifacts must continue to omit full email bodies and private URLs.
- **Python/GAS inconsistency:** production Discord briefings may come from GAS while local archives come from Python. A parity fixture should be required before enabling the new classifier in both paths.
- **Discord length pressure:** secondary tags and reasons should be compact to stay within the existing safe character limit.

## Verification run

- `cd skills/paper-recommender/project && uv run --with pytest pytest -q tests/test_newsletter_ingest.py tests/test_gmail_newsletter_briefing.py` -> `9 passed in 0.04s`
- `cd skills/paper-recommender/project && uv run --with pytest pytest -q` -> `225 passed in 0.84s`
- `python3 -m compileall -q skills/paper-recommender/newsletter_ingest.py skills/paper-recommender/gmail_newsletter_briefing.py skills/paper-recommender/project/src` -> passed
- `tmp=$(mktemp /tmp/newsletter_archive_to_discord.XXXXXX).js; cp integrations/google-apps-script/newsletter_archive_to_discord.gs "$tmp"; node --check "$tmp"` -> passed
- `cd skills/paper-recommender/project && uv run --with ruff ruff check ../newsletter_ingest.py ../gmail_newsletter_briefing.py tests/test_newsletter_ingest.py tests/test_gmail_newsletter_briefing.py` -> `All checks passed!`
