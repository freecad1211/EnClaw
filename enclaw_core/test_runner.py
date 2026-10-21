"""Focused tests for runner safety helpers."""

import unittest
from pathlib import Path

from .runner import RunnerError, _check_changed_paths, _format_command, run_worker_pool


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

    def test_worker_pool_is_bounded_and_exits_when_queue_is_empty(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temporary:
            results = run_worker_pool(Path(temporary) / "tasks.sqlite3", {}, workers=2, idle_seconds=1)
            self.assertEqual(results, [])
        with self.assertRaises(RunnerError):
            run_worker_pool(":memory:", {}, workers=17, idle_seconds=1)

    def test_one_task_runs_reviews_and_integrates_only_after_validation(self):
        import json
        import subprocess
        import tempfile

        from .policy import default_policy
        from .runner import run_one_task
        from .tasks import TaskStore

        def git(repo, *args):
            result = subprocess.run(
                ["git", "-C", str(repo), *args],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            if result.returncode:
                raise AssertionError(result.stderr)
            return result.stdout.strip()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            git(root, "init", "-b", "develop")
            git(root, "config", "user.email", "test@example.invalid")
            git(root, "config", "user.name", "EnClaw Test")
            (root / "README.md").write_text("base\\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-m", "initial")
            git(root, "branch", "enclaw/integration", "develop")
            integration = Path(temporary) / "integration"
            git(root, "worktree", "add", str(integration), "enclaw/integration")

            policy = default_policy("sample", str(root), "develop", "enclaw/integration")
            policy["project"]["workspace_root"] = str(Path(temporary) / "worktrees")
            policy["validation"]["require_independent_review"] = True
            policy["runner"]["require_sandbox"] = False  # explicit opt-out for this test
            policy["validation"]["commands"] = [[
                "python3", "-c", "from pathlib import Path; assert Path('generated.txt').read_text() == 'ok'"
            ]]
            policy["runner"]["agent_command"] = [
                "python3", "-c", "from pathlib import Path; Path('generated.txt').write_text('ok')", "{prompt}"
            ]
            policy["runner"]["review_command"] = [
                "python3", "-c", "print('APPROVE')", "{prompt}"
            ]
            policy_path = Path(temporary) / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            database = Path(temporary) / "tasks.sqlite3"
            store = TaskStore(database)
            queued = store.create("sample", "Create generated file", "Create generated.txt with the text ok.")
            result = run_one_task(
                database,
                {"sample": {"id": "sample", "policy_path": str(policy_path)}},
            )
            self.assertEqual(result["status"], "completed")
            self.assertTrue((integration / "generated.txt").exists())
            self.assertEqual(store.get(queued["task_id"])["status"], "completed")


if __name__ == "__main__":
    unittest.main()
