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
        self.assertLess(runtime_index, deploy.index('${SSH_CMD} "$REMOTE_HOST" "mkdir -p $REMOTE_WORKSPACE/skills"'))

    def test_required_phase_one_jobs_and_agents_are_declared(self) -> None:
        checker = load_checker()
        jobs_text = (ROOT / "runtime" / "jobs.yaml").read_text()
        agents_text = (ROOT / "runtime" / "agents.yaml").read_text()
        job_ids = {entry.id for entry in checker._entries(jobs_text)}
        agent_ids = {entry.id for entry in checker._entries(agents_text)}
        self.assertTrue(checker.REQUIRED_JOB_IDS.issubset(job_ids))
        self.assertTrue(checker.REQUIRED_AGENT_IDS.issubset(agent_ids))

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
