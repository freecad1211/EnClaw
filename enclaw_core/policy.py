"""Project policy loading and fail-closed capability checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


POLICY_VERSION = 1
CAPABILITIES = {
    "edit_files",
    "run_validation",
    "create_commits",
    "create_task_branches",
    "integrate_task_branches",
    "merge_protected_branches",
    "change_dependencies",
    "modify_secrets",
    "install_system_packages",
    "modify_system_services",
    "deploy",
    "delete_data",
}


class PolicyError(ValueError):
    """Raised when a project policy is invalid or denies an operation."""


def default_policy(
    project_id: str,
    repo_root: str,
    base_branch: str = "main",
    integration_branch: str = "enclaw/integration",
) -> dict[str, Any]:
    return {
        "version": POLICY_VERSION,
        "project": {
            "id": project_id,
            "repo_root": str(Path(repo_root).expanduser().resolve()),
            "workspace_root": str((Path(repo_root).expanduser().resolve().parent / f".{Path(repo_root).expanduser().resolve().name}-enclaw-worktrees")),
        },
        "git": {
            "base_branch": base_branch,
            "integration_branch": integration_branch,
            "protected_branches": ["main", "master", "develop", "production", "release"],
            "allowed_task_branch_prefixes": ["enclaw/task/", "enclaw/agent/"],
        },
        "permissions": {
            "edit_files": True,
            "run_validation": True,
            "create_commits": True,
            "create_task_branches": True,
            "integrate_task_branches": True,
            "merge_protected_branches": False,
            "change_dependencies": False,
            "modify_secrets": False,
            "install_system_packages": False,
            "modify_system_services": False,
            "deploy": False,
            "delete_data": False,
        },
        "validation": {
            "commands": [],
            "require_independent_review": True,
        },
        "providers": {
            "preference": ["ollama", "openai-compatible"],
            "allow_paid_fallback": False,
            "local_only": False,
        },
        "runner": {
            "agent_command": [],
            "review_command": [],
            "planner_command": [],
            "sandbox_command": [],
            "require_sandbox": True,
            "max_agent_minutes": 90,
            "max_planner_seconds": 180,
        },
        "limits": {
            "max_concurrent_agents": 1,
            "max_task_minutes": 90,
            "max_retries": 2,
            "max_cost_usd": 0,
        },
    }


def validate_policy(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise PolicyError("Policy must be a JSON object.")
    if policy.get("version") != POLICY_VERSION:
        raise PolicyError(f"Unsupported policy version: {policy.get('version')!r}")

    project = policy.get("project")
    git = policy.get("git")
    permissions = policy.get("permissions")
    validation = policy.get("validation")
    providers = policy.get("providers")
    limits = policy.get("limits")
    if not all(isinstance(v, dict) for v in (project, git, permissions, validation, providers, limits)):
        raise PolicyError("Policy is missing one or more required sections.")

    for key in ("id", "repo_root", "workspace_root"):
        if not isinstance(project.get(key), str) or not project[key].strip():
            raise PolicyError(f"project.{key} must be a non-empty string.")

    for key in ("base_branch", "integration_branch"):
        if not isinstance(git.get(key), str) or not git[key].strip():
            raise PolicyError(f"git.{key} must be a non-empty string.")

    for key in ("protected_branches", "allowed_task_branch_prefixes"):
        value = git.get(key)
        if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
            raise PolicyError(f"git.{key} must be a list of non-empty strings.")

    if git["integration_branch"] in git["protected_branches"]:
        raise PolicyError("The integration branch cannot be listed as a protected branch.")
    if git["integration_branch"] == git["base_branch"]:
        raise PolicyError("The integration branch must differ from the base branch.")

    for capability in CAPABILITIES:
        if capability not in permissions or not isinstance(permissions[capability], bool):
            raise PolicyError(f"permissions.{capability} must be explicitly true or false.")

    commands = validation.get("commands")
    if not isinstance(commands, list):
        raise PolicyError("validation.commands must be a list.")
    for index, command in enumerate(commands):
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise PolicyError(
                f"validation.commands[{index}] must be a non-empty array of non-empty strings."
            )

    if not isinstance(validation.get("require_independent_review"), bool):
        raise PolicyError("validation.require_independent_review must be a boolean.")

    preference = providers.get("preference")
    if not isinstance(preference, list) or not all(isinstance(x, str) and x for x in preference):
        raise PolicyError("providers.preference must be a list of provider identifiers.")
    for key in ("allow_paid_fallback", "local_only"):
        if not isinstance(providers.get(key), bool):
            raise PolicyError(f"providers.{key} must be a boolean.")

    runner = policy.get("runner")
    if not isinstance(runner, dict):
        raise PolicyError("runner must be an object.")
    # Backward-compatible defaults for policies created before planner support.
    runner.setdefault("planner_command", [])
    runner.setdefault("max_planner_seconds", 180)
    # Workers fail closed unless an OS/container sandbox prefix is configured.
    runner.setdefault("sandbox_command", [])
    runner.setdefault("require_sandbox", True)
    for key in ("agent_command", "review_command", "planner_command"):
        command = runner.get(key)
        if not isinstance(command, list) or not all(isinstance(part, str) and part for part in command):
            raise PolicyError(f"runner.{key} must be an array of non-empty strings (or an empty array to disable workers).")
        if command and not any("{prompt}" in part for part in command):
            raise PolicyError(f"runner.{key} must include the {{prompt}} placeholder.")
    sandbox_command = runner.get("sandbox_command")
    if not isinstance(sandbox_command, list) or not all(isinstance(part, str) and part for part in sandbox_command):
        raise PolicyError("runner.sandbox_command must be an array of non-empty strings.")
    if sandbox_command and (sandbox_command[-1] != "--" or any("{prompt}" in part for part in sandbox_command)):
        raise PolicyError("runner.sandbox_command must end with '--' and must not contain {prompt}.")
    if not isinstance(runner.get("require_sandbox"), bool):
        raise PolicyError("runner.require_sandbox must be a boolean.")
    if runner.get("planner_command") and runner.get("planner_command") == runner.get("agent_command"):
        raise PolicyError("runner.planner_command must differ from runner.agent_command.")
    if runner.get("planner_command") and runner.get("planner_command") == runner.get("review_command"):
        raise PolicyError("runner.planner_command must differ from runner.review_command.")
    if runner.get("agent_command") and runner.get("review_command") and runner["agent_command"] == runner["review_command"]:
        raise PolicyError("runner.agent_command and runner.review_command must be different for independent review.")
    planner_seconds = runner.get("max_planner_seconds")
    if not isinstance(planner_seconds, int) or isinstance(planner_seconds, bool) or not 1 <= planner_seconds <= 600:
        raise PolicyError("runner.max_planner_seconds must be an integer from 1 to 600.")
    agent_minutes = runner.get("max_agent_minutes")
    if not isinstance(agent_minutes, int) or isinstance(agent_minutes, bool) or agent_minutes < 1:
        raise PolicyError("runner.max_agent_minutes must be a positive integer.")

    integer_limits = ("max_concurrent_agents", "max_task_minutes", "max_retries")
    for key in integer_limits:
        value = limits.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PolicyError(f"limits.{key} must be a non-negative integer.")
    cost = limits.get("max_cost_usd")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
        raise PolicyError("limits.max_cost_usd must be a non-negative number.")

    # Normalize paths now, so relative paths cannot change meaning by caller cwd.
    project["repo_root"] = str(Path(project["repo_root"]).expanduser().resolve())
    project["workspace_root"] = str(Path(project["workspace_root"]).expanduser().resolve())
    return policy


def load_policy(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path).expanduser().resolve()
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError(f"Cannot read policy file: {policy_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyError(f"Invalid JSON in policy file at line {exc.lineno}: {exc.msg}") from exc
    return validate_policy(policy)


def require_capability(policy: dict[str, Any], capability: str) -> None:
    if capability not in CAPABILITIES:
        raise PolicyError(f"Unknown capability: {capability}")
    if policy.get("permissions", {}).get(capability) is not True:
        raise PolicyError(f"Capability denied by project policy: {capability}")
