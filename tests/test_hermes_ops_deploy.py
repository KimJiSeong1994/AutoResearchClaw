from __future__ import annotations

import unittest
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CHECK_HERMES = ROOT / "scripts" / "check-hermes-ops.sh"
SMOKE_HERMES_BRIDGE = ROOT / "scripts" / "check-hermes-bridge-smoke.sh"
INSTALL_HERMES_BRIDGE = ROOT / "scripts" / "install-hermes-bridge-canary-service.sh"
INSTALL_HERMES_AUX = ROOT / "scripts" / "install-hermes-auxiliary-bot-services.sh"
DEPLOY_HERMES = ROOT / "scripts" / "deploy-hermes-workspace.sh"


class HermesOpsDeployTest(unittest.TestCase):
    def test_hermes_scripts_exist_and_are_bash_valid(self) -> None:
        self.assertTrue(CHECK_HERMES.exists())
        self.assertTrue(SMOKE_HERMES_BRIDGE.exists())
        self.assertTrue(INSTALL_HERMES_BRIDGE.exists())
        self.assertTrue(INSTALL_HERMES_AUX.exists())
        self.assertTrue(DEPLOY_HERMES.exists())
        subprocess.run(
            [
                "bash",
                "-n",
                str(CHECK_HERMES),
                str(SMOKE_HERMES_BRIDGE),
                str(INSTALL_HERMES_BRIDGE),
                str(INSTALL_HERMES_AUX),
                str(DEPLOY_HERMES),
            ],
            check=True,
            cwd=ROOT,
        )

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
        self.assertIn('HERMES_LOG_GLOB="${HERMES_LOG_GLOB:-~/.hermes/logs/*.log}"', text)
        self.assertIn('HERMES_CURL_MAX_TIME="${HERMES_CURL_MAX_TIME:-60}"', text)
        self.assertIn('printf \'max-time = %s\\n\' "$HERMES_CURL_MAX_TIME"', text)
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

    def test_hermes_bridge_smoke_is_loopback_and_secret_safe(self) -> None:
        text = SMOKE_HERMES_BRIDGE.read_text(encoding="utf-8")

        self.assertIn("HERMES_BASE_URL=\"${HERMES_BASE_URL:-http://127.0.0.1:28789/v1}\"", text)
        self.assertIn("HERMES_BASE_URL must remain strict loopback", text)
        self.assertIn("HERMES_WORKSPACE must stay under the ~/.hermes canary directory", text)
        self.assertIn("HERMES_WORKSPACE must not contain parent-directory traversal", text)
        self.assertIn("HERMES_GATEWAY_TOKEN_FILE", text)
        self.assertIn("OpenClawGatewayClient", text)
        self.assertIn("models_health", text)
        self.assertIn("chat_completion", text)
        self.assertIn("discord-openclaw-bridge-hermes-smoke/1.0", text)
        self.assertIn('HERMES_SMOKE_TIMEOUT_SEC="${HERMES_SMOKE_TIMEOUT_SEC:-600}"', text)
        self.assertIn('timeout_sec=float(os.environ["HERMES_SMOKE_TIMEOUT_SEC"])', text)
        self.assertIn("quote_remote", text)
        self.assertNotIn("cat $token_file", text)
        self.assertNotIn("systemctl --user restart", text)
        self.assertNotIn("rm -rf", text)

    def test_hermes_bridge_canary_installer_is_guarded_and_disabled(self) -> None:
        text = INSTALL_HERMES_BRIDGE.read_text(encoding="utf-8")

        self.assertIn("HERMES_BRIDGE_SERVICE=\"${HERMES_BRIDGE_SERVICE:-discord-hermes-bridge-canary.service}\"", text)
        self.assertIn("HERMES_BRIDGE_ENABLE_GUARD=\"${HERMES_BRIDGE_ENABLE_GUARD:-~/.hermes/ENABLE_DISCORD_BRIDGE_CANARY}\"", text)
        self.assertIn("ConditionPathExists=$guard_file", text)
        self.assertIn("systemctl --user disable --now", text)
        self.assertIn("rm -f \"$guard_file\"", text)
        self.assertIn("Description=Discord bridge for Hermes canary gateway", text)
        self.assertIn("HERMES_BASE_URL", text)
        self.assertIn("OPENCLAW_BASE_URL", text)
        self.assertIn("/home/ubuntu/.hermes/workspace", text)
        self.assertIn("ExecStart=$python_bin -m discord_openclaw_bridge.bot", text)
        self.assertNotIn("systemctl --user enable", text)
        self.assertNotIn("systemctl --user start", text)
        self.assertNotIn("cat $token_file", text)
        self.assertNotIn("rm -rf", text)


    def test_hermes_auxiliary_bot_installer_is_guarded_and_reversible(self) -> None:
        text = INSTALL_HERMES_AUX.read_text(encoding="utf-8")

        self.assertIn('HERMES_AUX_CUTOVER="${HERMES_AUX_CUTOVER:-0}"', text)
        self.assertIn('HERMES_AUX_CUTOVER must be 0 or 1', text)
        self.assertIn('discord-hermes-jiphyeonjeon-miner.service', text)
        self.assertIn('discord-hermes-jiphyeonjeon-traveler.service', text)
        self.assertIn('discord-hermes-jiphyeonjeon-reporter.service', text)
        self.assertIn('systemctl --user stop "$old"', text)
        self.assertIn('systemctl --user start "$new"', text)
        self.assertIn('systemctl --user disable "$old"', text)
        self.assertIn('systemctl --user enable "$new"', text)
        self.assertIn('systemctl --user start "$old" || true', text)
        self.assertIn('$project/.venv/bin/$entry', text)
        self.assertIn('ReadWritePaths=$project $workspace $HOME/.hermes/state $extra_rw', text)
        self.assertNotIn('cat $token_file', text)
        self.assertNotIn('rm -rf', text)

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
        self.assertIn("id: hermes-bridge-smoke-check", jobs)
        self.assertIn("id: hermes-bridge-canary-service-install", jobs)
        self.assertIn("id: hermes-auxiliary-bot-service-install", jobs)
        self.assertIn("bash scripts/deploy-hermes-workspace.sh", jobs)
        self.assertIn("bash scripts/check-hermes-ops.sh", jobs)
        self.assertIn("bash scripts/check-hermes-bridge-smoke.sh", jobs)
        self.assertIn("bash scripts/install-hermes-bridge-canary-service.sh", jobs)
        self.assertIn("bash scripts/install-hermes-auxiliary-bot-services.sh", jobs)
        self.assertIn("manual canary only", jobs)
        self.assertIn("read-only remote canary inspection", jobs)
        self.assertIn("canary OpenAI-compatible chat smoke", jobs)
        self.assertIn("guarded disabled service unit", jobs)
        self.assertIn("guarded auxiliary bot service units", jobs)
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
