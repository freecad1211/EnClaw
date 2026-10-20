# EnClaw implementation status

## Implemented in the current feature branch

- Standalone Python package under `enclaw_core/`; Python standard library only.
- Per-project JSON policy generation, validation, and inspection.
- Fail-closed capability checks.
- Isolated Git worktree creation for allowlisted task-branch prefixes.
- Guarded task-branch integration: only the configured integration branch is accepted as target.
- Candidate merge in a temporary worktree, validation commands executed as argument arrays without a shell, then fast-forward-only integration.
- Local-first provider selection for Ollama and OpenAI-compatible chat endpoints.
- Explicit opt-in for remote endpoints declared free; paid/unknown remote fallback is disabled by default.
- SQLite task queue with explicit lifecycle transitions, cooperative cancellation requests, and append-only audit events.
- CLI commands to enqueue, list, inspect, cancel, and audit tasks.
- Unit and disposable-repository integration tests.
- Operator-only Discord slash commands for task queue/list/show/cancel, policy inspection, and starting one worker task.\n- Opt-in one-task worker using project-configured coding and distinct reviewer CLIs, with bounded execution time, cancellation checks, changed-path restrictions, automatic commit creation, review gating, configured validation, and integration-branch-only fast-forward merge.\n- GitHub Actions workflow to run the core tests.

## Not implemented yet

- Operator-only Discord slash commands support task queue/list/show/cancel, project-policy inspection, and starting one worker task.
- Interactive approval workflow with verified review identity.
- Multi-agent orchestration, role scheduling, parallel worker limits, and task resumption/retry orchestration.
- One-task worker using project-configured coding and reviewer CLIs; worker is opt-in and runs one task at a time per process. Multi-agent scheduling is not implemented.
- Container/sandbox enforcement for arbitrary agent tools.
- Robust reviewer isolation and authenticated review attestations. The current reviewer is a separately configured CLI and must print `APPROVE`, but its identity is not cryptographically verified.
- GitHub pull request workflow.
- Production deployment.

## Important safety limitations

The policy core controls EnClaw's own operations, but it is not an OS sandbox. Validation commands run with the permissions of the EnClaw process. Run it as an unprivileged dedicated user and do not configure untrusted commands. Protected-branch merges and deployment are intentionally not part of this milestone.

The provider module supports request/response formats for Ollama `/api/chat` and OpenAI-compatible `/chat/completions`, but this helper is not yet wired directly into the coding worker; the worker uses the configured coding CLI's own model configuration. Endpoint compatibility, model quality, and usage limits still need to be verified per provider. Concurrency and cost metadata are not yet enforced by a scheduler. A user must explicitly mark a remote endpoint as free with `OPENAI_COMPAT_IS_FREE=true`; do not use this setting unless the endpoint's pricing is known.

## Local verification

From the repository root:

```bash
python3 -m compileall -q enclaw_core
python3 -m unittest discover -s enclaw_core -t . -v
```
