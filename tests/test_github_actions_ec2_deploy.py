from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ec2-deploy.yml"


class GitHubActionsEc2DeployTest(unittest.TestCase):
    def test_workflow_exists_and_uses_expected_triggers(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", text)
        self.assertIn("pull_request:", text)
        self.assertIn("push:", text)
        self.assertIn("branches: [main]", text)

    def test_workflow_uses_secrets_and_strict_host_key_checking(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        for secret in ("EC2_REMOTE_HOST", "EC2_SSH_PRIVATE_KEY", "EC2_KNOWN_HOSTS"):
            self.assertIn(f"secrets.{secret}", text)
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertNotIn("StrictHostKeyChecking=no", text)
        self.assertNotIn("ssh-keyscan", text)
        self.assertNotIn("journalctl", text)
        self.assertRegex(text, r"chmod 600 \"\$RUNNER_TEMP/ec2_deploy_key\"")

    def test_workflow_pins_actions_before_using_ec2_credentials(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5", text)
        self.assertIn("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065", text)
        self.assertNotIn("actions/checkout@v4", text)
        self.assertNotIn("actions/setup-python@v5", text)

    def test_workflow_calls_existing_deploy_scripts_and_restart_check(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("bash scripts/deploy-openclaw-workspace.sh", text)
        self.assertIn("bash scripts/deploy-discord-openclaw-bridge.sh", text)
        self.assertIn("uv run --with pytest pytest -q", text)
        self.assertIn("bash project/scripts/install.sh", text)
        self.assertIn("bash project/scripts/restart.sh", text)
        self.assertIn("systemctl --user is-active discord-openclaw-bridge.service", text)
        self.assertIn("systemctl --user show discord-openclaw-bridge.service", text)

    def test_workflow_does_not_embed_obvious_private_key_material(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        forbidden_patterns = [
            "BEGIN OPENSSH PRIVATE KEY",
            "BEGIN RSA PRIVATE KEY",
            "DISCORD_BOT_TOKEN=",
            "OPENCLAW_GATEWAY_TOKEN=",
        ]
        for pattern in forbidden_patterns:
            self.assertNotIn(pattern, text)

    def test_deploy_scripts_accept_ssh_options_and_sync_runtime_scripts(self) -> None:
        workspace = (ROOT / "scripts" / "deploy-openclaw-workspace.sh").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts" / "deploy-discord-openclaw-bridge.sh").read_text(encoding="utf-8")

        self.assertIn("SSH_OPTIONS", workspace)
        self.assertIn("SSH_OPTIONS", bridge)
        self.assertIn("SSH_BASE=(ssh)", workspace)
        self.assertIn("SSH_BASE=(ssh)", bridge)
        self.assertIn("runtime/", workspace)
        self.assertIn("scripts/", workspace)
        self.assertIn("--exclude '.env'", workspace)
        self.assertIn("--exclude '.env'", bridge)
        self.assertGreaterEqual(workspace.count("--exclude '.env'"), 3)


if __name__ == "__main__":
    unittest.main()
