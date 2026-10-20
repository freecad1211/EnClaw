"""Bounded one-task coding worker using explicitly configured agent CLIs.

The worker is intentionally opt-in. It requires both an implementation command and
an independent review command in the project's policy, and never uses shell=True.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from .git_workspace import GitOperationError, create_worktree, integrate_task_branch, _git
from .policy import PolicyError, load_policy, require_capability
from .tasks import TaskStore, TaskStoreError


class RunnerError(RuntimeError):
    """Raised when a task cannot be safely executed."""


_FORBIDDEN_PATH_PARTS = {
    ".env", ".ssh", ".aws", ".gnupg", "secrets", "credentials",
    "id_rsa", "id_ed25519", "known_hosts",
}
_FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def _changed_paths(worktree: Path) -> list[str]:
    changed = _git(worktree, "status", "--porcelain", "-z").stdout
    paths: list[str] = []
    for item in changed.split("\0"):
        if not item:
            continue
        # Porcelain v1 prefixes each pathname with two status columns and a space.
        path = item[3:] if len(item) >= 4 else item
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.replace("\\", "/"))
    return paths


def _check_changed_paths(paths: Sequence[str]) -> None:
    for path in paths:
        parts = {part.lower() for part in Path(path).parts}
        name = Path(path).name.lower()
        if (
            parts & _FORBIDDEN_PATH_PARTS
            or name == ".env"
            or name.startswith(".env.")
            or name.endswith(tuple(_FORBIDDEN_SUFFIXES))
            or path.startswith(".enclaw/")
            or path == ".git"
        ):
            raise RunnerError(f"Changed path is outside the default agent allowlist: {path}")


def _format_command(template: Sequence[str], prompt: str, worktree: Path, task_id: str) -> list[str]:
    if not template:
        raise RunnerError("Runner command is not configured in this project's policy.")
    rendered = [
        part.replace("{prompt}", prompt)
            .replace("{worktree}", str(worktree))
            .replace("{task_id}", task_id)
        for part in template
    ]
    if not any("{prompt}" in part for part in template):
        raise RunnerError("Configured runner command must include the {prompt} placeholder.")
    return rendered


def _run_bounded(
    command: Sequence[str],
    cwd: Path,
    timeout_seconds: int,
    store: TaskStore,
    task_id: str,
) -> tuple[int, str]:
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    deadline = time.monotonic() + timeout_seconds
    output = ""
    while True:
        try:
            output, _ = process.communicate(timeout=min(1.0, max(0.1, deadline - time.monotonic())))
            return process.returncode, output[-12000:]
        except subprocess.TimeoutExpired:
            try:
                task = store.get(task_id)
            except TaskStoreError:
                process.terminate()
                process.wait(timeout=5)
                raise RunnerError("Task disappeared while its agent was running.")
            if task["cancellation_requested"]:
                process.terminate()
                try:
                    output, _ = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    output, _ = process.communicate()
                raise RunnerError("Task cancellation requested; agent process stopped.")
            if time.monotonic() >= deadline:
                process.terminate()
                try:
                    output, _ = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    output, _ = process.communicate()
                raise RunnerError(f"Agent command exceeded timeout of {timeout_seconds} seconds.")


def _task_prompt(task: dict[str, Any], project_root: Path) -> str:
    return (
        "You are an implementation agent working in an isolated Git worktree.\n"
        f"Project: {task['project_id']}\nTask ID: {task['task_id']}\n"
        f"Project root (reference only): {project_root}\n"
        f"Task title: {task['title']}\nTask description:\n{task['description']}\n\n"
        "Implement the requested change in the current working directory only. "
        "Do not commit changes. Do not modify secrets, .env files, EnClaw policy, "
        "system services, unrelated repositories, or files outside the worktree. "
        "Run relevant project checks if appropriate and summarize what changed."
    )


def run_one_task(
    database_path: str | Path,
    projects: dict[str, dict[str, Any]],
    *,
    actor: str = "enclaw-worker",
) -> dict[str, Any] | None:
    """Execute the oldest queued task once. Returns None if the queue is empty."""
    store = TaskStore(database_path)
    queued = [task for task in store.list(limit=200) if task["status"] == "queued"]
    if not queued:
        return None
    task = queued[-1]  # list() is newest-first; pick the oldest queued task.
    task_id = task["task_id"]
    try:
        task = store.transition(task_id, "planning", actor=actor)
    except TaskStoreError:
        return None  # Another worker claimed it first.

    worktree: Path | None = None
    try:
        project = projects.get(task["project_id"])
        if not project:
            raise RunnerError(f"No project registry entry for {task['project_id']!r}.")
        policy = load_policy(project["policy_path"])
        require_capability(policy, "create_task_branches")
        require_capability(policy, "edit_files")
        require_capability(policy, "create_commits")
        require_capability(policy, "run_validation")
        runner = policy.get("runner", {})
        agent_command = runner.get("agent_command", [])
        review_command = runner.get("review_command", [])
        if not agent_command or not review_command:
            raise RunnerError("Both runner.agent_command and runner.review_command must be configured.")

        branch = f"enclaw/task/{task_id}"
        worktree = create_worktree(policy, task_id, branch)
        task = store.transition(task_id, "running", actor=actor, details={"branch": branch, "worktree": str(worktree)})
        prompt = _task_prompt(task, Path(policy["project"]["repo_root"]))
        timeout = min(
            int(runner.get("max_agent_minutes", policy["limits"]["max_task_minutes"])) * 60,
            int(policy["limits"]["max_task_minutes"]) * 60,
        )
        if timeout <= 0:
            raise RunnerError("Agent timeout must be greater than zero.")
        command = _format_command(agent_command, prompt, worktree, task_id)
        return_code, output = _run_bounded(command, worktree, timeout, store, task_id)
        if return_code != 0:
            raise RunnerError(f"Agent command exited with status {return_code}:\n{output[-3000:]}")

        paths = _changed_paths(worktree)
        if not paths:
            raise RunnerError("Agent completed without making any tracked or untracked changes.")
        _check_changed_paths(paths)
        _git(worktree, "add", "--all")
        staged = _git(worktree, "diff", "--cached", "--name-only", "-z").stdout
        staged_paths = [path for path in staged.split("\0") if path]
        _check_changed_paths(staged_paths)
        _git(worktree, "config", "user.name", "EnClaw Agent")
        _git(worktree, "config", "user.email", "enclaw-agent@localhost")
        _git(worktree, "commit", "-m", f"agent: {task['title'][:60]}")
        commit_before_review = _git(worktree, "rev-parse", "HEAD").stdout.strip()
        task = store.transition(task_id, "validating", actor=actor, details={"commit": commit_before_review, "changed_paths": paths})

        diff = _git(worktree, "show", "--format=fuller", "--stat", "HEAD").stdout
        review_prompt = (
            "You are an independent, read-only code reviewer. Do not modify files. "
            "Inspect the committed change in the current worktree and the task requirements below. "
            "Look for correctness, security, missing tests, regressions, and policy violations. "
            "Your first non-empty output line must be exactly APPROVE or REJECT. "
            "Approve only if the change is acceptable; explain blocking findings after REJECT.\n\n"
            f"Task: {task['title']}\n{task['description']}\n\nCommit summary:\n{diff}"
        )
        review_argv = _format_command(review_command, review_prompt, worktree, task_id)
        review_code, review_output = _run_bounded(review_argv, worktree, timeout, store, task_id)
        if review_code != 0:
            raise RunnerError(f"Reviewer command exited with status {review_code}:\n{review_output[-3000:]}")
        if _git(worktree, "rev-parse", "HEAD").stdout.strip() != commit_before_review or _changed_paths(worktree):
            raise RunnerError("Reviewer changed the worktree; review must be read-only.")
        first_line = next((line.strip() for line in review_output.splitlines() if line.strip()), "")
        if first_line != "APPROVE":
            raise RunnerError(f"Independent review did not approve the change: {review_output[-3000:]}")
        task = store.transition(task_id, "integrating", actor=actor, details={"review": "APPROVE"})
        result = integrate_task_branch(
            policy,
            source_branch=branch,
            target_branch=policy["git"]["integration_branch"],
            review_approved=True,
        )
        return store.transition(
            task_id, "completed", actor=actor,
            details={"integration_result": result},
            result={"branch": branch, "worktree": str(worktree), "commit": commit_before_review},
        )
    except Exception as exc:
        try:
            current = store.get(task_id)
            if current["status"] not in {"completed", "failed", "cancelled"}:
                store.transition(task_id, "failed", actor=actor, details={"reason": str(exc)[:3000]})
        except Exception:
            pass
        raise RunnerError(f"Task {task_id} failed safely: {exc}") from exc
