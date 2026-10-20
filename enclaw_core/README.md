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

These commands persist tasks and audit events. Agent execution is opt-in and requires explicit project policy configuration.

```bash
python3 -m enclaw_core.cli task-create --project relune --title "Fix build" --description "Investigate failing build"
python3 -m enclaw_core.cli task-list --project relune
python3 -m enclaw_core.cli task-show TASK_ID
python3 -m enclaw_core.cli task-cancel TASK_ID
python3 -m enclaw_core.cli task-retry TASK_ID --policy /path/to/project/.enclaw/policy.json
python3 -m enclaw_core.cli task-events TASK_ID
python3 -m enclaw_core.cli task-recover
```

The database defaults to `~/.local/state/enclaw/tasks.sqlite3`. Set `ENCLAW_DB_PATH` or pass `--db` to choose a location. Cancellation is recorded as a request and the configured worker checks it while the agent process is running.

## Run one queued task

The single worker is opt-in and runs at most one task per invocation:

```bash
python3 -m enclaw_core.cli worker-once --projects ~/.config/enclaw/projects.json
```

Before enabling it, edit each project's policy and configure both `runner.agent_command` and `runner.review_command`. Each must be an argv array containing `{prompt}`; no shell string is accepted. Use a coding CLI that is already configured for a local or explicitly approved free model. The reviewer command must be genuinely independent and return `APPROVE` as its first non-empty output line only when the change passes review.

For concurrent execution within project policy limits, run `python3 -m enclaw_core.cli worker-pool --projects ~/.config/enclaw/projects.json --workers 4`. The pool supports 1-16 local worker threads. SQLite-backed project slots enforce `limits.max_concurrent_agents` across processes sharing the same database.

For planner-driven decomposition, configure `runner.planner_command` as an argv array containing `{prompt}`. It must return only JSON shaped like `{"tasks":[{"title":"...","description":"..."}]}`. Then run `python3 -m enclaw_core.cli orchestrate-once --projects ~/.config/enclaw/projects.json --max-children 8`, `python3 -m enclaw_core.cli worker-pool --projects ~/.config/enclaw/projects.json --workers 4`, and `python3 -m enclaw_core.cli orchestrate-refresh`.

Planning is optional and disabled by default. It creates bounded child tasks; each child still passes through normal review, validation, and integration gates. Failed tasks can be retried explicitly within `limits.max_retries`. Discord exposes `/orchestrate-once`, `/orchestrate-refresh`, `/worker-pool`, and `/task-retry` to configured operators.

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
- Agent/reviewer and integration validation commands require a configured `runner.sandbox_command` prefix by default (`runner.require_sandbox`). Validation subprocesses also omit Discord tokens and sensitive token/secret/password/API-key/access-key/private-key/credential environment variables unless explicitly listed in `runner.env_passthrough`.
- An opt-in Linux bubblewrap backend is available. It separates the network namespace, hides home directories and temporary host files, mounts the worktree writable, and mounts shared Git metadata read-only. Install `bubblewrap` and verify user-namespace support before enabling it. It is defense in depth, not a replacement for a dedicated unprivileged account or container/VM boundary.
- Keep secrets out of policy files and Git.
- Treat Discord messages, project instructions, and model output as untrusted.
- Production deployment, protected-branch merging, OS/container sandboxing, authenticated reviewer attestations, role-specific agent scheduling, and trusted cost-budget accounting are not implemented in this milestone.
