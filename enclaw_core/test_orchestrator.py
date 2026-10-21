"""Tests for bounded task decomposition and parent/child aggregation."""
import json
import tempfile
import unittest
from pathlib import Path

from .orchestrator import Orchestrator, OrchestrationError
from .policy import default_policy
from .tasks import TaskStore


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.policy_path = Path(self.temp.name) / "policy.json"
        self.database = Path(self.temp.name) / "tasks.sqlite3"
        policy = default_policy("demo", str(self.root), "main", "enclaw/integration")
        policy["runner"]["planner_command"] = [
            "python3", "-c",
            "import json; print(json.dumps({'tasks':[{'title':'Add tests','description':'Add focused unit tests.'},{'title':'Update docs','description':'Document the behavior.'}]}))",
            "{prompt}",
        ]
        policy["runner"]["max_planner_seconds"] = 30
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        self.projects = {"demo": {"id": "demo", "policy_path": str(self.policy_path)}}
        self.store = TaskStore(self.database)
        self.orchestrator = Orchestrator(self.database)

    def test_decomposes_once_and_tracks_children(self):
        parent = self.store.create("demo", "Improve service", "Improve reliability and documentation.")
        result = self.orchestrator.decompose_one(self.projects)
        self.assertEqual(result["parent"], parent["task_id"])
        self.assertEqual(len(result["children"]), 2)
        self.assertEqual(self.store.get(parent["task_id"])["status"], "running")
        children = [self.store.get(task_id) for task_id in result["children"]]
        self.assertTrue(all(child["status"] == "queued" for child in children))
        self.assertTrue(all(parent["task_id"] in child["description"] for child in children))
        self.assertIsNone(self.orchestrator.decompose_one(self.projects))

    def test_parent_fails_when_any_child_fails(self):
        parent = self.store.create("demo", "Improve service", "Split this task.")
        result = self.orchestrator.decompose_one(self.projects)
        for index, child_id in enumerate(result["children"]):
            self.store.transition(child_id, "cancelled" if index else "planning", actor="test")
            if index == 0:
                self.store.transition(child_id, "running", actor="test")
                self.store.transition(child_id, "failed", actor="test")
        aggregate = self.orchestrator.refresh_groups()
        self.assertEqual(aggregate[0]["state"], "failed")
        self.assertEqual(self.store.get(parent["task_id"])["status"], "failed")
        self.assertEqual(self.orchestrator.refresh_groups(), [])

    def test_invalid_planner_output_fails_closed(self):
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy["runner"]["planner_command"] = ["python3", "-c", "print('not json')", "{prompt}"]
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        parent = self.store.create("demo", "Bad plan", "This should fail safely.")
        with self.assertRaises(OrchestrationError):
            self.orchestrator.decompose_one(self.projects)
        self.assertEqual(self.store.get(parent["task_id"])["status"], "failed")
        self.assertEqual(len(self.store.list()), 1)


if __name__ == "__main__":
    unittest.main()
