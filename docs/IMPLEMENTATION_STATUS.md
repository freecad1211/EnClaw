# EnClaw implementation status

## Implemented in the current feature branch

- Standalone Python package under `enclaw_core/`; Python standard library only.
- Per-project JSON policy generation, validation, and inspection with fail-closed capability checks.
- Isolated Git worktree creation for allowlisted task-branch prefixes.
- Guarded task-branch integration: only the configured integration branch is accepted as target.
- Candidate merge in a temporary worktree; validation commands run as argument arrays without a shell; only a passing candidate is fast-forwarded.
- Local-first provider selection helper for Ollama and OpenAI-compatible chat endpoints.
- Explicit opt-in for remote endpoints declared free; paid/unknown remote fallback is disabled by default.
- SQLite task queue with explicit lifecycle transitions, cooperative cancellation requests, and append-only audit events.
- CLI commands to enqueue, list, inspect, cancel, and audit tasks.
- Operator-only Discord slash commands for task queue/list/show/cancel, policy inspection, and starting one worker task.
- Opt-in one-task worker using project-configured coding and distinct reviewer CLIs, with bounded execution time, cancellation checks, changed-path restrictions, automatic commit creation, review gating, configured validation, and integration-branch-only fast-forward merge.
- Unit tests, disposable-repository integration tests, Discord command-registration smoke test, and GitHub Actions CI.

## Not implemented yet

- Multi-agent task decomposition, role scheduling, and parallel worker execution.
- Enforced project-level concurrency limits, retry/resume orchestration, and cost-budget accounting.
- Authenticated review attestations and strong reviewer isolation. The reviewer is a separately configured CLI and must print `APPROVE`, but its identity is not cryptographically verified.
- OS/container sandboxing for agent subprocesses.
- Automatic GitHub pull-request creation and production deployment.

## Important safety limitations

The policy core controls EnClaw's own operations, but it is not an OS sandbox. The worker executes the configured agent CLI and validation commands with the EnClaw process's OS permissions. Use a dedicated unprivileged account, do not configure untrusted commands, and consider container isolation before overnight use. Protected-branch merges and deployment are intentionally excluded.

The provider module supports request/response formats for Ollama `/api/chat` and OpenAI-compatible `/chat/completions`, but this helper is not yet wired directly into the coding worker; the worker uses the configured coding CLI's own model configuration. Endpoint compatibility, model quality, and usage limits must be verified per provider. Concurrency and cost metadata are not yet enforced by a scheduler. Mark a remote endpoint as free with `OPENAI_COMPAT_IS_FREE=true` only after verifying its pricing.

## Local verification

From the repository root:

```bash
python3 -m compileall -q enclaw_core
python3 -m unittest discover -s enclaw_core -t . -v
```
