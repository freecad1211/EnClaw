"""Tests for provider selection policy; no live API calls are made."""

import unittest
from pathlib import Path

from .policy import default_policy
from .providers import configured_providers


class ProviderSelectionTests(unittest.TestCase):
    def setUp(self):
        self.policy = default_policy("sample", str(Path.cwd()))

    def test_local_provider_has_priority_over_free_remote(self):
        providers = configured_providers(
            self.policy,
            {
                "OLLAMA_MODEL": "qwen2.5-coder",
                "OPENAI_COMPAT_BASE_URL": "https://api.example.invalid/v1",
                "OPENAI_COMPAT_MODEL": "free-model",
                "OPENAI_COMPAT_IS_FREE": "true",
            },
        )
        self.assertEqual([p.provider_id for p in providers], ["ollama", "openai-compatible"])

    def test_free_remote_allowed_but_unknown_paid_remote_is_denied_by_default(self):
        env = {
            "OPENAI_COMPAT_BASE_URL": "https://api.example.invalid/v1",
            "OPENAI_COMPAT_MODEL": "model",
            "OPENAI_COMPAT_IS_FREE": "true",
        }
        self.assertEqual([p.provider_id for p in configured_providers(self.policy, env)], ["openai-compatible"])
        env["OPENAI_COMPAT_IS_FREE"] = "false"
        self.assertEqual(configured_providers(self.policy, env), [])
        self.policy["providers"]["allow_paid_fallback"] = True
        self.assertEqual(
            [p.provider_id for p in configured_providers(self.policy, env)],
            ["openai-compatible"],
        )

    def test_local_only_rejects_remote_endpoint(self):
        self.policy["providers"]["local_only"] = True
        providers = configured_providers(
            self.policy,
            {
                "OPENAI_COMPAT_BASE_URL": "https://api.example.invalid/v1",
                "OPENAI_COMPAT_MODEL": "model",
                "OPENAI_COMPAT_IS_FREE": "true",
            },
        )
        self.assertEqual(providers, [])


if __name__ == "__main__":
    unittest.main()
