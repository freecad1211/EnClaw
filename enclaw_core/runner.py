"""Bounded one-task coding worker using explicitly configured agent CLIs.

The worker is intentionally opt-in. It requires both an implementation command and
an independent review command in the project's policy, and never uses shell=True.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
_DEPENDENCY_FILES = {
    "package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock",
    "requirements.txt", "pyproject.toml", "poetry.lock", "uv.lock",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum",
}


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


def _check_changed_paths(paths: Sequence[str], *, allow_dependencies: bool = False) -> None:
    for path in paths:
        parts = {part.lower() for part in Path(path).parts}
        name = Path(path).name.lower()
        if (
            parts & _FORBIDDEN_PATH_PARTS
            or name == ".env"
            or name.startswith(".env.")
            or "secret" in name
            or "credential" in name
            or name.endswith(tuple(_FORBIDDEN_SUFFIXES))
            or path.startswith(".enclaw/")
            or path == ".git"
            or (not allow_dependencies and name in _DEPENDENCY_FILES)
            or path.startswith(".github/workflows/")
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


def _run_one_task_claimed(
    database_path: str | Path,
    projects: dict[str, dict[str, Any]],
    *,
    actor: str = "enclaw-worker",
    task_id_override: str | None = None,
) -> dict[str, Any] | None:
    """Execute one preselected queued task after the project slot is acquired."""
    store = TaskStore(database_path)
    if task_id_override:
        task = store.get(task_id_override)
        if task["status"] != "queued":
            return None
    else:
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
        if task["cancellation_requested"]:
            store.transition(task_id, "cancelled", actor=actor, details={"reason": "cancelled before agent start"})
            return store.get(task_id)
        prompt = _task_prompt(task, Path(policy["project"]["repo_root"]))
        starting_commit = _git(worktree, "rev-parse", "HEAD").stdout.strip()
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
        if _git(worktree, "rev-parse", "HEAD").stdout.strip() != starting_commit:
            raise RunnerError("Agent created commits directly; EnClaw requires uncommitted changes for safety checks.")
        if store.get(task_id)["cancellation_requested"]:
            raise RunnerError("Task cancellation requested after agent execution.")

        paths = _changed_paths(worktree)
        if not paths:
            raise RunnerError("Agent completed without making any tracked or untracked changes.")
        _check_changed_paths(paths, allow_dependencies=policy["permissions"]["change_dependencies"])
        _git(worktree, "add", "--all")
        staged = _git(worktree, "diff", "--cached", "--name-only", "-z").stdout
        staged_paths = [path for path in staged.split("\0") if path]
        _check_changed_paths(staged_paths, allow_dependencies=policy["permissions"]["change_dependencies"])
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
                final_status = "cancelled" if current["cancellation_requested"] else "failed"
                store.transition(task_id, final_status, actor=actor, details={"reason": str(exc)[:3000]})
        except Exception:
            pass
        raise RunnerError(f"Task {task_id} failed safely: {exc}") from exc

def run_one_task(
    database_path: str | Path,
    projects: dict[str, dict[str, Any]],
    *,
    actor: str = "enclaw-worker",
) -> dict[str, Any] | None:
    """Run one task while enforcing the project's worker limit across processes."""
    store = TaskStore(database_path)
    queued = [task for task in store.list(limit=200) if task["status"] == "queued"]
    if not queued:
        return None
    task = queued[-1]
    project = projects.get(task["project_id"])
    if not project:
        # Preserve the existing worker's auditable failure path for bad registry entries.
        return _run_one_task_claimed(database_path, projects, actor=actor, task_id_override=task["task_id"])
    try:
        policy = load_policy(project["policy_path"])
        limit = int(policy["limits"]["max_concurrent_agents"])
    except (PolicyError, OSError, ValueError) as exc:
        raise RunnerError(f"Cannot load worker concurrency policy: {exc}") from exc
    lease = store.acquire_project_slot(task["project_id"], limit)
    if lease is None:
        return None
    try:
        return _run_one_task_claimed(
            database_path, projects, actor=actor, task_id_override=task["task_id"]
        )
    finally:
        store.release_project_slot(lease)

def run_worker_pool(
    database_path: str | Path,
    projects: dict[str, dict[str, Any]],
    *,
    workers: int = 4,
    idle_seconds: int = 5,
    actor: str = "enclaw-pool",
) -> list[dict[str, Any]]:
    """Run a bounded local worker pool; per-project SQLite slots remain authoritative."""
    if not 1 <= workers <= 16:
        raise RunnerError("workers must be between 1 and 16.")
    if not 1 <= idle_seconds <= 300:
        raise RunnerError("idle_seconds must be between 1 and 300.")
    results: list[dict[str, Any]] = []
    results_lock = threading.Lock()

    def worker(index: int) -> None:
        idle_since = time.monotonic()
        while time.monotonic() - idle_since < idle_seconds:
            try:
                result = run_one_task(database_path, projects, actor=f"{actor}:{index}")
            except RunnerError as exc:
                with results_lock:
                    results.append({"status": "failed", "error": str(exc)})
                idle_since = time.monotonic()
                continue
            if result is not None:
                with results_lock:
                    results.append(result)
                idle_since = time.monotonic()
                continue
            store = TaskStore(database_path)
            if not any(task["status"] == "queued" for task in store.list(limit=200)):
                return
            time.sleep(0.25)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="enclaw-worker") as pool:
        futures = [pool.submit(worker, index) for index in range(workers)]
        for future in futures:
            future.result()
    return results
