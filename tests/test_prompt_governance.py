from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = ROOT / "scripts" / "check-prompt-governance.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_prompt_governance", CHECK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PromptGovernanceTest(unittest.TestCase):
    def test_registry_and_governance_doc_validate(self) -> None:
        checker = load_checker()
        self.assertEqual([], checker.validate_all(ROOT))

    def test_prompt_ids_are_unique_and_stable_shape(self) -> None:
        registry = json.loads((ROOT / "workspace" / "PROMPT_REGISTRY.json").read_text())
        prompt_ids = [prompt["prompt_id"] for prompt in registry["prompts"]]
        self.assertEqual(len(prompt_ids), len(set(prompt_ids)))
        self.assertIn("workspace_operator_contract", prompt_ids)
        self.assertIn("discord_openclaw_chat", prompt_ids)
        self.assertIn("weekly_trend_report", prompt_ids)
        self.assertIn("apps_script_cardnews_renderer", prompt_ids)

    def test_required_status_fields_are_declared_for_reporting(self) -> None:
        checker = load_checker()
        registry = json.loads((ROOT / "workspace" / "PROMPT_REGISTRY.json").read_text())
        fields = set(registry["status_schema"]["required_fields"])
        self.assertTrue(checker.REQUIRED_STATUS_FIELDS.issubset(fields))


if __name__ == "__main__":
    unittest.main()
