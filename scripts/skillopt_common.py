#!/usr/bin/env python3
"""Shared stdlib-only utilities for SkillOpt scripts.

Each helper is a verbatim consolidation of byte-identical copies that were
previously duplicated across skillopt_apply.py, skillopt_reward.py,
skillopt_propose.py, skillopt_audit.py, and skillopt_eval.py.

Import pattern (mirrors apply.py's existing import of propose.py):

    try:
        from skillopt_common import sha256_text, ...
    except ModuleNotFoundError:  # pragma: no cover - direct path fallback
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from skillopt_common import sha256_text, ...
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# HEX64_RE: identical in skillopt_propose.py and skillopt_reward.py.
HEX64_RE = re.compile(r"^[a-f0-9]{64}$")

# LOW_RISK_GAPS: apply.py had a tuple; reward.py had a set — same elements.
# frozenset satisfies both use-patterns: membership test (in) and set-intersection (&).
LOW_RISK_GAPS: frozenset[str] = frozenset(
    {"missing_verification", "missing_input_contract", "weak_output_contract"}
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Write JSON with sort_keys=True (apply.py and propose.py variant).

    skillopt_reward.py's write_json omits sort_keys — that file keeps its own
    local copy to preserve its output byte-stability.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_report_output_path(path: Path, root: Path, label: str) -> ValidationResult:
    resolved = path.resolve()
    protected = [root / ".codex/skills", root / "skills", root / "runtime"]
    for protected_root in protected:
        if is_relative_to(resolved, protected_root):
            return ValidationResult(False, [f"{label} must not be under protected skill/runtime surfaces"])
    allowed = root / ".omx/reports/skillopt"
    tmp_roots = {Path("/tmp").resolve(), Path("/private/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    if not is_relative_to(resolved, allowed) and not any(is_relative_to(resolved, tmp) for tmp in tmp_roots):
        return ValidationResult(False, [f"{label} must be under .omx/reports/skillopt or a temporary directory"])
    return ValidationResult(True, [])


def changed_line_count(patch: str) -> int:
    return sum(1 for line in patch.splitlines() if line.strip())
