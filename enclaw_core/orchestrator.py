"""Auditable planner/decomposer for multi-agent task graphs.

Planning is opt-in: a project's policy must configure runner.planner_command.
The planner can only create bounded child tasks; it cannot choose branches,
change policy, merge, or execute commands itself. Child execution still goes
through the existing guarded worker and independent-review/validation gates.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

from .policy import PolicyError, load_policy
from .tasks import TaskStore, TaskStoreError


class OrchestrationError(RuntimeError):
    """Raised when a task plan is invalid or cannot be executed safely."""


def _format_planner_command(template: Sequence[str], prompt: str, repo_root: Path, task_id: str) -> list[str]:
    if not template or not any("{prompt}" in part for part in template):
        raise OrchestrationError("runner.planner_command must be configured and include {prompt}.")
    return [
        part.replace("{prompt}", prompt).replace("{worktree}", str(repo_root)).replace("{task_id}", task_id)
        for part in template
    ]


def _run_planner(command: Sequence[str], cwd: Path, timeout_seconds: int) -> str:
    try:
        result = subprocess.run(
            list(command), cwd=cwd, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout_seconds, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired as exc:
        raise OrchestrationError("Planner exceeded its configured timeout.") from exc
    output = result.stdout or ""
    if result.returncode != 0:
        raise OrchestrationError(f"Planner exited with status {result.returncode}: {output[-3000:]}")
    if len(output) > 100_000:
        raise OrchestrationError("Planner output exceeded 100 KB.")
    return output


def _parse_plan(output: str, max_children: int) -> list[dict[str, str]]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OrchestrationError("Planner must return one JSON object with a 'tasks' array.") from exc
    if not isinstance(payload, dict) or set(payload) != {"tasks"} or not isinstance(payload["tasks"], list):
        raise OrchestrationError("Planner response schema must be exactly {'tasks': [...]}." )
    raw_tasks = payload["tasks"]
    if not 1 <= len(raw_tasks) <= max_children:
        raise OrchestrationError(f"Plan must contain between 1 and {max_children} child tasks.")
    children: list[dict[str, str]] = []
    for index, item in enumerate(raw_tasks):
        if not isinstance(item, dict) or set(item) != {"title", "description"}:
            raise OrchestrationError(f"Child task {index} must contain only title and description.")
        title, description = item["title"], item["description"]
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise OrchestrationError(f"Child task {index} has an invalid title.")
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > 6000:
            raise OrchestrationError(f"Child task {index} has an invalid description.")
        children.append({"title": title.strip(), "description": description.strip()})
    return children


class Orchestrator:
    """Decompose queued tasks and maintain parent/child relations in SQLite."""

    def __init__(self, database_path: str | Path):
        self.store = TaskStore(database_path)
        with self.store._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS task_groups (
                    parent_task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
                    project_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('planned','running','completed','failed','cancelled')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS task_group_children (
                    parent_task_id TEXT NOT NULL REFERENCES task_groups(parent_task_id),
                    child_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
                    ordinal INTEGER NOT NULL,
                    PRIMARY KEY(parent_task_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS task_group_children_parent
                    ON task_group_children(parent_task_id, ordinal);
            """)

    def decompose_one(
        self,
        projects: dict[str, dict[str, Any]],
        *,
        max_children: int = 8,
        actor: str = "enclaw-planner",
    ) -> dict[str, Any] | None:
        if not 1 <= max_children <= 16:
            raise OrchestrationError("max_children must be between 1 and 16.")
        queued = [task for task in self.store.list(limit=200) if task["status"] == "queued"]
        if not queued:
            return None
        parent = queued[-1]
        project = projects.get(parent["project_id"])
        if not project:
            raise OrchestrationError(f"No project registry entry for {parent['project_id']!r}.")
        policy = load_policy(project["policy_path"])
        runner = policy.get("runner", {})
        template = runner.get("planner_command", [])
        if not template:
            raise OrchestrationError("This project has no runner.planner_command; refusing implicit planning.")
        configured_max = int(policy["limits"].get("max_child_tasks", max_children))
        bounded_max = min(max_children, configured_max, 16)
        try:
            parent = self.store.transition(parent["task_id"], "planning", actor=actor, details={"mode": "decompose"})
        except TaskStoreError:
            return None
        try:
            prompt = (
                "Decompose this coding task into independent, reviewable implementation tasks. "
                "Return ONLY JSON with exact schema {\"tasks\":[{\"title\":string,\"description\":string}]}. "
                f"Create 1 to {bounded_max} tasks. Avoid overlapping file ownership where possible. "
                "Each description must be actionable, scoped, and include expected tests. Do not request "
                "secrets, policy changes, deployment, or protected-branch merges.\n"
                f"Project: {parent['project_id']}\nTitle: {parent['title']}\nRequest:\n{parent['description']}"
            )
            output = _run_planner(
                _format_planner_command(template, prompt, Path(policy["project"]["repo_root"]), parent["task_id"]),
                Path(policy["project"]["repo_root"]),
                max(10, min(int(runner.get("max_planner_seconds", 180)), 600)),
            )
            children = _parse_plan(output, bounded_max)
            created: list[dict[str, Any]] = []
            with self.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM task_groups WHERE parent_task_id=?", (parent["task_id"],)).fetchone():
                    db.rollback()
                    raise OrchestrationError("Task has already been decomposed; refusing duplicate children.")
                db.execute(
                    "INSERT INTO task_groups(parent_task_id, project_id, state) VALUES (?, ?, 'planned')",
                    (parent["task_id"], parent["project_id"]),
                )
                for ordinal, child in enumerate(children):
                    child_task = self.store.create(
                        parent["project_id"], child["title"],
                        f"Parent task: {parent['task_id']}\nParent objective: {parent['title']}\n\n{child['description']}",
                        actor=actor,
                    )
                    db.execute(
                        "INSERT INTO task_group_children(parent_task_id, child_task_id, ordinal) VALUES (?, ?, ?)",
                        (parent["task_id"], child_task["task_id"], ordinal),
                    )
                    created.append(child_task)
                db.commit()
            self.store.transition(
                parent["task_id"], "running", actor=actor,
                details={"child_count": len(created), "child_ids": [x["task_id"] for x in created]},
            )
            with self.store._connect() as db:
                db.execute("UPDATE task_groups SET state='running' WHERE parent_task_id=?", (parent["task_id"],))
            return {"parent": parent["task_id"], "children": [x["task_id"] for x in created], "state": "running"}
        except Exception as exc:
            try:
                current = self.store.get(parent["task_id"])
                if current["status"] == "planning":
                    self.store.transition(parent["task_id"], "failed", actor=actor, details={"reason": str(exc)[:1000]})
            except (TaskStoreError, RuntimeError):
                pass
            if isinstance(exc, (OrchestrationError, PolicyError, TaskStoreError, OSError)):
                raise
            raise OrchestrationError(f"Planning failed safely: {type(exc).__name__}: {exc}") from exc

    def refresh_groups(self, *, actor: str = "enclaw-orchestrator") -> list[dict[str, Any]]:
        """Aggregate terminal child states into their parent, idempotently."""
        with self.store._connect() as db:
            groups = db.execute(
                "SELECT parent_task_id, project_id, state FROM task_groups "
                "WHERE state IN ('planned','running') ORDER BY created_at"
            ).fetchall()
            output = []
            for group in groups:
                rows = db.execute(
                    "SELECT t.task_id, t.status FROM task_group_children c "
                    "JOIN tasks t ON t.task_id=c.child_task_id WHERE c.parent_task_id=? ORDER BY c.ordinal",
                    (group["parent_task_id"],),
                ).fetchall()
                statuses = [row["status"] for row in rows]
                if not statuses or any(status not in {"completed", "failed", "cancelled"} for status in statuses):
                    continue
                if all(status == "completed" for status in statuses):
                    state = "completed"
                elif all(status == "cancelled" for status in statuses):
                    state = "cancelled"
                else:
                    state = "failed"
                output.append({
                    "parent_task_id": group["parent_task_id"], "state": state,
                    "children": [{"task_id": row["task_id"], "status": row["status"]} for row in rows],
                })
        for item in output:
            parent = self.store.get(item["parent_task_id"])
            if item["state"] == "completed":
                self.store.transition(parent["task_id"], "validating", actor=actor, details={"aggregation": "all children completed"})
                self.store.transition(parent["task_id"], "integrating", actor=actor, details={"aggregation": "child tasks passed their own review and validation gates; parent is a summary"})
                self.store.transition(
                    parent["task_id"], "completed", actor=actor,
                    details={"child_count": len(item["children"])},
                    result={"children": item["children"]},
                )
            elif item["state"] == "cancelled":
                self.store.transition(parent["task_id"], "cancelled", actor=actor, details={"reason": "all child tasks cancelled"})
            else:
                self.store.transition(
                    parent["task_id"], "failed", actor=actor,
                    details={"reason": "one or more child tasks failed", "children": item["children"]},
                )
            with self.store._connect() as db:
                db.execute("UPDATE task_groups SET state=? WHERE parent_task_id=?", (item["state"], item["parent_task_id"]))
        return output
