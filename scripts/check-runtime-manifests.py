#!/usr/bin/env python3
"""Validate Hermes-lite runtime control-plane manifests.

The check is intentionally stdlib-only. It validates the small manifest subset
we currently use rather than being a general YAML parser.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
KIND_RE = re.compile(r"^kind:\s*(\S+)\s*$", re.MULTILINE)
ENTRY_RE = re.compile(r"^  - id:\s*([^\n#]+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^    ([a-zA-Z_][\w-]*):\s*(.*)\s*$")
LIST_ITEM_RE = re.compile(r"^      -\s*(.*?)\s*$")
SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
    re.compile(
        r"(?i)(api[_-]?key|bot[_-]?token|webhook[_-]?url|relay[_-]?read[_-]?token)"
        r"\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
]

REQUIRED_JOB_IDS = {
    "prompt-governance-validate",
    "openclaw-workspace-deploy",
    "openclaw-ops-readiness-check",
    "researchclaw-topic-run",
    "paper-recommender-daily",
    "newsletter-ingest-local-daily",
    "discord-openclaw-bridge-service",
    "discord-jiphyeonjeon-miner-service",
    "jiphyeonjeon-miner-review",
    "jiphyeonjeon-guard-ops-digest",
    "jiphyeonjeon-review-queue-optimizer-report",
    "jiphyeonjeon-newsletter-candidate-orchestrator",
    "jiphyeonjeon-editor-canonical-identity-report",
    "jiphyeonjeon-advisor-evidence-quality-gate",
    "jiphyeonjeon-traveler-source-discovery",
    "discord-card-news-publish",
}

REQUIRED_AGENT_IDS = {
    "workspace-operator",
    "openclaw-ec2-ops",
    "researchclaw",
    "paper-recommender",
    "discord-openclaw-bridge",
    "jiphyeonjeon-miner",
    "jiphyeonjeon-guard",
    "review-queue-optimizer",
    "newsletter-candidate-orchestrator",
    "jiphyeonjeon-editor",
    "jiphyeonjeon-advisor",
    "jiphyeonjeon-traveler",
    "jiphyeonjeon-claw",
    "card-news-publisher",
}


@dataclass(frozen=True)
class Entry:
    id: str
    fields: dict[str, str]
    lists: dict[str, list[str]]



def _validate_yaml_syntax(path: Path, errors: list[str]) -> None:
    """Validate YAML syntax with Ruby's stdlib parser when available.

    Python deliberately has no stdlib YAML parser. The manifests are still
    parsed below as a constrained subset for semantic checks, but this syntax
    pass prevents the deploy gate from accepting malformed YAML when the local
    operator environment has Ruby, which macOS does by default.
    """
    ruby = shutil.which("ruby")
    if ruby:
        proc = subprocess.run(
            [
                ruby,
                "-e",
                "require 'yaml'; YAML.safe_load(File.read(ARGV.fetch(0)), permitted_classes: [], permitted_symbols: [], aliases: false)",
                str(path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            detail = " ".join((proc.stderr or "").split())[:240]
            errors.append(f"{path}: invalid YAML syntax{': ' + detail if detail else ''}")
        return

    # Fallback for minimal environments: catch common malformed flow sequences
    # that the constrained regex parser would otherwise ignore.
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.count("[") != stripped.count("]") or stripped.count("{") != stripped.count("}"):
            errors.append(f"{path}:{lineno}: invalid YAML-like flow syntax")


def _load(path: Path, errors: list[str]) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"missing manifest: {path}")
        return ""
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(text):
            errors.append(f"possible concrete secret value in {path}: pattern={pattern.pattern}")
    return text


def _kind(path: Path, text: str, errors: list[str]) -> str:
    m = KIND_RE.search(text)
    if not m:
        errors.append(f"{path}: missing top-level kind")
        return ""
    return m.group(1)


def _entries(text: str) -> list[Entry]:
    matches = list(ENTRY_RE.finditer(text))
    entries: list[Entry] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        fields: dict[str, str] = {}
        lists: dict[str, list[str]] = {}
        current_list: str | None = None
        for line in block.splitlines()[1:]:
            fm = FIELD_RE.match(line)
            if fm:
                key, value = fm.group(1), fm.group(2).strip()
                fields[key] = value
                current_list = key if value == "" else None
                if current_list:
                    lists.setdefault(current_list, [])
                continue
            lm = LIST_ITEM_RE.match(line)
            if lm and current_list:
                lists.setdefault(current_list, []).append(lm.group(1).strip().strip('"'))
        entries.append(Entry(id=match.group(1).strip().strip('"'), fields=fields, lists=lists))
    return entries


def _validate_ids(label: str, entries: list[Entry], required: set[str], errors: list[str]) -> set[str]:
    ids: list[str] = [entry.id for entry in entries]
    seen: set[str] = set()
    for entry_id in ids:
        if not ID_RE.fullmatch(entry_id):
            errors.append(f"{label}: invalid id {entry_id!r}; use lowercase dash-separated ids")
        if entry_id in seen:
            errors.append(f"{label}: duplicate id {entry_id}")
        seen.add(entry_id)
    missing = sorted(required - seen)
    if missing:
        errors.append(f"{label}: missing required ids: {', '.join(missing)}")
    return seen


def _validate_source_refs(root: Path, entries: list[Entry], errors: list[str]) -> None:
    for entry in entries:
        for raw_ref in entry.lists.get("source_refs", []):
            ref = raw_ref.split("#", 1)[0].strip()
            if not ref or "*" in ref:
                continue
            if not (root / ref).exists():
                errors.append(f"agent {entry.id}: source_ref does not exist: {raw_ref}")


def validate_runtime_manifests(root: Path) -> list[str]:
    errors: list[str] = []
    jobs_path = root / "runtime" / "jobs.yaml"
    agents_path = root / "runtime" / "agents.yaml"
    for manifest_path in (jobs_path, agents_path):
        if manifest_path.exists():
            _validate_yaml_syntax(manifest_path, errors)
    jobs_text = _load(jobs_path, errors)
    agents_text = _load(agents_path, errors)
    if not jobs_text or not agents_text:
        return errors

    if _kind(jobs_path, jobs_text, errors) != "runtime-jobs":
        errors.append(f"{jobs_path}: kind must be runtime-jobs")
    if _kind(agents_path, agents_text, errors) != "runtime-agents":
        errors.append(f"{agents_path}: kind must be runtime-agents")

    job_entries = _entries(jobs_text)
    agent_entries = _entries(agents_text)
    if not job_entries:
        errors.append(f"{jobs_path}: no jobs entries found")
    if not agent_entries:
        errors.append(f"{agents_path}: no agents entries found")

    job_ids = _validate_ids("jobs", job_entries, REQUIRED_JOB_IDS, errors)
    agent_ids = _validate_ids("agents", agent_entries, REQUIRED_AGENT_IDS, errors)

    for job in job_entries:
        owner = job.fields.get("owner_agent", "").strip().strip('"')
        if not owner:
            errors.append(f"job {job.id}: missing owner_agent")
        elif owner not in agent_ids:
            errors.append(f"job {job.id}: owner_agent does not exist in agents.yaml: {owner}")
        if not job.lists.get("command_refs"):
            errors.append(f"job {job.id}: missing command_refs list")
        if "safety" not in job.fields:
            errors.append(f"job {job.id}: missing safety block")

    owned: set[str] = set()
    for agent in agent_entries:
        owns = agent.lists.get("owns_jobs", [])
        if not owns:
            errors.append(f"agent {agent.id}: missing owns_jobs list")
        for job_id in owns:
            if job_id not in job_ids:
                errors.append(f"agent {agent.id}: owns unknown job: {job_id}")
            owned.add(job_id)
        if "kind" not in agent.fields:
            errors.append(f"agent {agent.id}: missing kind")
        if not agent.lists.get("boundaries"):
            errors.append(f"agent {agent.id}: missing boundaries list")

    orphan_jobs = sorted(job_ids - owned)
    if orphan_jobs:
        errors.append(f"jobs not referenced by any agent owns_jobs: {', '.join(orphan_jobs)}")

    _validate_source_refs(root, agent_entries, errors)
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[1]
    errors = validate_runtime_manifests(root)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("runtime manifest check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
