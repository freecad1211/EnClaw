# Docker sandbox backend (experimental)

EnClaw includes an opt-in Docker launcher for configured agent and reviewer CLIs. It does not install a CLI or build an image for you. The image must already contain the CLI/runtime referenced by `runner.agent_command` and `runner.review_command`.

## Security defaults

The launcher:
- uses `docker run --pull=never`; it never pulls an image automatically;
- defaults to `--network none` and only accepts `none` or explicitly opted-in `bridge`;
- uses a read-only container root filesystem, drops Linux capabilities, enables `no-new-privileges`, and applies PID, memory, and CPU limits;
- runs with the invoking numeric UID/GID;
- mounts only the task worktree read/write and the shared Git metadata directory read-only;
- does not mount the host home directory or Docker socket;
- forwards no host environment variables unless explicitly named with `--env-key`.

This is defense in depth, not a complete hostile-code boundary. The Docker daemon is highly privileged; never expose its socket to the agent container. Use a dedicated unprivileged EnClaw OS account, keep Docker patched, and review image contents. The writable task worktree remains fully controllable by the agent.

## Configure a project

Build or obtain an image containing the exact CLI and runtime needed. The image should have no baked-in credentials. Example policy fragment for a prebuilt image called `enclaw-agent:local`:

```json
{
  "runner": {
    "sandbox_command": [
      "python3", "-m", "enclaw_core.docker_sandbox",
      "--image", "enclaw-agent:local",
      "--worktree", "{worktree}",
      "--repo-root", "{repo_root}",
      "--network", "none",
      "--"
    ],
    "require_sandbox": true
  }
}
```

The existing `agent_command` and `review_command` are appended after the final `--` and execute inside the image. Use container paths that exist in the image, such as `["opencode", "run", "{prompt}"]`, not host-only executable paths. Keep agent and reviewer commands genuinely independent.

### Explicit API access

Network access is disabled by default. If a remote or LAN API is required, the operator must deliberately change `--network none` to `--network bridge`. Bridge mode grants general container network access; it is not an endpoint allowlist. It also adds `host.docker.internal` as a host-gateway name but does not guarantee the host API is listening on an address reachable from Docker.

Only forward credentials that the CLI needs. For example, add `"--env-key", "OPENAI_COMPAT_API_KEY"` and/or `"--env-key", "OPENAI_COMPAT_BASE_URL"` before the final `"--"`. The variable must be present in the worker process environment; sensitive variables also need to be explicitly permitted through `runner.env_passthrough`. Never put API keys in the policy file or image.

Do not enable workers until the exact image, CLI, API connectivity, reviewer behavior, and the full EnClaw test suite have been checked. A Docker smoke test alone is not proof that agent execution is safely configured.
