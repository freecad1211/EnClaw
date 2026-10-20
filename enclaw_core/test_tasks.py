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

    def test_failed_task_retry_is_bounded_and_clears_cancel_flag(self):
        self.store.transition(self.task["task_id"], "planning", actor="test")
        self.store.transition(self.task["task_id"], "failed", actor="test")
        retried = self.store.retry_failed(self.task["task_id"], max_retries=1)
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["attempts"], 1)
        self.assertFalse(retried["cancellation_requested"])
        self.store.transition(self.task["task_id"], "planning", actor="test")
        self.store.transition(self.task["task_id"], "failed", actor="test")
        with self.assertRaises(TaskStoreError):
            self.store.retry_failed(self.task["task_id"], max_retries=1)

    def test_terminal_task_cannot_be_cancelled(self):
        self.store.transition(self.task["task_id"], "cancelled", actor="operator")
        with self.assertRaises(TaskStoreError):
            self.store.request_cancel(self.task["task_id"])

    def test_project_worker_slots_enforce_capacity_and_release(self):
        first = self.store.acquire_project_slot("relune", 1)
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.acquire_project_slot("relune", 1))
        # A different project has an independent concurrency budget.
        other = self.store.acquire_project_slot("other", 1)
        self.assertIsNotNone(other)
        self.store.release_project_slot(first)
        replacement = self.store.acquire_project_slot("relune", 1)
        self.assertIsNotNone(replacement)
        self.store.release_project_slot(other)
        self.store.release_project_slot(replacement)

    def test_zero_project_worker_limit_disables_workers(self):
        self.assertIsNone(self.store.acquire_project_slot("relune", 0))

    def test_task_list_can_be_filtered_by_project(self):
        self.store.create("other", "Other task", "Separate project")
        self.assertEqual(len(self.store.list(project_id="relune")), 1)
        self.assertEqual(len(self.store.list()), 2)


if __name__ == "__main__":
    unittest.main()
