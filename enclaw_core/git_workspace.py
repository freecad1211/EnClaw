"""Isolated Git worktree lifecycle and guarded task-branch integration."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence

from .policy import PolicyError, require_capability


class GitOperationError(RuntimeError):
    """Raised when a Git operation fails or violates repository safety rules."""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode != 0:
        raise GitOperationError(result.stderr.strip() or result.stdout.strip() or "Git command failed.")
    return result


def _repo_root(value: str) -> Path:
    candidate = Path(value).expanduser().resolve()
    result = _git(candidate, "rev-parse", "--show-toplevel")
    root = Path(result.stdout.strip()).resolve()
    if not root.is_dir():
        raise GitOperationError("Repository root does not exist.")
    return root


def _branch_exists(repo: Path, branch: str) -> bool:
    return _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


def _assert_branch_name(branch: str) -> None:
    if not branch or branch.startswith("-"):
        raise GitOperationError(f"Invalid Git branch name: {branch!r}")
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise GitOperationError(f"Invalid Git branch name: {branch!r}")


def _ensure_clean(repo: Path) -> None:
    status = _git(repo, "status", "--porcelain").stdout.strip()
    if status:
        raise GitOperationError("Repository worktree is not clean; refusing to continue.")


def create_worktree(policy: dict[str, Any], task_id: str, branch: str) -> Path:
    require_capability(policy, "create_task_branches")
    _assert_branch_name(branch)
    git_policy = policy["git"]
    prefixes = git_policy["allowed_task_branch_prefixes"]
    if not any(branch.startswith(prefix) for prefix in prefixes):
        raise PolicyError("Task branch does not match an allowed branch prefix.")
    if branch in git_policy["protected_branches"] or branch == git_policy["integration_branch"]:
        raise PolicyError("Cannot create a task worktree for a protected or integration branch.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", task_id):
        raise PolicyError("task_id may contain only letters, numbers, dots, underscores, and hyphens.")

    repo = _repo_root(policy["project"]["repo_root"])
    base = git_policy["base_branch"]
    if not _branch_exists(repo, base):
        raise GitOperationError(f"Configured base branch does not exist locally: {base}")
    if _branch_exists(repo, branch):
        raise GitOperationError(f"Branch already exists: {branch}")

    workspace_root = Path(policy["project"]["workspace_root"]).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    destination = (workspace_root / task_id).resolve()
    if destination == repo or repo in destination.parents:
        raise GitOperationError("Worktree path must not be inside the main repository.")
    if destination.exists():
        raise GitOperationError(f"Worktree path already exists: {destination}")

    _git(repo, "worktree", "add", "-b", branch, str(destination), base)
    return destination


def _run_validation(candidate: Path, commands: Sequence[Sequence[str]], timeout_seconds: int) -> None:
    for command in commands:
        try:
            result = subprocess.run(
                list(command),
                cwd=candidate,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_seconds,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            raise GitOperationError(f"Validation timed out: {command[0]}") from exc
        if result.returncode != 0:
            tail = (result.stdout or "")[-4000:]
            raise GitOperationError(
                f"Validation failed ({result.returncode}): {command!r}\n{tail}"
            )


def integrate_task_branch(
    policy: dict[str, Any],
    source_branch: str,
    target_branch: str,
    review_approved: bool = False,
) -> str:
    require_capability(policy, "integrate_task_branches")
    require_capability(policy, "run_validation")
    git_policy = policy["git"]
    integration = git_policy["integration_branch"]
    protected = set(git_policy["protected_branches"])

    _assert_branch_name(source_branch)
    _assert_branch_name(target_branch)
    if target_branch != integration:
        if target_branch in protected:
            raise PolicyError("Automated merge into a protected branch is denied.")
        raise PolicyError("Target must be the project's configured integration branch.")
    if target_branch in protected:
        raise PolicyError("Integration branch is protected; refusing to merge.")
    if source_branch == target_branch or source_branch in protected:
        raise PolicyError("Source and target must be distinct non-protected branches.")
    if not any(source_branch.startswith(p) for p in git_policy["allowed_task_branch_prefixes"]):
        raise PolicyError("Source branch is not an allowed task branch.")
    if policy["validation"]["require_independent_review"] and not review_approved:
        raise PolicyError("Independent review approval is required by project policy.")

    repo = _repo_root(policy["project"]["repo_root"])
    if not _branch_exists(repo, source_branch):
        raise GitOperationError(f"Source branch does not exist locally: {source_branch}")
    if not _branch_exists(repo, target_branch):
        raise GitOperationError(f"Integration branch does not exist locally: {target_branch}")

    # Do not proceed if the primary checkout has uncommitted changes.
    _ensure_clean(repo)
    candidate_branch = f"enclaw/check/{uuid.uuid4().hex[:12]}"
    candidate_path = Path(tempfile.mkdtemp(prefix="enclaw-candidate-"))
    candidate_path.rmdir()
    created = False
    try:
        _git(repo, "worktree", "add", "-b", candidate_branch, str(candidate_path), target_branch)
        created = True
        _git(candidate_path, "merge", "--no-ff", "--no-edit", source_branch)
        timeout_seconds = max(1, int(policy["limits"]["max_task_minutes"]) * 60)
        _run_validation(candidate_path, policy["validation"]["commands"], timeout_seconds)
        if policy["validation"]["require_independent_review"] and not review_approved:
            raise PolicyError("Review approval is required before integration.")
        # Fast-forward only: if the target moved, this fails rather than rewriting it.
        target_worktrees = _git(repo, "worktree", "list", "--porcelain").stdout
        target_path = None
        current = None
        for line in target_worktrees.splitlines():
            if line.startswith("worktree "):
                current = Path(line.removeprefix("worktree ")).resolve()
            elif line.startswith("branch refs/heads/") and line.removeprefix("branch refs/heads/") == target_branch:
                target_path = current
        if target_path is None:
            raise GitOperationError(
                f"Integration branch {target_branch} must be checked out in a worktree to permit guarded fast-forward."
            )
        _ensure_clean(target_path)
        _git(target_path, "merge", "--ff-only", candidate_branch)
        return f"Integrated {source_branch} into {target_branch} after validation."
    finally:
        if created:
            _git(repo, "worktree", "remove", "--force", str(candidate_path), check=False)
        _git(repo, "branch", "-D", candidate_branch, check=False)
