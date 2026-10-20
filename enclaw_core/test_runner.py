"""Focused tests for runner safety helpers."""

import unittest
from pathlib import Path

from .runner import RunnerError, _check_changed_paths, _format_command


class RunnerSafetyTests(unittest.TestCase):
    def test_command_requires_prompt_placeholder(self):
        with self.assertRaises(RunnerError):
            _format_command(["opencode", "run"], "task prompt", Path("/tmp/work"), "task-1")

    def test_command_substitutes_prompt_without_shell(self):
        command = _format_command(
            ["opencode", "run", "{prompt}", "--dir", "{worktree}"],
            "Fix this; do not run shell text",
            Path("/tmp/work"),
            "task-1",
        )
        self.assertEqual(command[2], "Fix this; do not run shell text")
        self.assertEqual(command[4], "/tmp/work")

    def test_secret_paths_are_blocked(self):
        for path in (".env", ".env.production", "keys/id_rsa", "cert.pem", ".enclaw/policy.json"):
            with self.subTest(path=path):
                with self.assertRaises(RunnerError):
                    _check_changed_paths([path])

    def test_normal_source_paths_are_allowed(self):
        _check_changed_paths(["src/index.ts", "tests/test_core.py", "README.md"])


if __name__ == "__main__":
    unittest.main()
