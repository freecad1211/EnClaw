"""Regression tests for runner hardening: sandbox enforcement, env scrubbing, retries, fairness."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .policy import default_policy, validate_policy
from .runner import RunnerError, _agent_env, run_one_task
from .sandbox import SandboxError, build_bwrap_argv
from .tasks import TaskStore


def _git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _make_project(temporary: Path, name: str, *, agent_code: str, require_sandbox: bool = False,
                  sandbox_command=None) -> dict:
    """Create a disposable repo + integration worktree + policy file; return a registry entry."""
    root = temporary / f"{name}-repo"
    root.mkdir()
    _git(root, "init", "-b", "develop")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "EnClaw Test")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    _git(root, "branch", "enclaw/integration", "develop")
    _git(root, "worktree", "add", str(temporary / f"{name}-integration"), "enclaw/integration")

    policy = default_policy(name, str(root), "develop", "enclaw/integration")
    policy["project"]["workspace_root"] = str(temporary / f"{name}-worktrees")
    policy["validation"]["commands"] = [["python3", "-c", "pass"]]
    policy["runner"]["agent_command"] = ["python3", "-c", agent_code, "{prompt}"]
    policy["runner"]["review_command"] = ["python3", "-c", "print('APPROVE')", "{prompt}"]
    policy["runner"]["require_sandbox"] = require_sandbox
    if sandbox_command:
        policy["runner"]["sandbox_command"] = sandbox_command
    policy_path = temporary / f"{name}-policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return {"id": name, "policy_path": str(policy_path)}


WRITE_FILE = "from pathlib import Path; Path('generated.txt').write_text('ok')"


class SandboxEnforcementTests(unittest.TestCase):
    def test_missing_sandbox_fails_closed_before_creating_a_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            entry = _make_project(tmp, "p", agent_code=WRITE_FILE, require_sandbox=True)
            database = tmp / "tasks.sqlite3"
            store = TaskStore(database)
            task = store.create("p", "title", "description")
            with self.assertRaises(RunnerError) as ctx:
                run_one_task(database, {"p": entry})
            self.assertIn("sandbox", str(ctx.exception).lower())
            self.assertEqual(store.get(task["task_id"])["status"], "failed")
            self.assertFalse((tmp / "p-worktrees").exists())

    def test_sandbox_prefix_wraps_agent_and_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            marker = tmp / "sandbox-calls.txt"
            wrapper = (
                "import subprocess, sys;"
                f"open(r'{marker}', 'a').write('x');"
                "sys.exit(subprocess.call(sys.argv[2:]))"
            )
            entry = _make_project(
                tmp, "p", agent_code=WRITE_FILE, require_sandbox=True,
                sandbox_command=["python3", "-c", wrapper, "--"],
            )
            database = tmp / "tasks.sqlite3"
            store = TaskStore(database)
            task = store.create("p", "title", "description")
            result = run_one_task(database, {"p": entry})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(marker.read_text(), "xxx")  # agent + reviewer + validation
            self.assertEqual(store.get(task["task_id"])["status"], "completed")


class EnvironmentScrubbingTests(unittest.TestCase):
    def test_service_secrets_are_not_passed_to_agents(self):
        fake = {
            "DISCORD_BOT_TOKEN": "discord-secret",
            "GITHUB_TOKEN": "gh-secret",
            "DB_PASSWORD": "pw",
            "OPENAI_API_KEY": "needed-by-agent",
            "PATH": os.environ.get("PATH", ""),
        }
        with mock.patch.dict(os.environ, fake, clear=True):
            env = _agent_env({})
            self.assertNotIn("DISCORD_BOT_TOKEN", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("DB_PASSWORD", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(_agent_env({"env_passthrough": ["GITHUB_TOKEN"]})["GITHUB_TOKEN"], "gh-secret")
            self.assertEqual(
                _agent_env({"env_passthrough": ["OPENAI_API_KEY"]})["OPENAI_API_KEY"],
                "needed-by-agent",
            )

    def test_policy_rejects_bad_env_passthrough(self):
        policy = default_policy("p", "/tmp/repo")
        policy["runner"]["env_passthrough"] = "GITHUB_TOKEN"
        with self.assertRaises(Exception):
            validate_policy(policy)


class BuiltInSandboxTests(unittest.TestCase):
    def test_missing_bubblewrap_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            worktree = Path(tmp) / "worktree"
            root.mkdir()
            worktree.mkdir()
            _git(root, "init", "-b", "develop")
            with mock.patch("enclaw_core.sandbox.sys.platform", "linux"), mock.patch("enclaw_core.sandbox.shutil.which", return_value=None):
                with self.assertRaisesRegex(SandboxError, "not installed"):
                    build_bwrap_argv(worktree, root, ["python3", "-c", "pass"])

    def test_bubblewrap_disables_network_and_mounts_git_metadata_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            worktree = Path(tmp) / "worktree"
            root.mkdir()
            worktree.mkdir()
            _git(root, "init", "-b", "develop")
            with mock.patch("enclaw_core.sandbox.sys.platform", "linux"), mock.patch("enclaw_core.sandbox.shutil.which", return_value="/usr/bin/bwrap"):
                command = build_bwrap_argv(worktree, root, ["python3", "-c", "pass"])
            self.assertIn("--unshare-all", command)
            self.assertIn("--ro-bind", command)
            self.assertIn("--bind", command)
            self.assertIn(str(worktree.resolve()), command)
            self.assertEqual(command[-3:], ["python3", "-c", "pass"])


class RetryAndFairnessTests(unittest.TestCase):
    def test_retry_after_failed_attempt_uses_a_fresh_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            flag = tmp / "first-attempt-done"
            agent = (
                "import sys; from pathlib import Path\n"
                f"flag = Path(r'{flag}')\n"
                "if not flag.exists():\n"
                "    flag.write_text('1'); sys.exit(1)\n"
                "Path('generated.txt').write_text('ok')\n"
            )
            entry = _make_project(tmp, "p", agent_code=agent)
            database = tmp / "tasks.sqlite3"
            store = TaskStore(database)
            task = store.create("p", "title", "description")
            with self.assertRaises(RunnerError):
                run_one_task(database, {"p": entry})
            store.retry_failed(task["task_id"], max_retries=2)
            result = run_one_task(database, {"p": entry})
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["result"]["branch"].endswith("-a1"))

    def test_saturated_project_does_not_block_other_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry = {
                "a": _make_project(tmp, "a", agent_code=WRITE_FILE),
                "b": _make_project(tmp, "b", agent_code=WRITE_FILE),
            }
            database = tmp / "tasks.sqlite3"
            store = TaskStore(database)
            task_a = store.create("a", "older", "project a task")
            task_b = store.create("b", "newer", "project b task")
            lease = store.acquire_project_slot("a", 1)  # project a's only slot is busy
            self.assertIsNotNone(lease)
            result = run_one_task(database, registry)
            self.assertEqual(result["task_id"], task_b["task_id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(store.get(task_a["task_id"])["status"], "queued")

    def test_list_is_deterministic_for_tasks_created_in_the_same_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.sqlite3")
            for title in ("one", "two", "three"):
                store.create("p", title, "description")
            self.assertEqual([t["title"] for t in reversed(store.list())], ["one", "two", "three"])


if __name__ == "__main__":
    unittest.main()
