from __future__ import annotations

import unittest
import os
from pathlib import Path
import subprocess
import tempfile


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
        self.assertIn("HERMES_REMOTE_WORKSPACE must stay under the ~/.hermes canary directory", text)
        self.assertIn("HERMES_REMOTE_WORKSPACE contains unsafe shell characters", text)
        self.assertIn("HERMES_REMOTE_WORKSPACE must not contain parent-directory traversal", text)
        self.assertIn("remote_workspace_quoted=", text)
        self.assertIn("for ssh_arg in", text)
        self.assertIn("python3 scripts/check-prompt-governance.py", text)
        self.assertIn("python3 scripts/check-runtime-manifests.py", text)
        self.assertIn("SSH_OPTIONS", text)
        self.assertIn('RSYNC_SSH+="${RSYNC_SSH:+ }$(quote_remote "$ssh_arg")"', text)
        self.assertIn("runtime/", text)
        self.assertIn("scripts/", text)
        self.assertIn("--exclude '.env'", text)
        self.assertNotIn("openclaw agents set-identity", text)
        self.assertNotIn("systemctl --user restart", text)
        self.assertNotIn("HERMES_GATEWAY_TOKEN=", text)

    def test_hermes_readiness_is_read_only_loopback_and_secret_safe(self) -> None:
        text = CHECK_HERMES.read_text(encoding="utf-8")

        self.assertIn("HERMES_BASE_URL=\"${HERMES_BASE_URL:-http://127.0.0.1:28789/v1}\"", text)
        self.assertIn("HERMES_BASE_URL must remain strict loopback", text)
        self.assertIn("HERMES_WORKSPACE must stay under the ~/.hermes canary directory", text)
        self.assertIn("HERMES_WORKSPACE must not contain parent-directory traversal", text)
        self.assertIn("HERMES_GATEWAY_TOKEN_FILE", text)
        self.assertIn('HERMES_TOKEN_FILE="${HERMES_GATEWAY_TOKEN_FILE:-~/.hermes_gateway_token}"', text)
        self.assertIn("quote_remote", text)
        self.assertNotIn('HERMES_TOKEN_FILE="${HERMES_GATEWAY_TOKEN_FILE:-$HOME/.hermes_gateway_token}"', text)
        self.assertIn('"$base_url/models"', text)
        self.assertIn("listener_addresses=", text)
        self.assertIn("not loopback-only", text)
        self.assertIn("curl_config=", text)
        self.assertIn("trap 'rm -f", text)
        self.assertIn("glob.glob", text)
        self.assertNotIn('Authorization: Bearer $(', text)
        self.assertNotIn("ls -1t $HERMES_LOG_GLOB", text)
        self.assertIn("SSH_OPTIONS", text)
        self.assertIn("never print", (ROOT / "runtime" / "jobs.yaml").read_text(encoding="utf-8"))
        forbidden = ("systemctl --user restart", "systemctl --user start", "rm -rf", "cat $token_file")
        for token in forbidden:
            self.assertNotIn(token, text)

    def test_runtime_manifests_declare_hermes_canary_without_removing_openclaw(self) -> None:
        agents = (ROOT / "runtime" / "agents.yaml").read_text(encoding="utf-8")
        jobs = (ROOT / "runtime" / "jobs.yaml").read_text(encoding="utf-8")

        self.assertIn("id: hermes-ec2-ops", agents)
        self.assertIn("profile: hermes-canary", agents)
        self.assertIn("workspace: ~/.hermes/workspace", agents)
        self.assertIn("gateway_endpoint: http://127.0.0.1:28789/v1", agents)
        self.assertIn("service: hermes-gateway.service", agents)
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

    def test_deploy_rejects_openclaw_workspace_override_before_remote_actions(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "REMOTE_HOST": "example.invalid",
                "KEY_FILE": "/tmp/nonexistent-key",
                "HERMES_REMOTE_WORKSPACE": "~/.openclaw/workspace",
            }
        )

        result = subprocess.run(
            ["bash", str(DEPLOY_HERMES)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must stay under the ~/.hermes canary directory", result.stderr)

    def test_deploy_rejects_nested_openclaw_hermes_workspace_override(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "REMOTE_HOST": "example.invalid",
                "KEY_FILE": "/tmp/nonexistent-key",
                "HERMES_REMOTE_WORKSPACE": "~/.openclaw/workspace/.hermes",
            }
        )

        result = subprocess.run(
            ["bash", str(DEPLOY_HERMES)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must stay under the ~/.hermes canary directory", result.stderr)

    def test_deploy_rejects_parent_traversal_from_hermes_workspace(self) -> None:
        for unsafe_workspace in (
            "~/.hermes/../.openclaw/workspace",
            "~/.hermes/workspace/../../.openclaw/workspace",
        ):
            with self.subTest(unsafe_workspace=unsafe_workspace):
                env = os.environ.copy()
                env.update(
                    {
                        "REMOTE_HOST": "example.invalid",
                        "KEY_FILE": "/tmp/nonexistent-key",
                        "HERMES_REMOTE_WORKSPACE": unsafe_workspace,
                    }
                )

                result = subprocess.run(
                    ["bash", str(DEPLOY_HERMES)],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must not contain parent-directory traversal", result.stderr)

    def test_deploy_rejects_single_quote_in_hermes_workspace(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "REMOTE_HOST": "example.invalid",
                "KEY_FILE": "/tmp/nonexistent-key",
                "HERMES_REMOTE_WORKSPACE": "~/.hermes/work'space",
            }
        )

        result = subprocess.run(
            ["bash", str(DEPLOY_HERMES)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains unsafe shell characters", result.stderr)

    def test_readiness_rejects_parent_traversal_from_hermes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_ssh = tmp_path / "ssh"
            fake_ssh.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
            fake_ssh.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{tmp_path}{os.pathsep}{env.get('PATH', '')}",
                    "REMOTE_HOST": "example.invalid",
                    "KEY_FILE": "/tmp/nonexistent-key",
                    "HERMES_WORKSPACE": "~/.hermes/../.openclaw/workspace",
                }
            )

            result = subprocess.run(
                ["bash", str(CHECK_HERMES)],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertNotEqual(result.returncode, 99)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not contain parent-directory traversal", result.stderr)

    def test_readiness_rejects_loopback_userinfo_url_before_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_ssh = tmp_path / "ssh"
            fake_ssh.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
            fake_ssh.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{tmp_path}{os.pathsep}{env.get('PATH', '')}",
                    "REMOTE_HOST": "example.invalid",
                    "KEY_FILE": "/tmp/nonexistent-key",
                    "HERMES_BASE_URL": "http://127.0.0.1:28789@evil.example/v1",
                }
            )

            result = subprocess.run(
                ["bash", str(CHECK_HERMES)],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertNotEqual(result.returncode, 99)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must remain strict loopback", result.stderr)

    def test_readiness_passes_remote_defaults_as_remote_safe_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            capture = tmp_path / "ssh-args.txt"
            fake_ssh = tmp_path / "ssh"
            fake_ssh.write_text(
                "#!/usr/bin/env bash\n"
                f"printf '%s\\n' \"$@\" > {capture}\n"
                "cat >/dev/null\n",
                encoding="utf-8",
            )
            fake_ssh.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{tmp_path}{os.pathsep}{env.get('PATH', '')}",
                    "REMOTE_HOST": "example.invalid",
                    "KEY_FILE": "/tmp/nonexistent-key",
                    "HERMES_LOG_GLOB": "/tmp/hermes/*.log; touch /tmp/pwned",
                }
            )

            subprocess.run(["bash", str(CHECK_HERMES)], cwd=ROOT, env=env, check=True)
            captured = capture.read_text(encoding="utf-8")

        self.assertIn("HERMES_TOKEN_FILE=~/.hermes_gateway_token", captured)
        self.assertNotIn(f"HERMES_TOKEN_FILE={Path.home()}/.hermes_gateway_token", captured)
        self.assertNotIn("; touch /tmp/pwned", captured)


if __name__ == "__main__":
    unittest.main()
