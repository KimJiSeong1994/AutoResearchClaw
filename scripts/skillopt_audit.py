#!/usr/bin/env python3
"""SkillOpt readiness audit for AutoResearchClaw agent skills.

This is a local, side-effect-light control-plane auditor. It inventories Codex
and runtime skill surfaces, maps them to runtime agents/jobs, and imports
PaperWiki evidence metadata without leaking absolute local vault paths or note
bodies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "skillopt-audit.v1"
ROOT_KINDS = {
    "codex_skill": ".codex/skills",
    "runtime_skill": "skills",
    "runtime_readme": "skills",
}

SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
    re.compile(
        r"(?i)(api[_-]?key|bot[_-]?token|webhook[_-]?url|relay[_-]?read[_-]?token)"
        r"\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
]
FORBIDDEN_PAPERWIKI_OUTPUT_PATTERNS = [
    re.compile(r"/Users/"),
    re.compile(r"(?m)(?:^|\s)~/"),
    re.compile(r"Mobile Documents"),
    *SECRET_VALUE_PATTERNS,
]

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
ENTRY_RE = re.compile(r"^  - id:\s*([^\n#]+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^    ([a-zA-Z_][\w-]*):\s*(.*)\s*$")
LIST_ITEM_RE = re.compile(r"^      -\s*(.*?)\s*$")

DIMENSION_ALIASES: dict[str, tuple[str, ...]] = {
    "trigger": (
        "description",
        "when to use",
        "use this skill",
        "입력 확인",
        "사용 조건",
        "적용 대상",
    ),
    "input_contract": ("input", "inputs", "required input", "입력", "입력 확인", "필수 메타"),
    "output_contract": ("output", "output format", "deliverables", "출력 구조", "산출물 구조", "agent output format"),
    "workflow": ("workflow", "steps", "procedure", "워크플로우", "작성 원칙", "근거 수집"),
    "safety_privacy": (
        "safety",
        "boundaries",
        "privacy",
        "reject content",
        "경계",
        "작성 원칙",
        "공개/비공개 분리",
        "금지",
    ),
    "verification": ("verification", "checklist", "tests", "검증", "검증 명령", "완료 체크리스트"),
    "evidence_citation": ("evidence", "citation", "source", "근거", "출처", "근거 표"),
    "failure_rollback": ("failure", "rollback", "fallback", "실패", "롤백", "한계와 주의"),
    "progressive_disclosure": ("progressive disclosure", "references/", "필요할 때만", "근거 참조"),
}

GAP_DEFINITIONS = {
    "ambiguous_trigger": "No clear trigger/description section detected.",
    "missing_input_contract": "No input contract section detected.",
    "weak_output_contract": "No output contract section detected.",
    "missing_workflow": "No workflow/procedure section detected.",
    "privacy_boundary_missing": "No safety/privacy boundary section detected.",
    "missing_verification": "No verification/checklist section detected.",
    "missing_evidence_policy": "No evidence/citation policy signal detected.",
    "missing_failure_rollback": "No failure/rollback/fallback signal detected.",
    "missing_progressive_disclosure_signal": "Codex skill has no progressive-disclosure/reference-use signal.",
    "runtime_unmapped": "No runtime agent/job reference found for this skill surface.",
    "paperwiki_evidence_unsanitized": "PaperWiki evidence contains forbidden local path/secret/body-like signal.",
}


@dataclass(frozen=True)
class ManifestEntry:
    id: str
    fields: dict[str, str]
    lists: dict[str, list[str]]


@dataclass(frozen=True)
class SkillRecord:
    root_kind: str
    name: str
    path: str
    description: str
    headings: list[str]
    content_sha256: str
    dimensions: dict[str, dict[str, Any]]
    gaps: list[dict[str, str]]
    runtime_refs: list[str]


def relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    result: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        if not raw_line.strip() or raw_line.startswith(" ") or raw_line.startswith("-"):
            continue
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def headings(text: str) -> list[str]:
    return [m.group(2).strip() for m in HEADING_RE.finditer(text)]


def infer_name(path: Path, root: Path, frontmatter: dict[str, str], text: str) -> str:
    if frontmatter.get("name"):
        return frontmatter["name"]
    if frontmatter.get("title"):
        return frontmatter["title"]
    for heading in headings(text):
        return heading
    try:
        return path.parent.relative_to(root).as_posix() if path.name.upper() == "README.MD" else path.parent.name
    except ValueError:
        return path.stem


def normalize_dimensions(text: str, frontmatter: dict[str, str]) -> dict[str, dict[str, Any]]:
    hs = headings(text)
    haystacks = [h.lower() for h in hs]
    if frontmatter.get("description"):
        haystacks.append("description")
    out: dict[str, dict[str, Any]] = {}
    for dimension, aliases in DIMENSION_ALIASES.items():
        matched: list[str] = []
        for alias in aliases:
            alias_l = alias.lower()
            for original, lower in zip(hs + (["description"] if frontmatter.get("description") else []), haystacks):
                if alias_l == lower or alias_l in lower:
                    matched.append(original)
        # Additional weak content signals for policy-like dimensions.
        text_lower = text.lower()
        if not matched:
            if dimension == "safety_privacy" and any(token in text_lower for token in ("secret", "token", "private", "비공개", "내부", "do not")):
                matched.append("content-signal:safety_privacy")
            elif dimension == "verification" and any(token in text_lower for token in ("pytest", "unittest", "validate", "검증")):
                matched.append("content-signal:verification")
            elif dimension == "evidence_citation" and any(token in text_lower for token in ("evidence", "source", "출처", "근거", "citation")):
                matched.append("content-signal:evidence_citation")
            elif dimension == "failure_rollback" and any(token in text_lower for token in ("fallback", "rollback", "실패", "한계")):
                matched.append("content-signal:failure_rollback")
            elif dimension == "progressive_disclosure" and any(token in text_lower for token in ("references/", "필요할 때만", "when needed")):
                matched.append("content-signal:progressive_disclosure")
        out[dimension] = {
            "present": bool(matched),
            "matched_headings": sorted(set(matched)),
            "confidence": "high" if matched and not matched[0].startswith("content-signal:") else ("medium" if matched else "none"),
        }
    return out


def gap(code: str, severity: str = "warning") -> dict[str, str]:
    return {"gap_code": code, "severity": severity, "message": GAP_DEFINITIONS[code]}


def compute_gaps(root_kind: str, dimensions: dict[str, dict[str, Any]], runtime_refs: list[str]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    required = {
        "trigger": "ambiguous_trigger",
        "input_contract": "missing_input_contract",
        "output_contract": "weak_output_contract",
        "workflow": "missing_workflow",
        "safety_privacy": "privacy_boundary_missing",
        "verification": "missing_verification",
    }
    for dimension, code in required.items():
        if not dimensions[dimension]["present"]:
            gaps.append(gap(code, "warning" if dimension in {"trigger", "verification"} else "needs_review"))
    phase1_edge = {
        "evidence_citation": "missing_evidence_policy",
        "failure_rollback": "missing_failure_rollback",
    }
    for dimension, code in phase1_edge.items():
        if not dimensions[dimension]["present"]:
            gaps.append(gap(code, "needs_review"))
    if root_kind == "codex_skill" and not dimensions["progressive_disclosure"]["present"]:
        gaps.append(gap("missing_progressive_disclosure_signal", "needs_review"))
    if not runtime_refs:
        gaps.append(gap("runtime_unmapped", "needs_review"))
    return gaps


def parse_manifest_entries(text: str) -> list[ManifestEntry]:
    matches = list(ENTRY_RE.finditer(text))
    entries: list[ManifestEntry] = []
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
                fields[key] = value.strip('"')
                current_list = key if value == "" else None
                if current_list:
                    lists.setdefault(current_list, [])
                continue
            lm = LIST_ITEM_RE.match(line)
            if lm and current_list:
                lists.setdefault(current_list, []).append(lm.group(1).strip().strip('"'))
        entries.append(ManifestEntry(id=match.group(1).strip().strip('"'), fields=fields, lists=lists))
    return entries


def load_manifest(path: Path) -> tuple[str, list[ManifestEntry]]:
    if not path.exists():
        return "", []
    text = path.read_text(encoding="utf-8")
    return text, parse_manifest_entries(text)


def collect_runtime_refs(root: Path, agents_path: Path, jobs_path: Path) -> dict[str, list[str]]:
    refs: dict[str, set[str]] = {}
    agents_text, agents = load_manifest(agents_path)
    jobs_text, jobs = load_manifest(jobs_path)
    agent_owned_jobs: dict[str, set[str]] = {}

    for entry in agents:
        owned = set(entry.lists.get("owns_jobs", []))
        agent_owned_jobs[entry.id] = owned
        label = f"agent:{entry.id}"
        owned_labels = {f"job:{job_id}" for job_id in owned}
        for raw in entry.lists.get("source_refs", []):
            ref = raw.split("#", 1)[0].strip()
            if ref and "*" not in ref:
                refs.setdefault(ref, set()).add(label)
                refs[ref].update(owned_labels)
        for job_id in owned:
            refs.setdefault(f"job:{job_id}", set()).add(label)

    for entry in jobs:
        label = f"job:{entry.id}"
        owner = entry.fields.get("owner_agent")
        if owner:
            refs.setdefault(f"agent:{owner}", set()).add(label)
        for key in ("command_refs", "checks", "outputs", "pre_publish_checks"):
            for raw in entry.lists.get(key, []):
                ref = raw.split("#", 1)[0].strip().split(" ", 1)[0]
                if ref and "*" not in ref and (root / ref).exists():
                    refs.setdefault(ref, set()).add(label)
    # source_scope is a manifest-level list; record as metadata coverage.
    for source, text in (("runtime/agents.yaml", agents_text), ("runtime/jobs.yaml", jobs_text)):
        for line in text.splitlines():
            stripped = line.strip().strip('"')
            if stripped.startswith("- "):
                value = stripped[2:].strip().strip('"')
                if value.endswith("SKILL.md") or value.endswith("README.md"):
                    refs.setdefault(value, set()).add(f"metadata_source_scope:{source}")
    return {k: sorted(v) for k, v in refs.items()}


def runtime_refs_for(path: str, refs: dict[str, list[str]]) -> list[str]:
    direct = set(refs.get(path, []))
    # README anchors and broad command refs can point to parent skill directory.
    parent = str(Path(path).parent)
    for ref, labels in refs.items():
        if ref.startswith(parent + "/") or path.startswith(str(Path(ref).parent) + "/"):
            direct.update(labels)
    return sorted(direct)


def iter_skill_files(codex_skills: Path, runtime_skills: Path) -> list[tuple[str, Path, Path]]:
    found: list[tuple[str, Path, Path]] = []
    if codex_skills.exists():
        for path in sorted(codex_skills.glob("*/SKILL.md")):
            found.append(("codex_skill", codex_skills, path))
    if runtime_skills.exists():
        for path in sorted(runtime_skills.glob("*/SKILL.md")):
            found.append(("runtime_skill", runtime_skills, path))
        for path in sorted(runtime_skills.glob("*/README.md")):
            found.append(("runtime_readme", runtime_skills, path))
    return found


def audit_skills(root: Path, codex_skills: Path, runtime_skills: Path, agents: Path, jobs: Path) -> list[SkillRecord]:
    refs = collect_runtime_refs(root, agents, jobs)
    records: list[SkillRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for root_kind, skill_root, path in iter_skill_files(codex_skills, runtime_skills):
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        name = infer_name(path, skill_root, fm, text)
        relative = relpath(path, root)
        dedup = (root_kind, name.lower(), relative)
        if dedup in seen:
            continue
        seen.add(dedup)
        dims = normalize_dimensions(text, fm)
        rt_refs = runtime_refs_for(relative, refs)
        records.append(
            SkillRecord(
                root_kind=root_kind,
                name=name,
                path=relative,
                description=fm.get("description", ""),
                headings=headings(text),
                content_sha256=sha256_text(text),
                dimensions=dims,
                gaps=compute_gaps(root_kind, dims, rt_refs),
                runtime_refs=rt_refs,
            )
        )
    return records


def sanitize_paperwiki_path(raw: str, vault_root: str | None) -> tuple[str, bool]:
    value = raw.strip().strip("`").strip()
    had_forbidden = any(pattern.search(value) for pattern in FORBIDDEN_PAPERWIKI_OUTPUT_PATTERNS)
    if vault_root and value.startswith(vault_root):
        value = value[len(vault_root) :].lstrip("/")
    if "/PaperWiki/PaperWiki/" in value:
        value = value.split("/PaperWiki/PaperWiki/", 1)[1]
    if any(pattern.search(value) for pattern in FORBIDDEN_PAPERWIKI_OUTPUT_PATTERNS):
        # Do not echo absolute local paths or secret-like basenames even in
        # failing reports. A fixed redaction preserves the diagnostic gap
        # without leaking private vault filenames or credential-looking text.
        return "redacted-paperwiki-path", True
    return value, had_forbidden and not (value.startswith("pages/") or value.startswith("raw/"))


def display_input_path(raw: str, root: Path) -> str:
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.name
    return raw


def sanitize_evidence_text(value: str, extra_forbidden: list[re.Pattern[str]] | None = None) -> tuple[str, bool]:
    forbidden = [*FORBIDDEN_PAPERWIKI_OUTPUT_PATTERNS, *(extra_forbidden or [])]
    failed = any(pattern.search(value) for pattern in forbidden)
    if failed:
        return "[redacted-paperwiki-metadata]", True
    return value, False


def sanitize_evidence_list(values: list[str], extra_forbidden: list[re.Pattern[str]] | None = None) -> tuple[list[str], bool]:
    sanitized: list[str] = []
    failed = False
    for value in values:
        safe, item_failed = sanitize_evidence_text(value, extra_forbidden)
        failed = failed or item_failed
        if safe not in sanitized:
            sanitized.append(safe)
    return sanitized, failed


def parse_paperwiki_evidence(path: Path | None, vault_root: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if path is None or not path.exists():
        return [], []
    text = path.read_text(encoding="utf-8")
    extra_forbidden = [re.compile(re.escape(vault_root))] if vault_root else []
    findings: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    current: dict[str, Any] | None = None
    raw_sanitization_failed = False
    for line in text.splitlines():
        rank = re.match(r"^##\s+\d+\.\s+(.+)$", line)
        if rank:
            if current:
                findings.append(current)
            title, failed = sanitize_evidence_text(rank.group(1).strip(), extra_forbidden)
            raw_sanitization_failed = raw_sanitization_failed or failed
            current = {"title": title, "path": "", "tags": [], "reasons": []}
            continue
        if current is None:
            continue
        if line.startswith("- path:"):
            raw = line.split(":", 1)[1].strip().strip("`")
            sanitized, failed = sanitize_paperwiki_path(raw, vault_root)
            current["path"] = sanitized
            raw_sanitization_failed = raw_sanitization_failed or failed
        elif line.startswith("- tags:"):
            tags, failed = sanitize_evidence_list(re.findall(r"`([^`]+)`", line), extra_forbidden)
            raw_sanitization_failed = raw_sanitization_failed or failed
            current["tags"] = tags
        elif line.startswith("- reasons:"):
            reasons, failed = sanitize_evidence_list(re.findall(r"`([^`]+)`", line), extra_forbidden)
            raw_sanitization_failed = raw_sanitization_failed or failed
            current["reasons"] = reasons
    if current:
        findings.append(current)

    serialized = json.dumps(findings, ensure_ascii=False, sort_keys=True)
    forbidden = list(FORBIDDEN_PAPERWIKI_OUTPUT_PATTERNS)
    if vault_root:
        forbidden.append(re.compile(re.escape(vault_root)))
    for pattern in forbidden:
        if pattern.search(serialized):
            raw_sanitization_failed = True
            break
    if raw_sanitization_failed:
        gaps.append(gap("paperwiki_evidence_unsanitized", "error"))
    return findings, gaps


def build_gap_matrix(records: list[SkillRecord], evidence_gaps: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        rows.append(
            {
                "path": record.path,
                "root_kind": record.root_kind,
                "name": record.name,
                "gap_codes": [g["gap_code"] for g in record.gaps],
                "runtime_refs": record.runtime_refs,
            }
        )
    if evidence_gaps:
        rows.append(
            {
                "path": "paperwiki_evidence",
                "root_kind": "paperwiki_evidence",
                "name": "PaperWiki evidence import",
                "gap_codes": [g["gap_code"] for g in evidence_gaps],
                "runtime_refs": [],
            }
        )
    return rows


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# SkillOpt Audit Report",
        "",
        f"- schema_version: `{report['schema_version']}`",
        f"- generated_at: `{report['generated_at']}`",
        f"- skills: {len(report['skills'])}",
        "",
        "## Gap Matrix",
        "",
        "| Skill | Root | Gaps | Runtime refs |",
        "|---|---|---|---|",
    ]
    for row in report["gap_matrix"]:
        lines.append(
            f"| `{row['path']}` | `{row['root_kind']}` | {', '.join(row['gap_codes']) or 'none'} | {', '.join(row['runtime_refs']) or 'none'} |"
        )
    if report.get("paperwiki_evidence"):
        lines += ["", "## PaperWiki Evidence Map", ""]
        for item in report["paperwiki_evidence"]:
            lines.append(f"- `{item.get('path','')}` — {item.get('title','')}")
    return "\n".join(lines) + "\n"


def make_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    generated_at = args.as_of or datetime.now(timezone.utc).isoformat()
    codex_skills = (root / args.codex_skills).resolve()
    runtime_skills = (root / args.runtime_skills).resolve()
    agents = (root / args.agents).resolve()
    jobs = (root / args.jobs).resolve()
    records = audit_skills(root, codex_skills, runtime_skills, agents, jobs)
    evidence, evidence_gaps = parse_paperwiki_evidence(Path(args.paperwiki_evidence) if args.paperwiki_evidence else None, args.paperwiki_vault_root)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "inputs": {
            "codex_skills": display_input_path(args.codex_skills, root),
            "runtime_skills": display_input_path(args.runtime_skills, root),
            "agents": display_input_path(args.agents, root),
            "jobs": display_input_path(args.jobs, root),
            "paperwiki_evidence": display_input_path(args.paperwiki_evidence, root),
        },
        "root_policy": {
            "scan_roots": [".codex/skills/*/SKILL.md", "skills/*/SKILL.md", "skills/*/README.md"],
            "dedup_key": ["root_kind", "normalized_name", "relative_path"],
            "auto_merge": False,
        },
        "gap_definitions": GAP_DEFINITIONS,
        "skills": [asdict(record) for record in records],
        "paperwiki_evidence": evidence,
        "paperwiki_evidence_gaps": evidence_gaps,
        "gap_matrix": build_gap_matrix(records, evidence_gaps),
    }


def write_report(report: dict[str, Any], out: Path, also_markdown: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if also_markdown:
        out.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SkillOpt readiness audit")
    parser.add_argument("--root", default=".")
    parser.add_argument("--codex-skills", default=".codex/skills")
    parser.add_argument("--runtime-skills", default="skills")
    parser.add_argument("--agents", default="runtime/agents.yaml")
    parser.add_argument("--jobs", default="runtime/jobs.yaml")
    parser.add_argument("--paperwiki-evidence", default="")
    parser.add_argument("--paperwiki-vault-root", default="")
    parser.add_argument("--as-of", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--markdown", action="store_true", help="also write a markdown report next to --out")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    report = make_report(args)
    if args.out:
        write_report(report, Path(args.out), args.markdown)
    if args.format == "markdown":
        sys.stdout.write(markdown_report(report))
    else:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 1 if report["paperwiki_evidence_gaps"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
