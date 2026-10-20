# EnClaw implementation status

## Implemented in the current feature branch

- Standalone, dependency-free Python policy core.
- Per-project JSON policy generation, validation, and inspection.
- Fail-closed capability checks.
- Isolated Git worktree creation for allowlisted task-branch prefixes.
- Guarded task-branch integration: only the configured integration branch is accepted as target.
- Candidate merge in a temporary worktree, validation commands executed as argument arrays without a shell, then fast-forward-only integration.
- Unit and disposable-repository integration tests.

## Not implemented yet

- Discord bot commands and interactive approvals.
- Actual multi-agent orchestration and role scheduling.
- Ollama and free API provider adapters.
- Durable task queue, database-backed audit history, cancellation/resume.
- Container/sandbox enforcement for arbitrary agent tools.
- Independent model-based code review implementation.
- GitHub pull request workflow and CI verification.
- Production deployment.

## Important safety limitation

The core policy controls EnClaw's own operations, but it is not an OS sandbox. Validation commands run with the permissions of the EnClaw process. Run it as an unprivileged dedicated user and do not configure untrusted commands. Production deployment and protected-branch merges are intentionally outside this first core milestone.

## Local verification

From the repository root:

```bash
python3 -m unittest enclaw-core.test_policy enclaw-core.test_git_workspace
```

Because Python package names cannot contain hyphens, the implementation directory is renamed to `enclaw_core` before release. Until then, run tests from the directory directly:

```bash
cd enclaw-core
python3 -m unittest test_policy test_git_workspace
```
