"""Tests for fail-closed Bubblewrap error diagnostics."""
import io
import unittest
from contextlib import redirect_stderr

from enclaw_core.sandbox import _write_bwrap_failure


class BubblewrapFailureDiagnosticsTests(unittest.TestCase):
    def test_uid_map_permission_error_mentions_apparmor_and_fail_closed(self):
        output = io.StringIO()
        with redirect_stderr(output):
            _write_bwrap_failure("bwrap: setting up uid map: Permission denied")
        message = output.getvalue()
        self.assertIn("AppArmor", message)
        self.assertIn("journalctl -k", message)
        self.assertIn("will not retry without isolation", message)

    def test_uid_map_audit_style_error_is_recognized(self):
        output = io.StringIO()
        with redirect_stderr(output):
            _write_bwrap_failure("open proc/123/uid_map: denied")
        self.assertIn("user namespace", output.getvalue())

    def test_unrelated_error_does_not_claim_apparmor_is_the_cause(self):
        output = io.StringIO()
        with redirect_stderr(output):
            _write_bwrap_failure("bwrap: invalid option")
        message = output.getvalue()
        self.assertIn("invalid option", message)
        self.assertNotIn("AppArmor", message)


if __name__ == "__main__":
    unittest.main()
