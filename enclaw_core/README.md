# EnClaw Core (preview)

A dependency-free Python core for project-specific permissions, model provider selection, durable task metadata, and isolated Git workspaces. This is an executable foundation, not yet a complete autonomous multi-agent runtime.

## Requirements

- Python 3.11+
- Git 2.30+
- A local Git repository for each registered project

No third-party Python packages are required for the core.

## Verify

```bash
python3 -m compileall -q enclaw_core
python3 -m unittest discover -s enclaw_core -t . -v
```

## Create a project policy

```bash
python3 -m enclaw_core.cli init-project /path/to/project --id relune --base develop --integration enclaw/integration
python3 -m enclaw_core.cli validate /path/to/project/.enclaw/policy.json
python3 -m enclaw_core.cli show-policy /path/to/project/.enclaw/policy.json
```

The initializer writes configuration only. It does not start agents, install dependencies, modify Git remotes, or change system services.

Create the integration branch from the intended baseline and check it out in a dedicated worktree:

```bash
git -C /path/to/project branch enclaw/integration develop
git -C /path/to/project worktree add /path/to/project-integration enclaw/integration
```

If the integration branch already exists, do not repeat the branch command. Keep the primary checkout and integration worktree clean.

## Isolated task worktree and guarded integration

```bash
python3 -m enclaw_core.cli create-worktree /path/to/project/.enclaw/policy.json task-123 --branch enclaw/task/task-123
```

After implementation and review, request integration:

```bash
python3 -m enclaw_core.cli integrate /path/to/project/.enclaw/policy.json \
  --source enclaw/task/task-123 \
  --target enclaw/integration \
  --review-approved
```

The target must be exactly the configured integration branch; protected branches are always refused by this core. A candidate merge is created in a temporary worktree, configured checks run as argument arrays without a shell, and only a passing candidate is fast-forwarded into the integration worktree. The primary checkout and integration worktree must be clean.

Important: `--review-approved` is currently an operator assertion, not a verified reviewer identity. Do not wire it directly to untrusted model output.

## Durable task queue

These commands persist tasks and audit events; they do not execute agents yet.

```bash
python3 -m enclaw_core.cli task-create --project relune --title "Fix build" --description "Investigate failing build"
python3 -m enclaw_core.cli task-list --project relune
python3 -m enclaw_core.cli task-show TASK_ID
python3 -m enclaw_core.cli task-cancel TASK_ID
python3 -m enclaw_core.cli task-events TASK_ID
```

The database defaults to `~/.local/state/enclaw/tasks.sqlite3`. Set `ENCLAW_DB_PATH` or pass `--db` to choose a location. Cancellation is recorded as a request and the configured worker checks it while the agent process is running.

## Run one queued task

The worker is opt-in and runs at most one task per invocation:

```bash
python3 -m enclaw_core.cli worker-once --projects ~/.config/enclaw/projects.json
```

Before enabling it, edit each project's policy and configure both `runner.agent_command` and `runner.review_command`. Each must be an argv array containing `{prompt}`; no shell string is accepted. Use a coding CLI that is already configured for a local or explicitly approved free model. The reviewer command must be genuinely independent and return `APPROVE` as its first non-empty output line only when the change passes review.

The Discord `/worker-once` command starts the same one-task worker in the bot process. Only configured operator IDs can use it. The bot allows one worker at a time per process.

## Local-first provider selection

The core supports Ollama's `/api/chat` and OpenAI-compatible `/chat/completions` formats.

- `OLLAMA_BASE_URL` defaults to `http://127.0.0.1:11434`.
- Set `OLLAMA_MODEL` to a locally installed model.
- For a remote OpenAI-compatible endpoint, set `OPENAI_COMPAT_BASE_URL` and `OPENAI_COMPAT_MODEL`.
- Supply `OPENAI_COMPAT_API_KEY` only through a private environment or secret manager.
- Set `OPENAI_COMPAT_IS_FREE=true` only after verifying that the endpoint is genuinely free.
- Paid or unknown remote endpoints are excluded unless the project explicitly enables paid fallback.

Provider endpoint compatibility and quota behavior must be checked individually.

## Security limitations

- Run EnClaw as a dedicated unprivileged OS user.
- The core is not an OS sandbox. Validation commands run with the EnClaw process's permissions.
- Keep secrets out of policy files and Git.
- Treat Discord messages, project instructions, and model output as untrusted.
- Production deployment, protected-branch merging, Discord commands, agent scheduling, and verified independent review are not implemented in this milestone.
