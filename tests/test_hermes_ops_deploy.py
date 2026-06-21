from __future__ import annotations

import unittest
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CHECK_HERMES = ROOT / "scripts" / "check-hermes-ops.sh"
DEPLOY_HERMES = ROOT / "scripts" / "deploy-hermes-workspace.sh"


class HermesOpsDeployTest(unittest.TestCase):
    def test_hermes_scripts_exist_and_are_bash_valid(self) -> None:
        self.assertTrue(CHECK_HERMES.exists())
        self.assertTrue(DEPLOY_HERMES.exists())
        subprocess.run(["bash", "-n", str(CHECK_HERMES), str(DEPLOY_HERMES)], check=True, cwd=ROOT)

    def test_hermes_deploy_is_side_by_side_and_secret_safe(self) -> None:
        text = DEPLOY_HERMES.read_text(encoding="utf-8")

        self.assertIn("HERMES_REMOTE_WORKSPACE=\"${HERMES_REMOTE_WORKSPACE:-~/.hermes/workspace}\"", text)
        self.assertIn("python3 scripts/check-prompt-governance.py", text)
        self.assertIn("python3 scripts/check-runtime-manifests.py", text)
        self.assertIn("SSH_OPTIONS", text)
        self.assertIn("RSYNC_SSH=\"${SSH_BASE[*]}\"", text)
        self.assertIn("runtime/", text)
        self.assertIn("scripts/", text)
        self.assertIn("--exclude '.env'", text)
        self.assertNotIn("openclaw agents set-identity", text)
        self.assertNotIn("systemctl --user restart", text)
        self.assertNotIn("HERMES_GATEWAY_TOKEN=", text)

    def test_hermes_readiness_is_read_only_loopback_and_secret_safe(self) -> None:
        text = CHECK_HERMES.read_text(encoding="utf-8")

        self.assertIn("HERMES_BASE_URL=\"${HERMES_BASE_URL:-http://127.0.0.1:28789/v1}\"", text)
        self.assertIn("HERMES_BASE_URL must remain loopback", text)
        self.assertIn("HERMES_GATEWAY_TOKEN_FILE", text)
        self.assertIn('"$base_url/models"', text)
        self.assertIn("SSH_OPTIONS", text)
        self.assertIn("never print", (ROOT / "runtime" / "jobs.yaml").read_text(encoding="utf-8"))
        forbidden = ("systemctl --user restart", "systemctl --user start", "rm -rf", "cat $token_file")
        for token in forbidden:
            self.assertNotIn(token, text)

    def test_runtime_manifests_declare_hermes_canary_without_removing_openclaw(self) -> None:
        agents = (ROOT / "runtime" / "agents.yaml").read_text(encoding="utf-8")
        jobs = (ROOT / "runtime" / "jobs.yaml").read_text(encoding="utf-8")

        self.assertIn("id: hermes-ec2-ops", agents)
        self.assertIn("hermes-workspace-deploy", agents)
        self.assertIn("hermes-ops-readiness-check", agents)
        self.assertIn("do not restart production services or mutate OpenClaw state", agents)
        self.assertIn("id: hermes-workspace-deploy", jobs)
        self.assertIn("id: hermes-ops-readiness-check", jobs)
        self.assertIn("bash scripts/deploy-hermes-workspace.sh", jobs)
        self.assertIn("bash scripts/check-hermes-ops.sh", jobs)
        self.assertIn("manual canary only", jobs)
        self.assertIn("read-only remote canary inspection", jobs)
        self.assertIn("id: openclaw-workspace-deploy", jobs)
        self.assertIn("id: openclaw-ops-readiness-check", jobs)
        self.assertIn("id: openclaw-ec2-ops", agents)


if __name__ == "__main__":
    unittest.main()
