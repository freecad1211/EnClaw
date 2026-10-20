"""Tests for durable task lifecycle and audit events."""

import tempfile
import unittest
from pathlib import Path

from .tasks import TaskStore, TaskStoreError


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name) / "state" / "tasks.sqlite3")
        self.task = self.store.create("relune", "Fix build", "Investigate and fix the TypeScript build.")

    def test_created_task_is_queued_and_audited(self):
        self.assertEqual(self.task["status"], "queued")
        events = self.store.events(self.task["task_id"])
        self.assertEqual(events[0]["event_type"], "task.created")

    def test_valid_transition_and_invalid_transition(self):
        updated = self.store.transition(self.task["task_id"], "planning", actor="orchestrator")
        self.assertEqual(updated["status"], "planning")
        with self.assertRaises(TaskStoreError):
            self.store.transition(self.task["task_id"], "completed", actor="orchestrator")

    def test_cancel_request_is_recorded(self):
        updated = self.store.request_cancel(self.task["task_id"])
        self.assertTrue(updated["cancellation_requested"])
        self.assertEqual(self.store.events(self.task["task_id"])[-1]["event_type"], "task.cancel_requested")

    def test_terminal_task_cannot_be_cancelled(self):
        self.store.transition(self.task["task_id"], "cancelled", actor="operator")
        with self.assertRaises(TaskStoreError):
            self.store.request_cancel(self.task["task_id"])

    def test_task_list_can_be_filtered_by_project(self):
        self.store.create("other", "Other task", "Separate project")
        self.assertEqual(len(self.store.list(project_id="relune")), 1)
        self.assertEqual(len(self.store.list()), 2)


if __name__ == "__main__":
    unittest.main()
