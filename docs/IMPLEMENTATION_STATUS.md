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
- SQLite task queue with explicit lifecycle transitions, cooperative cancellation requests, and append-only audit events.\n- CLI commands to enqueue, list, inspect, cancel, and audit tasks.\n- Unit and disposable-repository integration tests.
- GitHub Actions workflow to run the core tests.

## Not implemented yet

- Discord bot commands and interactive approvals.
- Actual multi-agent orchestration, role scheduling, and worker cancellation handling.
- Worker execution and resume/retry orchestration; task metadata and event history are persisted, but no worker consumes the queue yet.
- Container/sandbox enforcement for arbitrary agent tools.
- Independent model-based code review implementation. The current review flag is an operator assertion, not a verified reviewer identity.
- GitHub pull request workflow.
- Production deployment.

## Important safety limitations

The policy core controls EnClaw's own operations, but it is not an OS sandbox. Validation commands run with the permissions of the EnClaw process. Run it as an unprivileged dedicated user and do not configure untrusted commands. Protected-branch merges and deployment are intentionally not part of this milestone.

The provider module supports request/response formats for Ollama `/api/chat` and OpenAI-compatible `/chat/completions`. Endpoint compatibility, model quality, and usage limits still need to be verified per provider. A user must explicitly mark a remote endpoint as free with `OPENAI_COMPAT_IS_FREE=true`; do not use this setting unless the endpoint's pricing is known.

## Local verification

From the repository root:

```bash
python3 -m compileall -q enclaw_core
python3 -m unittest discover -s enclaw_core -t . -v
```
