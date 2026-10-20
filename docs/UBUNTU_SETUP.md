# Ubuntu setup guide (core preview)

This guide installs only the EnClaw Python core in a user-owned checkout. It does not install or change system services, Docker, OpenCode, Forge, or NanoClaw.

## 1. Clone the feature branch

```bash
git clone --branch feature/project-policy-engine https://github.com/freecad1211/EnClaw.git
cd EnClaw
python3 --version
git --version
```

Use Python 3.11 or newer and Git 2.30 or newer.

## 2. Verify the core

```bash
python3 -m compileall -q enclaw_core
python3 -m unittest discover -s enclaw_core -t . -v
```

The Git tests create disposable repositories under a temporary directory. They do not use your real project.

## 3. Configure a project

Run this from the EnClaw checkout, replacing the path and branch names:

```bash
python3 -m enclaw_core.cli init-project /path/to/project --id my-project --base develop --integration enclaw/integration
python3 -m enclaw_core.cli validate /path/to/project/.enclaw/policy.json
```

The policy file is created inside the project and the default task worktrees are created outside it. Inspect the generated policy before running any work.

Create the integration branch and a dedicated worktree explicitly:

```bash
git -C /path/to/project branch enclaw/integration develop
git -C /path/to/project worktree add /path/to/project-integration enclaw/integration
```

If the branch already exists, do not repeat the branch command. If the project has uncommitted changes, commit or safely stash them before using the integration workflow.

## 4. Configure local-first model selection

For Ollama, set `OLLAMA_BASE_URL` and `OLLAMA_MODEL` in the process environment. The Ollama model must already be available locally. The core does not install Ollama or download models automatically.

For a remote OpenAI-compatible endpoint, set `OPENAI_COMPAT_BASE_URL` and `OPENAI_COMPAT_MODEL`. Supply an API key only through a private environment file or secret manager, never in Git. Set `OPENAI_COMPAT_IS_FREE=true` only after verifying the provider's pricing. Otherwise, remote use is denied unless the project's policy explicitly enables paid fallback.

## Durable task queue smoke test

```bash
python3 -m enclaw_core.cli task-create --project smoke-test --title "Queue smoke test" --description "Verify task persistence"
python3 -m enclaw_core.cli task-list --project smoke-test
```

This only writes a local SQLite database. It does not launch an agent.

## Current scope

This branch does not yet run agents or connect a Discord bot. It provides policy validation, provider selection, isolated worktrees, and a guarded task-branch integration path. Do not treat it as the finished autonomous platform until the Discord adapter, task runner, durable audit log, and verified review flow have been implemented and tested.
