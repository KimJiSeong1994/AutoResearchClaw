from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run-researchclaw-topic.sh"


class RunResearchClawTopicSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = SCRIPT_PATH.read_text()

    def test_topic_is_not_interpolated_into_ssh_remote_command(self) -> None:
        ssh_invocation = re.search(r"^ssh\b.*?(?=\s+<<'REMOTE_SCRIPT')", self.script, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(ssh_invocation)
        assert ssh_invocation is not None

        self.assertIn('bash -s -- "$TOPIC_B64"', ssh_invocation.group(0))
        self.assertNotIn('$TOPIC"', ssh_invocation.group(0))
        self.assertNotIn('"$TOPIC"', ssh_invocation.group(0))

    def test_remote_script_decodes_topic_after_bash_s_receives_safe_argument(self) -> None:
        self.assertIn("<<'REMOTE_SCRIPT'", self.script)
        self.assertIn('TOPIC_B64="$(printf \'%s\' "$TOPIC" | base64 | tr -d \'\\n\')"', self.script)
        self.assertIn("python3 -c 'import base64, sys; print(base64.b64decode", self.script)
        self.assertNotIn("base64 --decode", self.script)
        self.assertIn('run-topic.sh "$TOPIC"', self.script)


if __name__ == "__main__":
    unittest.main()
