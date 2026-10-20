# EnClaw Project Policy Engine

## Purpose

EnClaw is a general-purpose, project-aware multi-agent development orchestrator built on the existing EJClaw/NanoClaw carry layer. Autonomy is configured per project and per capability rather than selected from one global autonomy level.

This document is the initial design contract. It does not claim that the features below are already implemented.

## Design principles

1. Preserve the upstream NanoClaw/EJClaw integration where possible; keep EnClaw-specific orchestration modular.
2. Discord is the initial operator interface, not the source of authorization truth.
3. Prefer free API providers and local inference when available. Provider adapters must expose capability, context-window, and cost metadata; never assume every provider supports every feature.
4. Every project has an explicit policy. Unknown or missing permissions deny the action.
5. Agents work in isolated Git worktrees and task branches. Never let an agent write directly to a protected branch or the shared checkout.
6. Automatic merging is allowed only within explicitly configured integration branches and only after required checks pass.
7. Destructive, secret-related, deployment, permission, and external-side-effect operations are denied by default or require an explicit human approval.
8. Every action is auditable, bounded by time/retry/token or cost limits, and cancellable.

## Target architecture

- **Discord adapter**: receives commands, posts progress, approval requests, and final reports.
- **Orchestrator**: validates requests, plans tasks, schedules agents, enforces concurrency and cancellation.
- **Project registry**: stores project root, repository, base branch, integration branch, provider preferences, checks, and policy.
- **Agent runner**: launches role-specific agents with scoped instructions and isolated worktrees.
- **Provider registry**: selects free API or local models by availability, capability, privacy, and configured limits.
- **Git coordinator**: creates and cleans worktrees, records commits, detects conflicts, and performs policy-checked merges.
- **Validation pipeline**: runs project-configured tests, lint, type checks, build, security checks, and independent review.
- **Policy engine**: authorizes each capability at the point of execution; Discord commands and model output cannot override policy.
- **Audit/event store**: records task transitions, tool calls, approvals, commits, validation results, merge decisions, and stop reasons.

## Per-project policy model

Each project should declare, at minimum:

- project_id, repo_url, working_root
- base_branch and integration_branch
- allowed branch prefixes and protected branches
- capability permissions for file edits, shell commands, dependency changes, commits, branch-to-branch merges, protected-branch merges, deployment, and destructive operations
- required validation commands and independent review requirements
- provider preference order and local-model fallback
- maximum concurrent agents, task duration, retries, token/cost budget
- approval requirements and stop conditions

Policy must be validated against a schema at load time. Invalid policy means no execution.

## Git isolation and merge rules

1. Each task gets a unique task branch and separate Git worktree.
2. Agents cannot target protected branches directly.
3. Task branches may be integrated only into an explicitly allowlisted integration branch.
4. Before integration, verify repository identity, branch ancestry, clean worktree, allowed changed paths, and task ownership.
5. Run configured checks and independent review on the candidate integration result.
6. On conflict, failed checks, policy mismatch, or uncertain repository state, stop and report; do not attempt an unbounded repair loop.
7. Protected-branch merging is a separate permission from task-branch integration and is disabled by default.
8. Deployment is separate from merging and disabled by default.

## Default-deny actions

The initial defaults should block or require human approval for:

- deleting or force-pushing branches, rewriting shared history, or modifying protected-branch rules
- deleting files or directories outside the task's approved scope
- changing secrets, credentials, environment files, SSH keys, or access tokens
- installing system packages or modifying system services
- database migrations against non-test data, production writes, releases, and deployments
- commands that access unrelated repositories or paths outside the configured project root
- actions whose impact or target cannot be determined confidently

## Initial implementation milestones

1. Add a validated project-policy schema and safe defaults.
2. Add a read-only policy inspection/validation command.
3. Add task/worktree lifecycle management with cleanup tests.
4. Add a merge gate that can only target configured integration branches.
5. Add provider capability discovery and local/free provider selection.
6. Add Discord task controls, approvals, stop/resume, and summaries.
7. Add end-to-end tests using a disposable local Git repository before enabling real project automation.

## Non-goals for the first iteration

- Automatically deploying to production.
- Giving every agent unrestricted host execution.
- Assuming free API availability or unlimited quotas.
- Allowing a model-generated plan to grant itself permissions.
- Merging into protected branches merely because tests passed.
