from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = ROOT / "scripts" / "check-runtime-manifests.py"
DEPLOY_PATH = ROOT / "scripts" / "deploy-openclaw-workspace.sh"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_runtime_manifests", CHECK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RuntimeManifestTest(unittest.TestCase):
    def test_runtime_manifests_validate(self) -> None:
        checker = load_checker()
        self.assertEqual([], checker.validate_runtime_manifests(ROOT))

    def test_deploy_gate_runs_runtime_manifest_check(self) -> None:
        deploy = DEPLOY_PATH.read_text()
        prompt_index = deploy.index("python3 scripts/check-prompt-governance.py")
        runtime_index = deploy.index("python3 scripts/check-runtime-manifests.py")
        self.assertLess(prompt_index, runtime_index)
        mkdir_index = deploy.index('"${SSH_BASE[@]}" "$REMOTE_HOST" "mkdir -p')
        self.assertLess(runtime_index, mkdir_index)
        self.assertIn("$REMOTE_WORKSPACE/skills", deploy[mkdir_index:])

    def test_required_phase_one_jobs_and_agents_are_declared(self) -> None:
        checker = load_checker()
        jobs_text = (ROOT / "runtime" / "jobs.yaml").read_text()
        agents_text = (ROOT / "runtime" / "agents.yaml").read_text()
        job_ids = {entry.id for entry in checker._entries(jobs_text)}
        agent_ids = {entry.id for entry in checker._entries(agents_text)}
        self.assertTrue(checker.REQUIRED_JOB_IDS.issubset(job_ids))
        self.assertTrue(checker.REQUIRED_AGENT_IDS.issubset(agent_ids))
        self.assertIn("jiphyeonjeon-editor-canonical-identity-report", job_ids)
        self.assertIn("jiphyeonjeon-advisor-evidence-quality-gate", job_ids)
        self.assertIn("jiphyeonjeon-editor", agent_ids)
        self.assertIn("jiphyeonjeon-advisor", agent_ids)
        self.assertNotIn("editorial-promotion-coordinator", job_ids)
        self.assertNotIn("editorial-promotion-coordinator", agent_ids)
        self.assertIn("집현정-편집자", agents_text)
        self.assertIn("집현전-지도교수", agents_text)

    def test_editor_and_advisor_are_advisory_only_without_promotion_activation(self) -> None:
        checker = load_checker()
        jobs_text = (ROOT / "runtime" / "jobs.yaml").read_text()
        agents_text = (ROOT / "runtime" / "agents.yaml").read_text()
        jobs = {entry.id: entry for entry in checker._entries(jobs_text)}
        agents = {entry.id: entry for entry in checker._entries(agents_text)}
        for job_id in ("jiphyeonjeon-editor-canonical-identity-report", "jiphyeonjeon-advisor-evidence-quality-gate"):
            job = jobs[job_id]
            self.assertIn("local-advisory", job.fields["type"])
            command_refs = " ".join(job.lists["command_refs"])
            self.assertNotIn("discord", command_refs.lower())
            self.assertNotIn("systemctl", command_refs.lower())
            self.assertNotIn("cron", command_refs.lower())
        self.assertNotIn("editorial-promotion-coordinator", jobs)
        self.assertNotIn("editorial-promotion-coordinator", agents)
        for job in jobs.values():
            self.assertNotEqual("editorial-promotion-coordinator", job.fields.get("owner_agent", ""))
            self.assertNotIn("editorial-promotion-coordinator", " ".join(job.lists.get("command_refs", [])))
        for agent in agents.values():
            self.assertNotIn("editorial-promotion-coordinator", agent.lists.get("owns_jobs", []))

    def test_traveler_runner_uses_runtime_scout_topics(self) -> None:
        runner = ROOT / "skills" / "discord-openclaw-bridge" / "project" / "scripts" / "run-traveler-collection-report.sh"
        installer = ROOT / "skills" / "discord-openclaw-bridge" / "install-traveler-collection-report-cron.sh"

        self.assertIn("runtime/traveler-scout-topics.json", runner.read_text(encoding="utf-8"))
        self.assertIn("runtime/traveler-scout-topics.json", installer.read_text(encoding="utf-8"))

    def test_malformed_yaml_is_rejected_when_yaml_parser_available(self) -> None:
        if shutil.which("ruby") is None:
            self.skipTest("Ruby YAML parser unavailable in this environment")
        checker = load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            shutil.copytree(ROOT / "runtime", tmp_root / "runtime")
            jobs_path = tmp_root / "runtime" / "jobs.yaml"
            jobs_path.write_text(jobs_path.read_text() + "\ninvalid_flow: [unterminated\n")
            errors = checker.validate_runtime_manifests(tmp_root)
        self.assertTrue(any("invalid YAML syntax" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
