"""Local-first model provider routing using only the Python standard library."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Any

from .policy import PolicyError


@dataclass(frozen=True)
class Provider:
    provider_id: str
    base_url: str
    model: str
    api_key: str | None
    local: bool
    free: bool


def configured_providers(policy: dict[str, Any], environ: dict[str, str] | None = None) -> list[Provider]:
    env = os.environ if environ is None else environ
    providers: dict[str, Provider] = {}

    ollama_model = env.get("OLLAMA_MODEL", "").strip()
    if ollama_model:
        providers["ollama"] = Provider(
            provider_id="ollama",
            base_url=env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            model=ollama_model,
            api_key=None,
            local=True,
            free=True,
        )

    compatible_model = env.get("OPENAI_COMPAT_MODEL", "").strip()
    compatible_url = env.get("OPENAI_COMPAT_BASE_URL", "").strip().rstrip("/")
    if compatible_model and compatible_url:
        providers["openai-compatible"] = Provider(
            provider_id="openai-compatible",
            base_url=compatible_url,
            model=compatible_model,
            api_key=env.get("OPENAI_COMPAT_API_KEY") or None,
            local=(urlsplit(compatible_url).scheme == "http" and urlsplit(compatible_url).hostname in {"127.0.0.1", "localhost", "::1"}),
            free=env.get("OPENAI_COMPAT_IS_FREE", "false").strip().lower() in {"1", "true", "yes"},
        )

    preference = policy["providers"]["preference"]
    selected = [providers[name] for name in preference if name in providers]
    if policy["providers"]["local_only"]:
        selected = [provider for provider in selected if provider.local]
    if not policy["providers"]["allow_paid_fallback"]:
        selected = [provider for provider in selected if provider.local or provider.free or policy["providers"]["allow_paid_fallback"]]
    return selected


def complete(
    policy: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    environ: dict[str, str] | None = None,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    """Try configured providers in policy order and return provider/model/content metadata.

    The caller is responsible for deciding whether remote endpoints are approved for a
    project. No provider is inferred from a model response, and no paid fallback is
    attempted unless policy explicitly permits it.
    """
    env = os.environ if environ is None else environ
    providers = configured_providers(policy, env)
    if not providers:
        raise PolicyError(
            "No eligible model provider is configured. Set OLLAMA_MODEL or configure "
            "OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_MODEL, then review the project policy."
        )

    failures: list[str] = []
    for provider in providers:
        try:
            if provider.provider_id == "ollama":
                payload = {
                    "model": provider.model,
                    "messages": messages,
                    "stream": False,
                }
                request = urllib.request.Request(
                    provider.base_url + "/api/chat",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = body.get("message", {}).get("content")
            else:
                payload = {"model": provider.model, "messages": messages, "stream": False}
                headers = {"Content-Type": "application/json"}
                if provider.api_key:
                    headers["Authorization"] = f"Bearer {provider.api_key}"
                request = urllib.request.Request(
                    provider.base_url + "/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                choices = body.get("choices") or []
                content = choices[0].get("message", {}).get("content") if choices else None

            if not isinstance(content, str) or not content.strip():
                raise ValueError("Provider returned no text content.")
            return {
                "provider": provider.provider_id,
                "model": provider.model,
                "content": content,
                "local": provider.local,
            }
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError) as exc:
            failures.append(f"{provider.provider_id}: {type(exc).__name__}: {exc}")

    raise RuntimeError("All eligible providers failed: " + " | ".join(failures))
