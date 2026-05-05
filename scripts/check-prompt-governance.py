#!/usr/bin/env python3
"""Validate Jiphyeonjeon-Claw prompt governance artifacts.

The check is intentionally stdlib-only so it can run before deploying the
OpenClaw workspace to EC2.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_LIFECYCLE = {
    "inventory",
    "draft",
    "review",
    "evaluate",
    "deploy",
    "monitor",
    "rollback",
}

REQUIRED_STATUS_FIELDS = {
    "run_id",
    "run_at_utc",
    "pipeline",
    "prompt_version",
    "model_primary",
    "fallback_used",
    "source_stats",
    "candidate_count",
    "evidence_coverage_pct",
    "prompt_output_valid_json",
    "secret_scan_pass",
    "delivery_target",
    "delivery_message_count",
    "artifact_dir",
    "health_status",
}

OPTIONAL_STATUS_FIELDS = {
    "model_fallback",
    "fallback_reason",
    "query_count",
    "cluster_count",
    "min_evidence_per_cluster",
    "score_stats",
    "prompt_input_bytes",
    "raw_path",
    "soul_source",
    "soul_fallback_used",
    "soul_card_sha256",
}

KNOWN_STATUS_FIELDS = REQUIRED_STATUS_FIELDS | OPTIONAL_STATUS_FIELDS

REQUIRED_PROMPT_FIELDS = {
    "prompt_id",
    "owner",
    "purpose",
    "source",
    "service_surface",
    "input_classes",
    "forbidden_data",
    "output_contract",
    "validation",
    "metrics",
    "rollback",
}

PROMPT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Match likely concrete secret values, not safe environment/property names such
# as DISCORD_BOT_TOKEN or RELAY_READ_TOKEN.
SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(
        r"(?i)(api[_-]?key|bot[_-]?token|webhook[_-]?url|relay[_-]?read[_-]?token)"
        r"\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
]


def _non_empty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(_non_empty(item) for item in value)
    if isinstance(value, dict):
        return bool(value)
    return value is not None


def _load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing registry: {path}")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(f"invalid json: {path}:{exc.lineno}:{exc.colno}: {exc.msg}")
        return {}
    if not isinstance(data, dict):
        errors.append(f"registry root must be an object: {path}")
        return {}
    return data


def _check_no_secret_values(path: Path, text: str, errors: list[str]) -> None:
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(text):
            errors.append(f"possible concrete secret value in {path}: pattern={pattern.pattern}")


def validate_registry(root: Path) -> list[str]:
    errors: list[str] = []
    registry_path = root / "workspace" / "PROMPT_REGISTRY.json"
    data = _load_json(registry_path, errors)
    if not data:
        return errors

    _check_no_secret_values(registry_path, registry_path.read_text(encoding="utf-8"), errors)

    lifecycle = set(data.get("lifecycle_stages") or [])
    missing_lifecycle = sorted(REQUIRED_LIFECYCLE - lifecycle)
    if missing_lifecycle:
        errors.append(f"registry lifecycle missing stages: {', '.join(missing_lifecycle)}")

    status_fields = set((data.get("status_schema") or {}).get("required_fields") or [])
    missing_status = sorted(REQUIRED_STATUS_FIELDS - status_fields)
    if missing_status:
        errors.append(f"status schema missing fields: {', '.join(missing_status)}")
    optional_status_fields = set((data.get("status_schema") or {}).get("optional_fields") or [])
    missing_optional_status = sorted(OPTIONAL_STATUS_FIELDS - optional_status_fields)
    if missing_optional_status:
        errors.append(f"status schema missing optional fields: {', '.join(missing_optional_status)}")

    prompts = data.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        errors.append("registry prompts must be a non-empty list")
        return errors

    seen_ids: set[str] = set()
    for index, prompt in enumerate(prompts, start=1):
        label = f"prompts[{index}]"
        if not isinstance(prompt, dict):
            errors.append(f"{label} must be an object")
            continue

        missing_fields = sorted(field for field in REQUIRED_PROMPT_FIELDS if field not in prompt)
        if missing_fields:
            errors.append(f"{label} missing fields: {', '.join(missing_fields)}")
            continue

        prompt_id = str(prompt.get("prompt_id", ""))
        if not PROMPT_ID_RE.fullmatch(prompt_id):
            errors.append(f"{label} invalid prompt_id: {prompt_id!r}")
        if prompt_id in seen_ids:
            errors.append(f"duplicate prompt_id: {prompt_id}")
        seen_ids.add(prompt_id)

        for field in REQUIRED_PROMPT_FIELDS - {"source"}:
            if not _non_empty(prompt.get(field)):
                errors.append(f"{prompt_id or label} field must be non-empty: {field}")
        unknown_metrics = sorted(set(prompt.get("metrics") or []) - KNOWN_STATUS_FIELDS)
        if unknown_metrics:
            errors.append(f"{prompt_id or label} metrics are not declared in status schema: {', '.join(unknown_metrics)}")
        for validation_ref in prompt.get("validation") or []:
            if isinstance(validation_ref, str) and ("/" in validation_ref or validation_ref.endswith((".py", ".sh", ".md", ".json"))):
                if not (root / validation_ref).is_file():
                    errors.append(f"{prompt_id or label} validation reference does not exist: {validation_ref}")

        source = prompt.get("source")
        if not isinstance(source, dict):
            errors.append(f"{prompt_id or label} source must be an object")
            continue
        source_path = source.get("path")
        if not isinstance(source_path, str) or not source_path.strip():
            errors.append(f"{prompt_id or label} source.path must be non-empty")
            continue
        resolved = root / source_path
        if not resolved.is_file():
            errors.append(f"{prompt_id} source.path does not exist: {source_path}")
        anchors = source.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            errors.append(f"{prompt_id} source.anchors must be a non-empty list")

    return errors


def validate_governance_doc(root: Path) -> list[str]:
    errors: list[str] = []
    doc_path = root / "workspace" / "PROMPT_GOVERNANCE.md"
    try:
        text = doc_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"missing governance doc: {doc_path}"]

    _check_no_secret_values(doc_path, text, errors)
    required_sections = [
        "Prompt lifecycle",
        "Change gate",
        "Unified prompt status schema",
        "Rollback rule",
    ]
    for section in required_sections:
        if f"## {section}" not in text:
            errors.append(f"governance doc missing section: {section}")

    for field in KNOWN_STATUS_FIELDS:
        if f"`{field}`" not in text:
            errors.append(f"governance doc missing status field: {field}")

    return errors


def validate_all(root: Path) -> list[str]:
    return [*validate_registry(root), *validate_governance_doc(root)]


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[1]
    errors = validate_all(root)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("prompt governance check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
