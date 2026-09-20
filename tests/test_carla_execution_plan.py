import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CarlaExecutionPlanTest(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "config/carla/execution-plan.json").read_text())

    def test_plan_is_not_stage_evidence_or_an_x86_route(self):
        self.assertEqual(1, self.plan["schema_version"])
        self.assertEqual("work_plan_not_stage_evidence", self.plan["kind"])
        constraints = self.plan["constraints"]
        self.assertEqual("linux/arm64", constraints["platform"])
        self.assertEqual("DGX Spark only", constraints["machine"])
        self.assertFalse(constraints["external_x86_cook_host"])
        self.assertEqual(1, constraints["max_concurrent_ue_builds"])
        self.assertFalse(constraints["silent_feature_removal"])

    def test_task_dependencies_are_explicit_and_acyclic(self):
        tasks = self.plan["tasks"]
        by_id = {task["id"]: task for task in tasks}
        self.assertEqual(len(tasks), len(by_id))
        complete = set()

        def visit(task_id, ancestors):
            self.assertIn(task_id, by_id)
            self.assertNotIn(task_id, ancestors)
            if task_id in complete:
                return
            for prerequisite in by_id[task_id]["depends_on"]:
                visit(prerequisite, ancestors | {task_id})
            complete.add(task_id)

        for task in tasks:
            self.assertTrue(task["owner"])
            self.assertTrue(task["write_scope"])
            self.assertTrue(task["acceptance"])
            self.assertTrue(task["does_not_prove"])
            self.assertIn(task["state"], ("queued", "blocked", "in_progress", "complete"))
            visit(task["id"], set())

    def test_product_stages_do_not_skip_editor_cook_or_sensors(self):
        tasks = {task["id"]: task for task in self.plan["tasks"]}
        self.assertEqual({"F2", "U1"}, set(tasks["E1"]["depends_on"]))
        self.assertEqual(["E1"], tasks["C1"]["depends_on"])
        self.assertEqual(["C1"], tasks["S1"]["depends_on"])
        self.assertEqual(["S1"], tasks["A1"]["depends_on"])
        self.assertEqual(["A1"], tasks["R1"]["depends_on"])
        self.assertEqual("blocked", tasks["C1"]["state"])
        self.assertEqual("blocked", tasks["A1"]["state"])


if __name__ == "__main__":
    unittest.main()
