"""Command-line interface for the EnClaw core."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .git_workspace import create_worktree, integrate_task_branch
from .policy import PolicyError, default_policy, load_policy, validate_policy
from .tasks import TaskStore, TaskStoreError
from .runner import run_one_task, run_worker_pool, RunnerError
from .orchestrator import Orchestrator, OrchestrationError


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise PolicyError(f"Refusing to overwrite existing file: {path}")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _store(database: str | None) -> TaskStore:
    return TaskStore(database or os.environ.get(
        "ENCLAW_DB_PATH", "~/.local/state/enclaw/tasks.sqlite3"
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enclaw", description="Project-aware EnClaw safety core")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-project", help="Create a per-project policy file without modifying Git")
    init.add_argument("repo")
    init.add_argument("--id", required=True)
    init.add_argument("--base", default="main")
    init.add_argument("--integration", default="enclaw/integration")
    init.add_argument("--output", default=".enclaw/policy.json")

    validate = sub.add_parser("validate", help="Validate a policy JSON file")
    validate.add_argument("policy")

    show = sub.add_parser("show-policy", help="Print a normalized project policy")
    show.add_argument("policy")

    worktree = sub.add_parser("create-worktree", help="Create an isolated task worktree")
    worktree.add_argument("policy")
    worktree.add_argument("task_id")
    worktree.add_argument("--branch", required=True)

    integrate = sub.add_parser("integrate", help="Validate and integrate a task branch")
    integrate.add_argument("policy")
    integrate.add_argument("--source", required=True)
    integrate.add_argument("--target", required=True)
    integrate.add_argument("--review-approved", action="store_true")

    task_create = sub.add_parser("task-create", help="Add a task to the durable queue (does not run it)")
    task_create.add_argument("--db")
    task_create.add_argument("--project", required=True)
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--description", required=True)

    task_list = sub.add_parser("task-list", help="List queued and historical tasks")
    task_list.add_argument("--db")
    task_list.add_argument("--project")
    task_list.add_argument("--limit", type=int, default=50)

    task_show = sub.add_parser("task-show", help="Show one task and its audit events")
    task_show.add_argument("task_id")
    task_show.add_argument("--db")

    task_cancel = sub.add_parser("task-cancel", help="Request cooperative cancellation")
    task_cancel.add_argument("task_id")
    task_cancel.add_argument("--db")

    task_retry = sub.add_parser("task-retry", help="Retry a failed task within its project retry budget")
    task_retry.add_argument("task_id")
    task_retry.add_argument("--policy", required=True)
    task_retry.add_argument("--db")

    task_events = sub.add_parser("task-events", help="Print task audit events")
    task_events.add_argument("task_id")
    task_events.add_argument("--db")

    worker = sub.add_parser("worker-once", help="Execute at most one queued task using the project's configured agent and reviewer CLIs")
    worker.add_argument("--db")
    worker.add_argument("--projects", default="~/.config/enclaw/projects.json")

    pool = sub.add_parser("worker-pool", help="Run a bounded worker pool respecting project concurrency policies")
    pool.add_argument("--db")
    pool.add_argument("--projects", default="~/.config/enclaw/projects.json")
    pool.add_argument("--workers", type=int, default=4)
    pool.add_argument("--idle-seconds", type=int, default=5)

    plan = sub.add_parser("orchestrate-once", help="Decompose one queued parent task using its configured planner CLI")
    plan.add_argument("--db")
    plan.add_argument("--projects", default="~/.config/enclaw/projects.json")
    plan.add_argument("--max-children", type=int, default=8)

    refresh = sub.add_parser("orchestrate-refresh", help="Aggregate completed child tasks into parent task status")
    refresh.add_argument("--db")

    args = parser.parse_args(argv)
    try:
        if args.command == "init-project":
            repo = Path(args.repo).expanduser().resolve()
            policy = validate_policy(default_policy(args.id, str(repo), args.base, args.integration))
            output = (repo / args.output).resolve()
            if repo not in output.parents:
                raise PolicyError("Policy output must be inside the configured project root.")
            _write_json(output, policy)
            print(f"Created policy: {output}")
            print("No agents were started and no Git branches were changed.")
        elif args.command == "validate":
            policy = load_policy(args.policy)
            print(f"Policy valid: {policy['project']['id']}")
        elif args.command == "show-policy":
            print(json.dumps(load_policy(args.policy), indent=2))
        elif args.command == "create-worktree":
            policy = load_policy(args.policy)
            print(create_worktree(policy, args.task_id, args.branch))
        elif args.command == "integrate":
            policy = load_policy(args.policy)
            print(integrate_task_branch(
                policy, args.source, args.target, review_approved=args.review_approved
            ))
        elif args.command == "task-create":
            print(json.dumps(_store(args.db).create(args.project, args.title, args.description), indent=2))
        elif args.command == "task-list":
            print(json.dumps(_store(args.db).list(args.project, args.limit), indent=2))
        elif args.command == "task-show":
            store = _store(args.db)
            print(json.dumps({"task": store.get(args.task_id), "events": store.events(args.task_id)}, indent=2))
        elif args.command == "task-cancel":
            print(json.dumps(_store(args.db).request_cancel(args.task_id), indent=2))
        elif args.command == "task-retry":
            policy = load_policy(args.policy)
            print(json.dumps(_store(args.db).retry_failed(
                args.task_id, max_retries=policy["limits"]["max_retries"]
            ), indent=2))
        elif args.command == "task-events":
            print(json.dumps(_store(args.db).events(args.task_id), indent=2))
        elif args.command == "orchestrate-once":
            registry_path = Path(args.projects).expanduser().resolve()
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            entries = registry.get("projects") if isinstance(registry, dict) else None
            if not isinstance(entries, list):
                raise PolicyError("Project registry must contain a 'projects' array.")
            projects = {}
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("policy_path"), str):
                    raise PolicyError("Every project registry entry requires string id and policy_path.")
                if entry["id"] in projects:
                    raise PolicyError(f"Duplicate project id in registry: {entry['id']}")
                policy_path = Path(entry["policy_path"]).expanduser()
                if not policy_path.is_absolute():
                    policy_path = registry_path.parent / policy_path
                projects[entry["id"]] = {**entry, "policy_path": str(policy_path.resolve())}
            result = Orchestrator(args.db or os.environ.get("ENCLAW_DB_PATH", "~/.local/state/enclaw/tasks.sqlite3")).decompose_one(projects, max_children=args.max_children)
            print(json.dumps(result, indent=2) if result else "No queued task was available.")
        elif args.command == "orchestrate-refresh":
            result = Orchestrator(args.db or os.environ.get("ENCLAW_DB_PATH", "~/.local/state/enclaw/tasks.sqlite3")).refresh_groups()
            print(json.dumps(result, indent=2))
        elif args.command == "worker-pool":
            registry_path = Path(args.projects).expanduser().resolve()
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            entries = registry.get("projects") if isinstance(registry, dict) else None
            if not isinstance(entries, list):
                raise PolicyError("Project registry must contain a 'projects' array.")
            projects = {}
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("policy_path"), str):
                    raise PolicyError("Every project registry entry requires string id and policy_path.")
                if entry["id"] in projects:
                    raise PolicyError(f"Duplicate project id in registry: {entry['id']}")
                policy_path = Path(entry["policy_path"]).expanduser()
                if not policy_path.is_absolute():
                    policy_path = registry_path.parent / policy_path
                projects[entry["id"]] = {**entry, "policy_path": str(policy_path.resolve())}
            result = run_worker_pool(
                args.db or os.environ.get("ENCLAW_DB_PATH", "~/.local/state/enclaw/tasks.sqlite3"),
                projects, workers=args.workers, idle_seconds=args.idle_seconds,
            )
            print(json.dumps(result, indent=2))
        elif args.command == "worker-once":
            registry_path = Path(args.projects).expanduser().resolve()
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            entries = registry.get("projects") if isinstance(registry, dict) else None
            if not isinstance(entries, list):
                raise PolicyError("Project registry must contain a 'projects' array.")
            projects = {}
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("policy_path"), str):
                    raise PolicyError("Every project registry entry requires string id and policy_path.")
                if entry["id"] in projects:
                    raise PolicyError(f"Duplicate project id in registry: {entry['id']}")
                policy_path = Path(entry["policy_path"]).expanduser()
                if not policy_path.is_absolute():
                    policy_path = registry_path.parent / policy_path
                projects[entry["id"]] = {**entry, "policy_path": str(policy_path.resolve())}
            result = run_one_task(args.db or os.environ.get("ENCLAW_DB_PATH", "~/.local/state/enclaw/tasks.sqlite3"), projects)
            print(json.dumps(result, indent=2) if result else "No queued task was available.")
        return 0
    except (PolicyError, TaskStoreError, RunnerError, OrchestrationError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"EnClaw stopped safely: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
