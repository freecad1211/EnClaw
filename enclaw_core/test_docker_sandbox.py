"""Unit tests for the Docker sandbox argv builder; no Docker daemon required."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .docker_sandbox import DockerSandboxError, build_docker_argv


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class DockerSandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "README.md").write_text("test\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial")
        self.worktree = root / "worktree"
        git(self.repo, "worktree", "add", "-b", "enclaw/task/test", str(self.worktree), "main")

    def build(self, **overrides):
        values = {
            "image": "enclaw-agent:test",
            "worktree": self.worktree,
            "repo_root": self.repo,
            "command": ["agent-cli", "run", "prompt"],
        }
        values.update(overrides)
        return build_docker_argv(**values)

    def test_defaults_are_network_isolated_and_hardened(self):
        argv = self.build()
        self.assertEqual(argv[:3], ["docker", "run", "--rm"])
        self.assertIn("--network", argv)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop", argv)
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
        self.assertIn("no-new-privileges", argv)
        self.assertIn("--pids-limit", argv)
        self.assertIn("--memory", argv)
        self.assertIn("--cpus", argv)
        self.assertNotIn("/var/run/docker.sock", " ".join(argv))
        mount_specs = [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == "--mount"]
        self.assertFalse(any(mount.startswith(f"type=bind,src={Path.home()},dst=") for mount in mount_specs))
        self.assertEqual(argv[-3:], ["agent-cli", "run", "prompt"])

    def test_git_common_metadata_is_read_only_and_worktree_is_writable(self):
        argv = self.build()
        mounts = [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == "--mount"]
        self.assertTrue(any(f"src={self.worktree.resolve()},dst={self.worktree.resolve()}" in m for m in mounts))
        self.assertTrue(any(m.endswith(",readonly") and ".git" in m for m in mounts))

    def test_network_bridge_requires_explicit_choice(self):
        argv = self.build(network="bridge")
        self.assertEqual(argv[argv.index("--network") + 1], "bridge")
        self.assertIn("host.docker.internal:host-gateway", argv)

    def test_only_explicit_environment_keys_are_forwarded(self):
        with mock.patch.dict(os.environ, {"OPENAI_COMPAT_API_KEY": "secret", "DISCORD_BOT_TOKEN": "should-not-pass"}):
            argv = self.build(env_keys=["OPENAI_COMPAT_API_KEY"])
        self.assertIn("OPENAI_COMPAT_API_KEY", argv)
        self.assertNotIn("DISCORD_BOT_TOKEN", argv)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(DockerSandboxError, "not set"):
                self.build(env_keys=["OPENAI_COMPAT_API_KEY"])

    def test_rejects_primary_repository_as_worktree(self):
        with self.assertRaisesRegex(DockerSandboxError, "primary repository"):
            self.build(worktree=self.repo)

    def test_rejects_invalid_network_and_image(self):
        with self.assertRaisesRegex(DockerSandboxError, "network"):
            self.build(network="host")
        with self.assertRaisesRegex(DockerSandboxError, "image"):
            self.build(image="--privileged")

    def test_rejects_worktree_from_another_repository(self):
        other = Path(self.temp.name) / "other"
        other.mkdir()
        git(other, "init", "-b", "main")
        git(other, "config", "user.name", "Test")
        git(other, "config", "user.email", "test@example.invalid")
        (other / "README.md").write_text("other\n", encoding="utf-8")
        git(other, "add", "README.md")
        git(other, "commit", "-m", "initial")
        unrelated_worktree = Path(self.temp.name) / "other-worktree"
        git(other, "worktree", "add", "-b", "enclaw/task/other", str(unrelated_worktree), "main")
        with self.assertRaisesRegex(DockerSandboxError, "do not share"):
            self.build(worktree=unrelated_worktree)


if __name__ == "__main__":
    unittest.main()
