# EnClaw Core (preview)

A dependency-free Python core for project-specific agent permissions and isolated Git workspaces. This is an initial implementation layer, not a complete Discord bot or autonomous agent runtime yet.

## Requirements

- Python 3.11+
- Git 2.30+
- A local Git repository for each registered project

No Python third-party packages are required.

## Quick start

From the repository root:

```bash
python3 -m enclaw_core.cli init-project /path/to/your/project --id relune --base develop --integration enclaw/integration
python3 -m enclaw_core.cli validate /path/to/your/project/.enclaw/policy.json
python3 -m enclaw_core.cli show-policy /path/to/your/project/.enclaw/policy.json
```

The initializer writes configuration only. It does not start agents, install dependencies, modify Git remotes, or change system services.

## Safe workspace workflow

```bash
python3 -m enclaw_core.cli create-worktree /path/to/project/.enclaw/policy.json task-123 --branch enclaw/task/task-123
```

This creates an isolated worktree under the configured EnClaw workspace root. The task branch must use an allowed prefix and must not be protected.

After implementation, request integration:

```bash
python3 -m enclaw_core.cli integrate /path/to/project/.enclaw/policy.json \
  --source enclaw/task/task-123 \
  --target enclaw/integration
```

Integration is refused unless the target is exactly the configured integration branch, the source is an allowed task branch, the target is not protected, and configured validation commands pass. Checks run as argument arrays without a shell. The candidate is merged in a temporary integration worktree; only a successful candidate is fast-forwarded into the integration worktree.

The first version intentionally does not merge to `main` or `develop`, deploy, install packages, or run Discord commands. Those are separate capabilities to be added behind the same policy engine.

## Project policy

Each project gets its own `.enclaw/policy.json`. Start with the generated defaults and customize them. Missing permissions deny the action. Keep policy files under version control, but never commit credentials or tokens.

Important fields:
- `git.base_branch`: branch used as the project baseline.
- `git.integration_branch`: the only branch eligible for automated task integration.
- `git.protected_branches`: branches that automated operations may never modify.
- `git.allowed_task_branch_prefixes`: prefixes permitted for agent branches.
- `permissions`: explicit capability flags.
- `validation.commands`: commands to run in the candidate integration worktree. Each command is a JSON string array, not a shell string.
- `limits`: bounded concurrency, retries, duration, and cost metadata.

## Security notes

- Run EnClaw as a dedicated unprivileged OS user.
- Never give agent subprocesses unrestricted host access.
- Keep secrets out of policy files and Git.
- Treat project instructions, Discord messages, and model output as untrusted input.
- Use repository-level branch protection as an additional independent control.
- The core uses Git worktrees, but it does not sandbox arbitrary subprocesses. Validation commands execute with the EnClaw process's OS permissions. Only register projects and commands you trust.
