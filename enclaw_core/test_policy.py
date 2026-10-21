"""Unit tests for project policy validation."""

import unittest
from pathlib import Path

from .policy import PolicyError, default_policy, validate_policy


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = default_policy("sample", str(Path.cwd()), "develop", "enclaw/integration")

    def test_default_policy_is_valid(self):
        normalized = validate_policy(self.policy)
        self.assertEqual(normalized["git"]["integration_branch"], "enclaw/integration")
        self.assertFalse(normalized["permissions"]["deploy"])

    def test_older_policy_gets_safe_planner_defaults(self):
        policy = default_policy("sample", str(Path.cwd()))
        del policy["runner"]["planner_command"]
        del policy["runner"]["max_planner_seconds"]
        normalized = validate_policy(policy)
        self.assertEqual(normalized["runner"]["planner_command"], [])
        self.assertEqual(normalized["runner"]["max_planner_seconds"], 180)

    def test_missing_capability_is_denied(self):
        policy = default_policy("sample", str(Path.cwd()))
        del policy["permissions"]["deploy"]
        with self.assertRaises(PolicyError):
            validate_policy(policy)

    def test_integration_must_differ_from_base(self):
        policy = default_policy("sample", str(Path.cwd()), "main", "main")
        with self.assertRaises(PolicyError):
            validate_policy(policy)

    def test_validation_command_must_be_argv(self):
        policy = default_policy("sample", str(Path.cwd()))
        policy["validation"]["commands"] = ["npm test"]
        with self.assertRaises(PolicyError):
            validate_policy(policy)

    def test_sandbox_network_must_be_stage_specific(self):
        policy = default_policy("sample", str(Path.cwd()))
        policy["runner"]["sandbox_command"] = ["sandbox", "--network", "bridge", "--"]
        with self.assertRaisesRegex(PolicyError, "{network}"):
            validate_policy(policy)

    def test_sandbox_env_keys_must_be_stage_specific(self):
        policy = default_policy("sample", str(Path.cwd()))
        policy["runner"]["sandbox_command"] = ["sandbox", "--env-key", "OPENAI_API_KEY", "--"]
        with self.assertRaisesRegex(PolicyError, "{env_keys}"):
            validate_policy(policy)

    def test_limits_cannot_be_negative(self):
        policy = default_policy("sample", str(Path.cwd()))
        policy["limits"]["max_retries"] = -1
        with self.assertRaises(PolicyError):
            validate_policy(policy)


if __name__ == "__main__":
    unittest.main()
