"""Discord slash-command control plane for EnClaw's durable task queue.

This adapter can create and inspect tasks and start one explicitly configured worker task.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import discord
    from discord import app_commands
except ImportError as exc:  # pragma: no cover - exercised in install environments
    raise SystemExit("Install requirements first: python3 -m pip install -r requirements.txt") from exc

from .policy import PolicyError, load_policy
from .runner import run_one_task, run_worker_pool
from .orchestrator import Orchestrator, OrchestrationError
from .tasks import TaskStore, TaskStoreError


def _database_path() -> str:
    return os.environ.get("ENCLAW_DB_PATH", "~/.local/state/enclaw/tasks.sqlite3")


def _operator_ids() -> set[int]:
    raw = os.environ.get("DISCORD_OPERATOR_IDS", "")
    result: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            try:
                result.add(int(item))
            except ValueError as exc:
                raise RuntimeError("DISCORD_OPERATOR_IDS must be comma-separated numeric Discord user IDs.") from exc
    return result


def _projects_path() -> Path:
    return Path(os.environ.get(
        "ENCLAW_PROJECTS_FILE", "~/.config/enclaw/projects.json"
    )).expanduser().resolve()


def load_projects() -> dict[str, dict[str, Any]]:
    path = _projects_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Cannot read project registry: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Project registry JSON is invalid at line {exc.lineno}.") from exc
    projects = raw.get("projects") if isinstance(raw, dict) else None
    if not isinstance(projects, list):
        raise RuntimeError("Project registry must contain a 'projects' array.")
    result: dict[str, dict[str, Any]] = {}
    for item in projects:
        if not isinstance(item, dict):
            raise RuntimeError("Every project registry entry must be an object.")
        project_id = item.get("id")
        policy_path = item.get("policy_path")
        if not isinstance(project_id, str) or not project_id.strip():
            raise RuntimeError("Each project requires a non-empty id.")
        if project_id in result:
            raise RuntimeError(f"Duplicate project ID in registry: {project_id}")
        if not isinstance(policy_path, str) or not policy_path.strip():
            raise RuntimeError(f"Project {project_id!r} requires policy_path.")
        candidate = Path(policy_path).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        result[project_id] = {"id": project_id, "policy_path": str(candidate.resolve())}
    return result


def _format_task(task: dict[str, Any]) -> str:
    return f"• `{task['task_id']}` **{task['status']}** — {task['project_id']}: {task['title']}"


def build_bot() -> discord.Client:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required.")
    operators = _operator_ids()
    if not operators:
        raise RuntimeError("DISCORD_OPERATOR_IDS must contain at least one allowed Discord user ID.")
    guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
    guild = discord.Object(id=int(guild_id)) if guild_id else None
    intents = discord.Intents.none()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    worker_lock = asyncio.Lock()
    worker_state: dict[str, Any] = {"running": False, "mode": None, "started_at": None, "last_result": None, "last_error": None}

    async def authorized(interaction: discord.Interaction) -> bool:
        if interaction.user.id not in operators:
            await interaction.response.send_message(
                "This EnClaw control command is restricted to configured operators.",
                ephemeral=True,
            )
            return False
        return True

    @client.event
    async def on_ready() -> None:
        if guild is not None:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        else:
            await tree.sync()
        print(f"EnClaw Discord control plane ready as {client.user}", flush=True)

    @tree.command(name="enclaw-status", description="Show recent EnClaw tasks")
    async def enclaw_status(interaction: discord.Interaction) -> None:
        if not await authorized(interaction):
            return
        tasks = TaskStore(_database_path()).list(limit=10)
        if not tasks:
            await interaction.response.send_message("No tasks have been queued.", ephemeral=True)
            return
        await interaction.response.send_message("\n".join(_format_task(task) for task in tasks), ephemeral=True)

    @tree.command(name="project-list", description="List registered projects")
    async def project_list(interaction: discord.Interaction) -> None:
        if not await authorized(interaction):
            return
        try:
            projects = load_projects()
            lines = []
            for project_id, project in sorted(projects.items()):
                try:
                    policy = load_policy(project["policy_path"])
                    target = policy["git"]["integration_branch"]
                    autonomy = policy.get("autonomy", {}).get("level", "policy-defined")
                    lines.append(f"• **{project_id}** — integration: `{target}`, autonomy: `{autonomy}`")
                except (PolicyError, OSError, KeyError, TypeError):
                    lines.append(f"• **{project_id}** — policy unavailable or invalid")
        except (RuntimeError, OSError) as exc:
            await interaction.response.send_message(f"Cannot list projects: {exc}"[:1900], ephemeral=True)
            return
        await interaction.response.send_message("\n".join(lines)[:1900] or "No projects registered.", ephemeral=True)

    @tree.command(name="worker-status", description="Show local worker status")
    async def worker_status(interaction: discord.Interaction) -> None:
        if not await authorized(interaction):
            return
        state = "running" if worker_state["running"] else "idle"
        details = [f"State: **{state}**", f"Mode: {worker_state.get('mode') or '—'}"]
        if worker_state.get("started_at"):
            details.append(f"Started: `{worker_state['started_at']}`")
        if worker_state.get("last_result"):
            details.append(f"Last result: {worker_state['last_result']}")
        if worker_state.get("last_error"):
            details.append(f"Last error: `{str(worker_state['last_error'])[-500:]}`")
        await interaction.response.send_message("\n".join(details), ephemeral=True)

    @tree.command(name="task-log", description="Show recent audit events for a task")
    @app_commands.describe(task_id="Task ID", limit="Number of recent events (1-15)")
    async def task_log(interaction: discord.Interaction, task_id: str, limit: app_commands.Range[int, 1, 15] = 10) -> None:
        if not await authorized(interaction):
            return
        try:
            events = TaskStore(_database_path()).events(task_id)[-limit:]
        except TaskStoreError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        lines = []
        for event in events:
            details = json.dumps(event["details"], ensure_ascii=False, sort_keys=True)
            if len(details) > 180:
                details = details[:177] + "..."
            lines.append(f"`{event['at']}` **{event['event_type']}** — {event['actor']}\n{details}")
        await interaction.response.send_message("\n".join(lines)[:1900] or "No events found.", ephemeral=True)

    @tree.command(name="task-create", description="Queue a task for a registered project")
    @app_commands.describe(project_id="Project ID from the EnClaw project registry", title="Short task title", description="What should be done")
    async def task_create(
        interaction: discord.Interaction,
        project_id: str,
        title: str,
        description: str,
    ) -> None:
        if not await authorized(interaction):
            return
        try:
            projects = load_projects()
            if project_id not in projects:
                raise TaskStoreError(f"Unknown project ID: {project_id}")
            task = TaskStore(_database_path()).create(
                project_id, title, description, actor=f"discord:{interaction.user.id}"
            )
        except (RuntimeError, TaskStoreError) as exc:
            await interaction.response.send_message(f"Task not created: {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Task queued: `{task['task_id']}` — {task['title']}\n"
            "The task is queued; use /worker-once to process one queued task. No worker starts automatically.",
            ephemeral=True,
        )

    @tree.command(name="task-list", description="List EnClaw tasks")
    @app_commands.describe(project_id="Optional project ID filter")
    async def task_list(interaction: discord.Interaction, project_id: str = "") -> None:
        if not await authorized(interaction):
            return
        try:
            tasks = TaskStore(_database_path()).list(project_id=project_id.strip() or None, limit=15)
        except TaskStoreError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        message = "\n".join(_format_task(task) for task in tasks) or "No tasks found."
        await interaction.response.send_message(message[:1900], ephemeral=True)

    @tree.command(name="task-show", description="Show a task and its recent audit events")
    async def task_show(interaction: discord.Interaction, task_id: str) -> None:
        if not await authorized(interaction):
            return
        try:
            store = TaskStore(_database_path())
            task = store.get(task_id)
            events = store.events(task_id)[-8:]
        except TaskStoreError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        event_lines = [f"`{event['at']}` {event['event_type']} ({event['actor']})" for event in events]
        message = f"**{task['title']}**\nID: `{task_id}`\nProject: {task['project_id']}\nStatus: {task['status']}\n" + "\n".join(event_lines)
        await interaction.response.send_message(message[:1900], ephemeral=True)

    @tree.command(name="task-retry", description="Retry a failed task within its project retry budget")
    async def task_retry(interaction: discord.Interaction, task_id: str) -> None:
        if not await authorized(interaction):
            return
        try:
            store = TaskStore(_database_path())
            task = store.get(task_id)
            project = load_projects().get(task["project_id"])
            if project is None:
                raise TaskStoreError(f"Unknown project ID: {task['project_id']}")
            policy = load_policy(project["policy_path"])
            retried = store.retry_failed(
                task_id,
                max_retries=policy["limits"]["max_retries"],
                actor=f"discord:{interaction.user.id}",
            )
        except (PolicyError, TaskStoreError, RuntimeError, OSError) as exc:
            await interaction.response.send_message(f"Retry refused: {exc}"[:1900], ephemeral=True)
            return
        await interaction.response.send_message(
            f"Task {retried['task_id']} requeued (retry {retried['attempts']}/{policy['limits']['max_retries']}).",
            ephemeral=True,
        )

    @tree.command(name="task-cancel", description="Request cancellation of a queued or active task")
    async def task_cancel(interaction: discord.Interaction, task_id: str) -> None:
        if not await authorized(interaction):
            return
        try:
            task = TaskStore(_database_path()).request_cancel(
                task_id, actor=f"discord:{interaction.user.id}"
            )
        except TaskStoreError as exc:
            await interaction.response.send_message(f"Cancellation not requested: {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Cancellation requested for `{task_id}`. A worker must observe this flag to stop.",
            ephemeral=True,
        )

    @tree.command(name="worker-once", description="Start one queued task using the configured agent and reviewer")
    async def worker_once(interaction: discord.Interaction) -> None:
        if not await authorized(interaction):
            return
        if worker_lock.locked():
            await interaction.response.send_message("A worker is already running in this bot process.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Worker started. Check task status with /enclaw-status or /task-show. "
            "The worker uses only the project policy's configured commands.",
            ephemeral=True,
        )

        async def background_worker() -> None:
            async with worker_lock:
                worker_state.update({"running": True, "mode": "worker-once", "started_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"), "last_error": None})
                await interaction.followup.send("Worker execution started; task transitions will be recorded in the audit log.", ephemeral=True)
                try:
                    result = await asyncio.to_thread(
                        run_one_task,
                        _database_path(),
                        load_projects(),
                        actor=f"discord-worker:{interaction.user.id}",
                    )
                    worker_state["last_result"] = str(result)[:500] if result is not None else "no queued task processed"
                    await interaction.followup.send(f"Worker finished: {worker_state['last_result']}"[:1800], ephemeral=True)
                except Exception as exc:
                    worker_state["last_error"] = f"{type(exc).__name__}: {exc}"
                    print(f"EnClaw worker stopped safely: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                    await interaction.followup.send(f"Worker failed: {type(exc).__name__}: {str(exc)[-700:]}"[:1800], ephemeral=True)
                finally:
                    worker_state["running"] = False
                    worker_state["mode"] = None

        asyncio.create_task(background_worker())

    @tree.command(name="worker-pool", description="Run a bounded pool of workers under project concurrency limits")
    @app_commands.describe(workers="Maximum local worker threads (1-16)")
    async def worker_pool(interaction: discord.Interaction, workers: app_commands.Range[int, 1, 16] = 4) -> None:
        if not await authorized(interaction):
            return
        if worker_lock.locked():
            await interaction.response.send_message("A worker or pool is already running in this bot process.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def background_pool() -> None:
            async with worker_lock:
                worker_state.update({"running": True, "mode": f"worker-pool ({workers})", "started_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"), "last_error": None})
                try:
                    results = await asyncio.to_thread(
                        run_worker_pool,
                        _database_path(),
                        load_projects(),
                        workers=workers,
                        actor=f"discord-pool:{interaction.user.id}",
                    )
                    completed = sum(1 for item in results if item.get("status") == "completed")
                    failed = sum(1 for item in results if item.get("status") == "failed")
                    worker_state["last_result"] = f"completed={completed}, failed={failed}, total={len(results)}"
                    await interaction.followup.send(
                        f"Worker pool finished. Completed: {completed}; failed: {failed}; results: {len(results)}.",
                        ephemeral=True,
                    )
                except Exception as exc:
                    worker_state["last_error"] = f"{type(exc).__name__}: {exc}"
                    await interaction.followup.send(
                        f"Worker pool stopped safely: {type(exc).__name__}: {str(exc)[-700:]}"[:1900],
                        ephemeral=True,
                    )
                finally:
                    worker_state["running"] = False
                    worker_state["mode"] = None

        asyncio.create_task(background_pool())
        await interaction.followup.send(
            f"Started a pool with {workers} local worker threads. Project policy controls actual per-project concurrency.",
            ephemeral=True,
        )

    @tree.command(name="orchestrate-once", description="Decompose one queued task into bounded child tasks")
    @app_commands.describe(max_children="Maximum child tasks to allow (1-16)")
    async def orchestrate_once(interaction: discord.Interaction, max_children: app_commands.Range[int, 1, 16] = 8) -> None:
        if not await authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await asyncio.to_thread(
                Orchestrator(_database_path()).decompose_one,
                load_projects(),
                max_children=max_children,
                actor=f"discord-planner:{interaction.user.id}",
            )
        except (OrchestrationError, PolicyError, TaskStoreError, RuntimeError, OSError) as exc:
            await interaction.followup.send(f"Planning stopped safely: {exc}"[:1900], ephemeral=True)
            return
        if result is None:
            await interaction.followup.send("No root task is waiting to be planned.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Parent {result['parent']} planned into {len(result['children'])} child tasks: "
            + ", ".join(result["children"]),
            ephemeral=True,
        )

    @tree.command(name="orchestrate-refresh", description="Update parent task status from child task results")
    async def orchestrate_refresh(interaction: discord.Interaction) -> None:
        if not await authorized(interaction):
            return
        try:
            results = await asyncio.to_thread(
                Orchestrator(_database_path()).refresh_groups,
                actor=f"discord-orchestrator:{interaction.user.id}",
            )
        except (OrchestrationError, PolicyError, TaskStoreError, RuntimeError, OSError) as exc:
            await interaction.response.send_message(f"Aggregation stopped safely: {exc}"[:1900], ephemeral=True)
            return
        if not results:
            await interaction.response.send_message("No parent task groups are ready to aggregate.", ephemeral=True)
            return
        summary = "\n".join(f"{item['parent_task_id']} -> {item['state']} ({len(item['children'])} children)" for item in results)
        await interaction.response.send_message(summary[:1900], ephemeral=True)

    @tree.command(name="project-policy", description="Show a project's policy summary")
    async def project_policy(interaction: discord.Interaction, project_id: str) -> None:
        if not await authorized(interaction):
            return
        try:
            project = load_projects().get(project_id)
            if project is None:
                raise TaskStoreError(f"Unknown project ID: {project_id}")
            policy = load_policy(project["policy_path"])
        except (RuntimeError, TaskStoreError, PolicyError) as exc:
            await interaction.response.send_message(f"Cannot load policy: {exc}", ephemeral=True)
            return
        permissions = policy["permissions"]
        enabled = ", ".join(sorted(name for name, value in permissions.items() if value)) or "none"
        text = (
            f"**{project_id} policy**\n"
            f"Base: `{policy['git']['base_branch']}`\n"
            f"Integration target: `{policy['git']['integration_branch']}`\n"
            f"Protected branches: {', '.join(policy['git']['protected_branches'])}\n"
            f"Enabled capabilities: {enabled}\n"
            f"Provider order: {', '.join(policy['providers']['preference'])}"
        )
        await interaction.response.send_message(text[:1900], ephemeral=True)

    return client


def main() -> int:
    try:
        build_bot().run(os.environ["DISCORD_BOT_TOKEN"])
        return 0
    except (RuntimeError, KeyError, ValueError) as exc:
        print(f"EnClaw Discord adapter stopped safely: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
