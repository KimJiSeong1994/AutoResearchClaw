"""Propose scoring-config changes from the outcome ledger, and apply them under a gate.

Tuning the confidence weights would be theatre. `decide_evidence` makes every
accept/reject call *before* it computes a score (blocked, fetch failed, no
metadata, topic terms with no keyword match), and the only consumers of the
score are a duplicate tie-break and the number printed in the daily report. So
`base_confidence` and friends change what the operator reads, not what the
traveler finds.

What actually changes outcomes is the curated portfolio: which static sources
get crawled when the providers rate-limit. Those are attributable, because every
ledger observation carries the URL.

The one proposal here is not statistical inference. A reviewer rejecting a
source candidate has already said "do not collect from here", so the tuner
carries that decision into the portfolio rather than re-deriving it. Inferring
quality from adoption rates was considered and dropped: the operator sees the
traveler's own score before deciding, so adoption partly measures trust in that
score, which is not a basis for automatically narrowing what the traveler may
find. The proposer therefore refuses far more often than it fires.

Applying is a separate, explicit step. Nothing here runs unattended: the daily
cron may write the ledger, but a config change waits for a human who ran
`propose`, read the evidence, and passed `--confirm`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._shared import _read_jsonl_rows
from .config import _load_dotenv
from .traveler_outcomes import EVENT_ADOPTED, EVENT_OBSERVED, EVENT_REVIEWED, default_ledger_path

LOG = logging.getLogger(__name__)

SCHEMA_VERSION = "traveler-tuning.v1"
STATIC_PROVIDER = "static-technical-sources"

# A source appears in the ledger many times but is reviewed once, so "how many
# rejections" is the wrong question. The signal that matters is whether a human
# ever ruled on it. This only requires that the URL genuinely shows up as a
# discovery, so a config typo cannot be "dropped" on the strength of no data.
MIN_OBSERVATIONS_PER_SOURCE = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize_ledger(ledger_path: Path) -> dict[str, dict[str, Any]]:
    """Per-URL outcome counts: observations, adoptions, and explicit verdicts."""
    rows = _read_jsonl_rows(ledger_path)
    adopted = {str(r.get("url_key") or "") for r in rows if r.get("event") == EVENT_ADOPTED}
    verdicts: dict[str, str] = {
        str(r.get("url_key") or ""): str(r.get("verdict") or "")
        for r in rows
        if r.get("event") == EVENT_REVIEWED
    }
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("event") != EVENT_OBSERVED:
            continue
        key = str(row.get("url_key") or "")
        url = str(row.get("url") or "")
        if not key or not url:
            continue
        entry = summary.setdefault(
            url,
            {"url_key": key, "provider": str(row.get("provider") or ""), "observations": 0, "adopted": 0, "approved": 0, "rejected": 0, "reviews": 0},
        )
        entry["observations"] += 1
        entry["adopted"] = int(key in adopted)
        verdict = verdicts.get(key, "")
        entry["approved"] = int(verdict == "approve")
        entry["rejected"] = int(verdict == "reject")
        entry["reviews"] = int(verdict in {"approve", "reject"})
    return summary


def propose_changes(ledger_path: Path, scoring: dict[str, Any]) -> dict[str, Any]:
    """Config proposals with their evidence, or refusals with the reason why.

    Only one proposal exists, and it is not an inference: a reviewer rejecting a
    source candidate has already said "do not collect from here", so the tuner
    carries that decision into the portfolio. Inferring quality from adoption
    rates was considered and dropped — adoption is confounded by the operator
    seeing the traveler's own score before deciding, so it cannot support an
    automatic narrowing of what the traveler is allowed to find.
    """
    summary = summarize_ledger(ledger_path)
    proposals: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    configured = {str(row[1]): row for row in scoring.get("static_sources", []) if isinstance(row, list) and len(row) == 4}

    for url, row in configured.items():
        stats = summary.get(url, {"observations": 0, "adopted": 0, "approved": 0, "rejected": 0, "reviews": 0})
        if stats["observations"] < MIN_OBSERVATIONS_PER_SOURCE:
            refusals.append({"target": url, "reason": "never_observed", "detail": "no ledger observation, so nothing links this config row to a real discovery"})
            continue
        if stats["reviews"] == 0:
            refusals.append({"target": url, "reason": "unreviewed", "detail": "no verdict recorded; an unreviewed source is not a rejected one"})
            continue
        if stats["approved"] or stats["adopted"]:
            refusals.append({"target": url, "reason": "approved_or_adopted", "detail": "the record supports keeping this source"})
            continue
        if stats["rejected"]:
            proposals.append({
                "action": "drop_static_source",
                "target": url,
                "title": str(row[0]),
                "source_type": str(row[2]),
                "evidence": dict(stats),
                "rationale": "a reviewer rejected this source candidate; dropping it carries out that decision",
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "ledger_path": str(ledger_path),
        "sources_configured": len(configured),
        "sources_with_outcomes": sum(1 for url in configured if url in summary),
        "proposals": proposals,
        "refusals": refusals,
        "automatic_apply": False,
        "notes": [
            "confidence weights are deliberately not tuned: decide_evidence rejects before scoring, so those weights change the displayed number and duplicate tie-break, not what gets found",
            "a source with no verdict is unreviewed, not rejected; silence never justifies dropping it",
            "adoption rates are not used to propose changes: the operator sees the traveler's score before deciding, so adoption partly measures trust in that score",
            "proposals narrow what the traveler may find, so applying them stays a human step",
        ],
    }


def apply_proposals(
    *,
    scoring_path: Path,
    proposals: list[dict[str, Any]],
    baseline_sha256: str,
    lineage_path: Path | None = None,
) -> dict[str, Any]:
    """Apply accepted proposals to the scoring config, hash-anchored with a backup.

    Refuses if the config changed since `propose` read it, so a stale proposal
    cannot silently overwrite an edit made in between.
    """
    current = _sha256_file(scoring_path)
    if current != baseline_sha256:
        raise ValueError(f"scoring config changed since proposal was generated (expected {baseline_sha256[:12]}, found {current[:12]})")
    if not proposals:
        raise ValueError("no proposals to apply")

    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    drop_urls = {str(p["target"]) for p in proposals if p.get("action") == "drop_static_source"}

    kept = [row for row in scoring.get("static_sources", []) if not (isinstance(row, list) and len(row) == 4 and str(row[1]) in drop_urls)]
    if not kept:
        raise ValueError("refusing to empty the static portfolio; the traveler would lose its fallback when providers rate-limit")
    scoring["static_sources"] = kept

    backup = scoring_path.with_suffix(scoring_path.suffix + ".bak")
    shutil.copy2(scoring_path, backup)
    scoring_path.write_text(json.dumps(scoring, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = {
        "schema_version": SCHEMA_VERSION,
        "applied_at": _utc_now(),
        "scoring_path": str(scoring_path),
        "backup_path": str(backup),
        "before_sha256": baseline_sha256,
        "after_sha256": _sha256_file(scoring_path),
        "dropped_sources": sorted(drop_urls),
        "sources_remaining": len(kept),
    }
    if lineage_path is not None:
        lineage_path.parent.mkdir(parents=True, exist_ok=True)
        with lineage_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def _default_scoring_path() -> Path:
    from .traveler_evidence import _scoring_path

    return _scoring_path()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-traveler-tune",
        description="Propose Traveler scoring-config changes from recorded outcomes; apply only with --confirm.",
    )
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--scoring", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("propose", help="Print proposals and refusals. Changes nothing.")
    apply_cmd = sub.add_parser("apply", help="Apply proposals. Requires --confirm.")
    apply_cmd.add_argument("--confirm", action="store_true", help="Required. Without it this is a dry run.")
    apply_cmd.add_argument("--lineage", type=Path, default=None, help="Append an applied-change record here.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    ledger = (args.ledger or default_ledger_path()).expanduser()
    scoring_path = (args.scoring or _default_scoring_path()).expanduser()
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    report = propose_changes(ledger, scoring)

    if args.command == "propose":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not report["proposals"]:
        print("no proposals met the evidence guards; nothing to apply", file=sys.stderr)
        return 1
    if not args.confirm:
        print(json.dumps({**report, "dry_run": True, "hint": "re-run with --confirm to apply"}, ensure_ascii=False, indent=2))
        return 0

    record = apply_proposals(
        scoring_path=scoring_path,
        proposals=report["proposals"],
        baseline_sha256=_sha256_file(scoring_path),
        lineage_path=args.lineage.expanduser() if args.lineage else None,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
