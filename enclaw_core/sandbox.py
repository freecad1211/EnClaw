"""Linux bubblewrap sandbox backend for EnClaw worker subprocesses.

This is defense in depth, not a substitute for a dedicated unprivileged account
or container/VM boundary.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


class SandboxError(RuntimeError):
    """Raised when the Linux sandbox cannot be safely initialized."""


_MASKED_ROOTS = (Path("/home"), Path("/root"), Path("/tmp"))


def _masked_parent_dirs(path: Path) -> list[str]:
    """Return directory creation args for paths hidden by a tmpfs mount."""
    for masked in _MASKED_ROOTS:
        try:
            relative = path.relative_to(masked)
        except ValueError:
            continue
        current = masked
        result: list[str] = []
        for part in relative.parts:
            current = current / part
            result.extend(["--dir", str(current)])
        return result
    return []


def build_bwrap_argv(worktree: Path, repo_root: Path, command: Sequence[str]) -> list[str]:
    """Build a fail-closed bubblewrap invocation without a shell."""
    if sys.platform != "linux":
        raise SandboxError("The built-in bubblewrap sandbox is supported on Linux only.")
    if not command or not all(isinstance(part, str) and part for part in command):
        raise SandboxError("Sandbox command must be a non-empty argv array.")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SandboxError("bubblewrap (bwrap) is not installed; refusing unsandboxed execution.")

    worktree = worktree.expanduser().resolve(strict=True)
    repo_root = repo_root.expanduser().resolve(strict=True)
    if not worktree.is_dir() or not repo_root.is_dir():
        raise SandboxError("Sandbox worktree and repository root must be existing directories.")

    common = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if common.returncode != 0:
        raise SandboxError("Cannot resolve the repository's Git metadata directory.")
    common_dir = Path(common.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (repo_root / common_dir).resolve()
    if not common_dir.is_dir():
        raise SandboxError("Git metadata directory is missing or not a directory.")

    argv = [
        bwrap, "--die-with-parent", "--new-session", "--unshare-all",
        "--ro-bind", "/", "/",
        "--tmpfs", "/home", "--tmpfs", "/root", "--tmpfs", "/tmp",
        "--dev", "/dev", "--proc", "/proc",
    ]
    # Host home directories and temporary files are hidden. The worktree is the
    # only writable host mount; shared Git metadata is read-only.
    for path in (common_dir, worktree):
        argv.extend(_masked_parent_dirs(path.parent))
        if any(path == root for root in _MASKED_ROOTS):
            raise SandboxError(f"Refusing to mount a masked root directly: {path}")
    argv.extend(["--dir", str(common_dir), "--ro-bind", str(common_dir), str(common_dir)])
    argv.extend(["--dir", str(worktree), "--bind", str(worktree), str(worktree)])
    argv.extend([
        "--chdir", str(worktree), "--setenv", "HOME", "/tmp",
        "--setenv", "TMPDIR", "/tmp", "--",
    ])
    argv.extend(command)
    return argv


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        separator = args.index("--")
    except ValueError:
        print("EnClaw sandbox requires '--' before the command.", file=sys.stderr)
        return 2
    prefix, command = args[:separator], args[separator + 1:]
    values: dict[str, str] = {}
    index = 0
    while index < len(prefix):
        key = prefix[index]
        if key not in {"--worktree", "--repo-root"} or index + 1 >= len(prefix) or key in values:
            print(f"Invalid sandbox option: {key}", file=sys.stderr)
            return 2
        values[key] = prefix[index + 1]
        index += 2
    if set(values) != {"--worktree", "--repo-root"} or not command:
        print("Sandbox requires --worktree, --repo-root, and a command after '--'.", file=sys.stderr)
        return 2
    try:
        command_argv = build_bwrap_argv(Path(values["--worktree"]), Path(values["--repo-root"]), command)
        return subprocess.run(command_argv, check=False).returncode
    except (OSError, SandboxError, subprocess.SubprocessError) as exc:
        print(f"EnClaw sandbox refused execution: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
