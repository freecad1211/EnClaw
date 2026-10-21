"""Docker-backed sandbox launcher for EnClaw agent/reviewer commands.

The image must already contain the configured CLI and its runtime. The default
network is disabled. Network access and environment forwarding are explicit
operator choices; this module never mounts the Docker socket or host home.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


class DockerSandboxError(RuntimeError):
    """Raised when a safe Docker sandbox cannot be prepared."""


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise DockerSandboxError(
            f"Git metadata check failed for {path}: {result.stderr.strip()[:500]}"
        )
    return result.stdout.strip()


def build_docker_argv(
    *,
    image: str,
    worktree: Path,
    repo_root: Path,
    command: Sequence[str],
    network: str = "none",
    env_keys: Sequence[str] = (),
) -> list[str]:
    """Build a fail-closed Docker argv. Never invoke a shell or pull an image."""
    if not image or image.startswith("-") or any(ch.isspace() for ch in image):
        raise DockerSandboxError("A valid prebuilt Docker image name is required.")
    if network not in {"none", "bridge"}:
        raise DockerSandboxError("network must be 'none' or 'bridge'.")
    if not command or not all(isinstance(arg, str) and arg for arg in command):
        raise DockerSandboxError("The container command must be a non-empty argv array.")

    worktree = worktree.expanduser().resolve(strict=True)
    repo_root = repo_root.expanduser().resolve(strict=True)
    if not worktree.is_dir() or not repo_root.is_dir():
        raise DockerSandboxError("Repository root and worktree must be directories.")
    if worktree == repo_root:
        raise DockerSandboxError("Refusing to run an agent directly in the primary repository.")

    top_level = Path(_git(worktree, "rev-parse", "--show-toplevel")).resolve()
    if top_level != worktree:
        raise DockerSandboxError("The supplied worktree path is not its Git worktree root.")

    common_text = _git(worktree, "rev-parse", "--git-common-dir")
    common_dir = Path(common_text)
    if not common_dir.is_absolute():
        common_dir = (worktree / common_dir).resolve()
    else:
        common_dir = common_dir.resolve(strict=True)
    expected_text = _git(repo_root, "rev-parse", "--git-common-dir")
    expected_common = Path(expected_text)
    if not expected_common.is_absolute():
        expected_common = (repo_root / expected_common).resolve()
    else:
        expected_common = expected_common.resolve(strict=True)
    if common_dir != expected_common or not common_dir.is_dir():
        raise DockerSandboxError("Worktree and repository root do not share Git metadata.")

    keys = list(env_keys)
    if len(set(keys)) != len(keys):
        raise DockerSandboxError("Environment variable names must not be duplicated.")
    for key in keys:
        if not key or not key.replace("_", "A").isalnum() or key[0].isdigit():
            raise DockerSandboxError(f"Invalid environment variable name: {key!r}")
        if key not in os.environ:
            raise DockerSandboxError(f"Requested environment variable is not set: {key}")

    argv = [
        "docker", "run", "--rm", "--init", "--pull=never",
        "--network", network,
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "128",
        "--memory", "1g",
        "--cpus", "2",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
        "--env", "HOME=/tmp/enclaw-home",
        "--env", "TMPDIR=/tmp",
        "--mount", f"type=bind,src={worktree},dst={worktree}",
        "--mount", f"type=bind,src={common_dir},dst={common_dir},readonly",
        "--workdir", str(worktree),
    ]
    if network == "bridge":
        # Enables an explicitly opted-in bridge network. This does not guarantee
        # access to host-local services; configure the API endpoint separately.
        argv.extend(["--add-host", "host.docker.internal:host-gateway"])
    for key in keys:
        argv.extend(["--env", key])
    argv.extend([image, *command])
    return argv


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        separator = args.index("--")
    except ValueError:
        print("EnClaw Docker sandbox requires '--' before the command.", file=sys.stderr)
        return 2
    options, command = args[:separator], args[separator + 1:]
    parser = argparse.ArgumentParser(prog="enclaw-docker-sandbox")
    parser.add_argument("--image", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--network", choices=("none", "bridge"), default="none")
    parser.add_argument("--env-key", action="append", default=[])
    try:
        config = parser.parse_args(options)
        docker_argv = build_docker_argv(
            image=config.image,
            worktree=Path(config.worktree),
            repo_root=Path(config.repo_root),
            command=command,
            network=config.network,
            env_keys=config.env_key,
        )
        result = subprocess.run(docker_argv, check=False)
        return result.returncode
    except (OSError, ValueError, DockerSandboxError) as exc:
        print(f"EnClaw Docker sandbox refused execution: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
