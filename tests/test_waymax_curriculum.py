from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/waymax-learning-30-days.md"
JOURNAL = ROOT / "docs/learning/waymax-30-days/README.md"
ROW = re.compile(r"^\| ([0-9]{2}) \| ([0-9]{4}-[0-9]{2}-[0-9]{2}) \|", re.MULTILINE)
SPEC = importlib.util.spec_from_file_location(
    "waymax_curriculum_support", ROOT / "scripts/waymax/lab_support.py",
)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


class WaymaxCurriculumTest(unittest.TestCase):
    def test_thirty_days_are_unique_consecutive_and_have_deliverables(self):
        text = PLAN.read_text()
        rows = ROW.findall(text)
        self.assertEqual([f"{day:02d}" for day in range(1, 31)], [row[0] for row in rows])
        start = date.fromisoformat(rows[0][1])
        self.assertEqual(
            [(start + timedelta(days=offset)).isoformat() for offset in range(30)],
            [row[1] for row in rows],
        )
        for line in text.splitlines():
            if ROW.match(line):
                cells = [value.strip() for value in line.split("|")[1:-1]]
                self.assertEqual(6, len(cells), line)
                self.assertTrue(all(cells), line)

    def test_journal_schedule_matches_plan_without_auto_completing_days(self):
        self.assertEqual(ROW.findall(PLAN.read_text()), ROW.findall(JOURNAL.read_text()))
        states = {"NOT_STARTED", "IN_PROGRESS", "VERIFIED", "REVIEW_REQUIRED"}
        for line in JOURNAL.read_text().splitlines():
            if not ROW.match(line):
                continue
            cells = [value.strip() for value in line.split("|")[1:-1]]
            self.assertIn(cells[3], states)
            if cells[3] == "VERIFIED":
                date.fromisoformat(cells[2])
                links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", cells[4])
                self.assertEqual(1, len(links), line)
                self.assertTrue((JOURNAL.parent / links[0]).is_file(), line)

    def test_every_experiment_changes_exactly_one_allowed_field(self):
        baseline = support.read_config()
        expected = {
            "updates-100.json": ("training_updates", 100),
            "hidden-16.json": ("hidden_units", 16),
            "lr-0003.json": ("learning_rate", 0.003),
            "seed-1.json": ("seed", 1),
        }
        directory = ROOT / "config/waymax/experiments"
        self.assertEqual(set(expected), {path.name for path in directory.glob("*.json")})
        plan = PLAN.read_text()
        for name, (field, value) in expected.items():
            config = json.loads((directory / name).read_text())
            support.validate_config(config)
            differences = {key for key in baseline if config[key] != baseline[key]}
            self.assertEqual({field}, differences, name)
            self.assertEqual(value, config[field])
            self.assertIn(f"config/waymax/experiments/{name}", plan)

    def test_local_curriculum_links_exist(self):
        paths = [
            PLAN, JOURNAL, JOURNAL.parent / "TEMPLATE.md",
            JOURNAL.parent / "preparation-20261107.md",
        ]
        for path in paths:
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text()):
                target = target.split("#", 1)[0]
                if not target or "://" in target:
                    continue
                self.assertTrue((path.parent / target).resolve().is_file(),
                                f"{path.name}: {target}")

    def test_template_distinguishes_learning_and_experiment_results(self):
        text = (JOURNAL.parent / "TEMPLATE.md").read_text()
        for token in (
            "学习状态", "实验预期", "实验实际", "NOT_RUN", "FAIL_EXPECTED",
            "退出码", "SHA256", "RUN_DIR", "观察事实", "当前推断", "未知项",
        ):
            self.assertIn(token, text)

    def test_preparation_is_not_presented_as_thirty_completed_days(self):
        plan = PLAN.read_text()
        self.assertIn("不是 30 天已经完成", plan)
        self.assertIn("不能再称为盲测集", plan)
        self.assertIn("不能用它声称盲测泛化", plan)
        self.assertIn("配置目前只是已准备和静态校验", plan)
        for gate in ("G1", "G2", "G3", "G4"):
            self.assertIn(gate, plan)


if __name__ == "__main__":
    unittest.main()
