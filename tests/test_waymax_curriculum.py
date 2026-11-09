from datetime import date, timedelta
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
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
            if cells[3] in {"IN_PROGRESS", "VERIFIED"}:
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
            ROOT / "docs/learning/autodrive-job-track.md",
            ROOT / "docs/learning/autodrive-job-track/README.md",
            ROOT / "docs/learning/autodrive-job-track/TEMPLATE.md",
            JOURNAL.parent / "day-01.md", ROOT / "docs/waymax-concepts-illustrated.md",
            JOURNAL.parent / "day-02.md",
            JOURNAL.parent / "day-03.md",
            JOURNAL.parent / "day-04.md",
            ROOT / "docs/waymax-reading-first-output.md",
            ROOT / "docs/waymax-files-explained.md",
            ROOT / "docs/waymax-first-baseline-walkthrough.md",
            ROOT / "docs/waymax-artifact-retention.md",
        ]
        paths.extend(JOURNAL.parent / f"day-{day:02d}.md" for day in range(1, 31))
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

    def test_illustrated_guide_separates_examples_results_and_acceptance(self):
        text = (ROOT / "docs/waymax-concepts-illustrated.md").read_text()
        for token in (
            "教学示意", "不是本地 TFRecord 的实测截图", "既有本地实测",
            "不是本次新运行", "不是两个神经网络", "Checkpoint",
            "Observation", "Behavior Cloning", "SimulatorState", "不是严格盲测",
        ):
            self.assertIn(token, text)

    def test_teaching_images_are_nonempty_pngs(self):
        for name, size in (
            ("roles-and-step.png", (1500, 1080)),
            ("three-experiments.png", (1500, 1130)),
            ("record-hierarchy.png", (1500, 1090)),
            ("reset-step-timeline.png", (1500, 1100)),
            ("action-units.png", (1500, 1100)),
        ):
            data = (ROOT / "docs/assets/waymax" / name).read_bytes()
            self.assertGreater(len(data), 10000)
            self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
            self.assertEqual(b"IHDR", data[12:16])
            self.assertEqual(size, struct.unpack(">II", data[16:24]))

    def test_day_one_records_execution_without_claiming_understanding(self):
        text = (JOURNAL.parent / "day-01.md").read_text()
        self.assertIn("IN_PROGRESS", text)
        self.assertIn("NOT_VERIFIED", text)
        self.assertIn("不证明学习者已掌握", text)
        self.assertIn("learning-20261107T152431Z-ym466a_w", text)
        self.assertIn("26 项测试", text)
        self.assertIn("不虚构退出码", text)
        self.assertIn("没有新车辆轨迹、训练 loss 或 checkpoint", text)

    def test_day_two_prepared_lesson_keeps_execution_and_understanding_unverified(self):
        text = (JOURNAL.parent / "day-02.md").read_text()
        for token in (
            "NOT_STARTED", "NOT_RUN", "NOT_VERIFIED", "只读",
            "python", "source.root", "waymax_file", "editable",
            "不必再运行 `make waymax-check`", "不是训练 checkpoint",
        ):
            self.assertIn(token, text)
        row = next(line for line in JOURNAL.read_text().splitlines()
                   if line.startswith("| 02 |"))
        self.assertIn("NOT_STARTED", row)
        self.assertIn("day-02.md", row)

    def test_day_three_separates_fields_slots_frames_and_valid_objects(self):
        text = (JOURNAL.parent / "day-03.md").read_text()
        for token in (
            "IN_PROGRESS", "NOT_RUN", "NOT_VERIFIED", "只读",
            "本课只读前两条", "125", "128", "32", "6", "14",
            "10 帧过去", "80 帧未来", "91 帧", "不是车辆数量",
            "30000", "不是严格盲测", "record_sha256", "data_sha256",
            "未初始化 JAX 后端", "没有新增实验运行目录",
        ):
            self.assertIn(token, text)
        row = next(line for line in JOURNAL.read_text().splitlines()
                   if line.startswith("| 03 |"))
        self.assertIn("IN_PROGRESS", row)
        self.assertIn("day-03.md", row)

    def test_day_four_separates_reset_step_and_simulation_time(self):
        text = (JOURNAL.parent / "day-04.md").read_text()
        for token in (
            "2026-11-08（Asia/Singapore）", "IN_PROGRESS", "NOT_RUN", "NOT_VERIFIED",
            "READING_ONLY", "没有初始化 JAX/TF/GPU", "不必重跑",
            "log_trajectory", "sim_trajectory", "init_steps=11", "dt=0.1",
            "timestep = 10 + n", "80 次推进 = 8 秒模拟时间，不是 80 秒",
            "之后", "is_done=true", "不是驾驶安全验收", "同一个 reset 起点",
            "learning-20261107T163242Z-3nn37_xl", "独立运行要求仍保留",
        ):
            self.assertIn(token, text)
        row = next(line for line in JOURNAL.read_text().splitlines()
                   if line.startswith("| 04 |"))
        self.assertIn("2026-11-08 | IN_PROGRESS", row)
        self.assertIn("day-04.md", row)

    def test_local_day_four_baseline_has_expected_timeline(self):
        run = ROOT / "artifacts/waymax/learning-20261107T163242Z-3nn37_xl"
        if not run.is_dir():
            self.skipTest("no local baseline; artifacts/ is gitignored")
        config = json.loads((run / "config.json").read_text())
        for name in ("expert-0", "expert-1"):
            directory = run / name
            summary = json.loads((directory / "summary.json").read_text())
            with (directory / "trajectory.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            initial = config["init_steps"] - 1
            steps = config["rollout_steps"]
            self.assertEqual(10, initial)
            self.assertEqual(80, steps)
            self.assertEqual(initial, summary["initial_timestep"])
            self.assertEqual(initial + steps, summary["final_timestep"])
            self.assertEqual(steps, summary["steps"])
            self.assertEqual(steps, len(rows))
            self.assertIs(summary["is_done"], True)
            self.assertEqual(list(range(1, steps + 1)),
                             [int(row["step"]) for row in rows])
            self.assertEqual(list(range(initial + 1, initial + steps + 1)),
                             [int(row["timestep"]) for row in rows])

    def test_day_five_explains_physical_and_normalized_actions(self):
        text = (JOURNAL.parent / "day-05.md").read_text()
        for token in (
            "2026-11-08（Asia/Singapore）", "IN_PROGRESS", "NOT_RUN", "NOT_VERIFIED",
            "READING_ONLY", "accel_m_s2", "steering_curvature_m_inv", "m/s²", "m⁻¹",
            "[-6.0, 6.0]", "[-0.3, 0.3]", "方向盘角度", "dt=0.1",
            "timestep+1", "inverse dynamics", "forward dynamics", "action_scale",
            "normalized_target", "[-2.0 m/s², 0.0 m⁻¹]", "不是安全控制器",
            "没有新增 RUN_DIR", "learning-20261107T163242Z-3nn37_xl",
        ):
            self.assertIn(token, text)
        row = next(line for line in JOURNAL.read_text().splitlines()
                   if line.startswith("| 05 |"))
        self.assertIn("2026-11-08 | IN_PROGRESS", row)
        self.assertIn("day-05.md", row)

    def test_remaining_day_lessons_are_recorded_without_auto_completion(self):
        for day in range(6, 31):
            text = (JOURNAL.parent / f"day-{day:02d}.md").read_text()
            self.assertIn("NOT_STARTED", text, day)
            self.assertIn("NOT_RUN", text, day)
            self.assertIn("NOT_VERIFIED", text, day)
            row = next(line for line in JOURNAL.read_text().splitlines()
                       if line.startswith(f"| {day:02d} |"))
            self.assertIn(f"day-{day:02d}.md", row)
            self.assertIn("NOT_STARTED", row)

    def test_job_track_is_practical_and_does_not_claim_job_readiness(self):
        text = (ROOT / "docs/learning/autodrive-job-track.md").read_text()
        for token in (
            "12 周补强计划", "C++", "CMake", "ROS 2", "PID", "Pure Pursuit",
            "Waymax 状态仿真", "不要求先“精通全部智能驾驶”", "不下载大数据",
            "不启动 CARLA", "checkpoint", "失败案例", "尚未开始执行",
        ):
            self.assertIn(token, text)

    def test_job_track_course_pack_is_complete_and_links_to_exercise(self):
        course_root = ROOT / "docs/learning/autodrive-job-track"
        index = (course_root / "README.md").read_text()
        self.assertIn("01", index)
        self.assertIn("12", index)
        for week in range(1, 13):
            path = course_root / f"week-{week:02d}.md"
            self.assertTrue(path.is_file(), path)
            text = path.read_text()
            self.assertIn("NOT_STARTED", text, path)
            self.assertIn("NOT_RUN", text, path)
            self.assertIn("验收", text, path)
        for path in (
            course_root / "TEMPLATE.md",
            ROOT / "projects/job-track/cpp-basics/CMakeLists.txt",
            ROOT / "projects/job-track/cpp-basics/README.md",
            ROOT / "projects/job-track/cpp-basics/include/vehicle_state.hpp",
            ROOT / "projects/job-track/cpp-basics/src/vehicle_state.cpp",
            ROOT / "projects/job-track/cpp-basics/src/main.cpp",
            ROOT / "projects/job-track/cpp-basics/tests/state_test.cpp",
        ):
            self.assertTrue(path.is_file(), path)

    def test_first_output_guide_explains_checks_without_promoting_them(self):
        text = (ROOT / "docs/waymax-reading-first-output.md").read_text()
        for token in (
            "开课前检查", "不是驾驶考试", "inventory", "cpu:0",
            "tensorflow_visible_gpus", "26 次驾驶试验", "bash -n",
            "不进行 `load_scenarios`", "记录中的字段数量",
        ):
            self.assertIn(token, text)

    def test_file_map_explains_generation_and_different_stage_outputs(self):
        text = (ROOT / "docs/waymax-files-explained.md").read_text()
        for token in (
            "learning_lab", "inventory", "Lab.rollout", "FILES.md", "file-guide.json",
            "artifact-manifest.json", "trajectory.csv", "summary.json", "arrays.npz",
            "CSV", "不回填修改旧档案", "没有默认保存为 run.log",
        ):
            self.assertIn(token, text)

    def test_baseline_lesson_preserves_stage_and_teacher_demonstration_boundaries(self):
        text = (ROOT / "docs/waymax-first-baseline-walkthrough.md").read_text()
        for token in (
            "本次由助手执行", "没有训练小模型", "learning-20261107T163242Z-3nn37_xl",
            "trajectory.csv", "initial_timestep", "log_divergence",
            "不是驾驶任务评分 PASS", "下一小步才是",
        ):
            self.assertIn(token, text)

    def test_archived_run_references_use_relocated_paths(self):
        old_names = (
            "learning-20261107T103618Z-k7gymij4",
            "learning-20261107T104337Z-0dl52ez7",
            "learning-20261107T104724Z-9blo18b0",
            "learning-20261107T105021Z-s0p4uex7",
            "repeatability-20261107.json",
        )
        for name in ("waymax-learning-plan.md", "waymax-learning-results-20261107.md"):
            text = (ROOT / "docs" / name).read_text()
            for old_name in old_names:
                self.assertNotIn(f"artifacts/waymax/{old_name}", text)
        plan = (ROOT / "docs/waymax-learning-plan.md").read_text()
        for old_name in old_names:
            self.assertIn(f"artifacts/waymax/_archive/{old_name}", plan)

    def test_local_relocation_receipt_preserves_archived_and_kept_files(self):
        archive = ROOT / "artifacts/waymax/_archive"
        receipts = sorted(archive.glob("relocation-*.json"))
        if not receipts:
            self.skipTest("no generated relocation receipt; artifacts/ is gitignored")
        for receipt_path in receipts:
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual("VERIFIED", receipt["status"])
            for record in receipt["moves"] + receipt["kept"]:
                target = Path(record.get("to", record.get("path", "")))
                self.assertTrue(target.resolve().is_relative_to((ROOT / "artifacts/waymax").resolve()))
                self.assertTrue(target.exists(), str(target))
                for name, expected in record["files"].items():
                    path = target / name if target.is_dir() else target
                    self.assertTrue(path.is_file() and not path.is_symlink(), str(path))
                    self.assertEqual(expected["bytes"], path.stat().st_size, str(path))
                    self.assertEqual(expected["sha256"],
                                     hashlib.sha256(path.read_bytes()).hexdigest(), str(path))

    def test_local_relocated_repeatability_report_matches_current_files(self):
        path = ROOT / "artifacts/waymax/_archive/repeatability-relocated-20261107.json"
        if not path.is_file():
            self.skipTest("no generated relocated report; artifacts/ is gitignored")
        report = json.loads(path.read_text())
        self.assertEqual("PASS", report["status"])
        for item in report["observations"]:
            self.assertTrue(item["equal"])
            for root in report["runs"]:
                source = Path(root) / item["file"]
                self.assertTrue(source.resolve().is_relative_to((ROOT / "artifacts/waymax").resolve()))
                self.assertEqual(item["sha256"],
                                 hashlib.sha256(source.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
