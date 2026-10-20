"""Integration tests using disposable local Git repositories."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from .git_workspace import GitOperationError, create_worktree, integrate_task_branch
from .policy import PolicyError, default_policy


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class GitWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-b", "develop")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "EnClaw Tests")
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        git(self.root, "add", "README.md")
        git(self.root, "commit", "-m", "initial")
        git(self.root, "branch", "enclaw/integration", "develop")
        self.policy = default_policy("test", str(self.root), "develop", "enclaw/integration")
        self.policy["project"]["workspace_root"] = str(self.root.parent / "worktrees")
        self.policy["validation"]["require_independent_review"] = False
        self.policy["validation"]["commands"] = [[
            "python3", "-c", "from pathlib import Path; assert Path('README.md').exists()"
        ]]

    def test_create_worktree_isolated_from_main_checkout(self):
        path = create_worktree(self.policy, "task-1", "enclaw/task/task-1")
        self.assertTrue((path / ".git").exists())
        self.assertEqual(git(self.root, "branch", "--show-current"), "develop")

    def test_rejects_branch_outside_allowed_prefix(self):
        with self.assertRaises(PolicyError):
            create_worktree(self.policy, "task-2", "feature/not-isolated")

    def test_rejects_target_other_than_integration(self):
        git(self.root, "branch", "enclaw/task/task-3", "develop")
        with self.assertRaises(PolicyError):
            integrate_task_branch(self.policy, "enclaw/task/task-3", "develop")

    def test_integration_refuses_empty_validation_commands(self):
        git(self.root, "branch", "enclaw/task/task-empty-checks", "develop")
        integration_path = self.root.parent / "integration-empty-checks"
        git(self.root, "worktree", "add", str(integration_path), "enclaw/integration")
        policy = dict(self.policy)
        policy["validation"] = dict(self.policy["validation"])
        policy["validation"]["commands"] = []
        with self.assertRaises(PolicyError):
            integrate_task_branch(
                policy, "enclaw/task/task-empty-checks", "enclaw/integration"
            )

    def test_requires_review_when_policy_requires_it(self):
        policy = dict(self.policy)
        policy["validation"] = dict(self.policy["validation"])
        policy["validation"]["require_independent_review"] = True
        git(self.root, "branch", "enclaw/task/task-4", "develop")
        with self.assertRaises(PolicyError):
            integrate_task_branch(policy, "enclaw/task/task-4", "enclaw/integration")

    def test_integration_runs_candidate_validation(self):
        task_path = create_worktree(self.policy, "task-5", "enclaw/task/task-5")
        (task_path / "feature.txt").write_text("done\n", encoding="utf-8")
        git(task_path, "add", "feature.txt")
        git(task_path, "commit", "-m", "add feature")
        # Target must be checked out in a worktree for the final fast-forward.
        integration_path = self.root.parent / "integration"
        git(self.root, "worktree", "add", str(integration_path), "enclaw/integration")
        result = integrate_task_branch(
            self.policy, "enclaw/task/task-5", "enclaw/integration", review_approved=True
        )
        self.assertIn("Integrated", result)
        self.assertTrue((integration_path / "feature.txt").exists())


if __name__ == "__main__":
    unittest.main()
