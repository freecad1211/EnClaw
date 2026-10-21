# EnClaw implementation status

## Implemented in the current feature branch

- Standalone Python package under `enclaw_core/`; Python standard library only.
- Per-project JSON policy generation, validation, and inspection with fail-closed capability checks.
- Isolated Git worktree creation for allowlisted task-branch prefixes.
- Guarded task-branch integration: only the configured integration branch is accepted as target.
- Candidate merge in a temporary worktree; validation commands run as argument arrays without a shell, use a scrubbed environment, and are wrapped in the configured sandbox prefix when sandboxing is required; only a passing candidate is fast-forwarded.
- Local-first provider selection helper for Ollama and OpenAI-compatible chat endpoints.
- Explicit opt-in for remote endpoints declared free; paid/unknown remote fallback is disabled by default.
- SQLite task queue with explicit lifecycle transitions, cooperative cancellation requests, append-only audit events, policy-bounded explicit retries, and SQLite-backed project worker slots that enforce concurrency across local worker processes.
- CLI commands to enqueue, list, inspect, cancel, audit, and recover orphaned active tasks after worker-process loss.
- Optional planner-driven task decomposition (`orchestrate-once`) with strict JSON schema, bounded child count/output/time, durable parent-child records, and parent status aggregation (`orchestrate-refresh`). Existing project policies remain compatible; planning is disabled unless `runner.planner_command` is configured.
- Operator-only Discord slash commands for task queue/list/show/cancel/retry, policy inspection, planner decomposition/aggregation, one-task execution, and a bounded worker pool.
- Opt-in one-task worker and bounded local worker pool using project-configured coding and distinct reviewer CLIs, with cross-process project concurrency slots, bounded execution time, cancellation checks, changed-path restrictions, automatic commit creation, review gating, configured validation, and integration-branch-only fast-forward merge.
- Unit tests, disposable-repository integration tests, Discord command-registration smoke test, and GitHub Actions CI.

## Not implemented yet

- Role-specific agent scheduling (planner-created child tasks currently share the same coding/reviewer role configuration).
- Automatic retry/resume orchestration and cost-budget accounting. Failed tasks can be retried explicitly within the project policy retry budget. The `task-recover` command marks active tasks failed when no live worker lease remains; it does not automatically replay them.
- Cryptographically authenticated reviewer identity and reviewer sandbox isolation. Review approval is now bound to the exact source commit SHA; this prevents stale approvals after the branch changes, but the reviewer's identity is still not cryptographically verified.
- A hardened production isolation boundary. An opt-in Linux bubblewrap backend provides network namespace isolation, hides host home/tmp directories, keeps shared Git metadata read-only, and allows writes only to the task worktree. It still requires host support and is defense in depth, not a container/VM or dedicated-user boundary.
- Automatic GitHub pull-request creation and production deployment.

## Important safety limitations

Agent, reviewer, and integration validation commands are fail-closed on sandbox configuration: with the default `runner.require_sandbox: true`, a task fails before any worktree is created unless `runner.sandbox_command` (an argv prefix ending in `--`) is configured, and that prefix is prepended to both commands. Independent review approval is bound to the exact reviewed source commit SHA; if the branch HEAD changes before integration, integration is denied and review must be repeated. Set `require_sandbox` to `false` only for trusted local development. Agent and reviewer subprocesses also run in their own process group (stopped as a group on cancel/timeout) and do not inherit `DISCORD_*` or `*_TOKEN`/`*_SECRET`/`*_PASSWORD`/`*_API_KEY`/`*_ACCESS_KEY`/`*_PRIVATE_KEY`/`*_CREDENTIALS` environment variables unless listed in `runner.env_passthrough`. Validation commands during integration use the configured sandbox prefix and secret-scrubbed environment; if sandboxing is required but no prefix is configured, validation fails closed. The optional built-in Linux backend is configured with `runner.sandbox_command` (see the Ubuntu setup guide); custom prefixes are not independently verified, and bubblewrap must be tested on the target host.

The policy core controls EnClaw's own operations, but it is not itself an OS sandbox. The worker executes the configured agent CLI and validation commands with the EnClaw process's OS permissions. Use a dedicated unprivileged account, do not configure untrusted commands, and consider container isolation before overnight use. Protected-branch merges and deployment are intentionally excluded.

The provider module supports request/response formats for Ollama `/api/chat` and OpenAI-compatible `/chat/completions`, but this helper is not yet wired directly into the coding worker; the worker uses the configured coding CLI's own model configuration. Endpoint compatibility, model quality, and usage limits must be verified per provider. Project concurrency limits are enforced by SQLite worker slots across local processes. Cost budgets are still not enforced because configured external CLIs do not provide a uniform, trusted usage meter. Mark a remote endpoint as free with `OPENAI_COMPAT_IS_FREE=true` only after verifying its pricing.

## Local verification

From the repository root:

```bash
python3 -m compileall -q enclaw_core
python3 -m unittest discover -s enclaw_core -t . -v
```
