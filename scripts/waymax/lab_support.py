"""Small, dependency-free contracts for the CPU learning lab."""

import hashlib
import json
import os
from pathlib import Path
import subprocess


REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "config/waymax/learning.json"
CPU_ENVIRONMENT = {
    "CUDA_VISIBLE_DEVICES": "-1",
    "JAX_PLATFORMS": "cpu",
    "JAX_PLATFORM_NAME": "cpu",
    "MPLBACKEND": "Agg",
}


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def describe_artifact(name):
    """Explain generated files without importing the simulation libraries."""
    path = Path(name)
    if path.parts[0] == "source":
        return ("learning_lab.main / shutil.copy2",
                "运行开始时复制的实验脚本，不是输入数据或模型。",
                "需要核对当时运行的代码时再看。")
    common = {
        "FILES.md": ("lab_support.write_file_guide", "本目录的阅读导航。", "先读本页。"),
        "file-guide.json": ("lab_support.write_file_guide", "同一份文件说明的结构化版本。",
                            "name、producer、purpose、first_read。"),
        "config.json": ("learning_lab.main", "复制本次使用的配置，保留这次打算做什么。",
                        "num_scenarios、rollout_steps；配置字段存在不代表训练已执行。"),
        "command.json": ("learning_lab.main", "记录 Python 实际收到的参数和部分进程环境。",
                         "argv、JAX_PLATFORMS、CUDA_VISIBLE_DEVICES。"),
        "code.json": ("learning_lab.main / sha256", "source/ 脚本副本的内容指纹。",
                      "用于检查代码是否改变，不是模型成绩。"),
        "environment.json": ("learning_lab.inventory", "查询本次 Python、代码、计算后端和输入文件。",
                             "jax_backend=cpu、tensorflow_visible_gpus=[]、data_file。"),
        "records.json": ("learning_lab.inspect_records", "从本地 TFRecord 解析所选记录的身份与字段规模。",
                         "两个 scenario_id；不是车辆个数。"),
        "status.json": ("learning_lab.main / progress", "运行中更新阶段状态，结束时保存耗时等信息。",
                        "stage、status、phases；只解释本次执行的阶段。"),
        "artifact-manifest.json": ("learning_lab.main / finally",
                                  "在本导航之后生成留存文件大小和 SHA256 清单；不包含自身哈希。",
                                  "用于发现文件损坏，不是可信签名或驾驶能力证明。"),
        "baseline.json": ("learning_lab.execute_rollouts",
                          "汇总两个 expert rollout 的身份、步数和指标。",
                          "rollouts、steps、metrics、valid_frames。"),
        "error.txt": ("learning_lab.main / exception handler",
                      "失败时保存 Python 错误回溯。", "保留错误，不改写成成功。"),
        "checkpoint.npz": ("lab_runtime.train_policy", "保存小模型参数和训练集归一化信息。",
                           "不是视频；仅 all 阶段训练后产生。"),
        "training-samples.npz": ("lab_runtime.train_policy",
                                "实际进入监督训练的特征和 expert 标签。",
                                "features、targets；仅 all 阶段产生。"),
        "training.json": ("lab_runtime.train_policy", "保存训练划分、loss、更新次数与 checkpoint 哈希。",
                          "initial_loss、final_loss；不等于闭环驾驶质量。"),
        "action-comparison.json": ("learning_lab.execute_rollouts",
                                   "比较同起点 expert 与固定动作的轨迹差。",
                                   "max_position_difference_m；仅证明动作影响状态。"),
        "observation-check.json": ("lab_runtime.check_future_observation_independence",
                                  "记录未来日志位置干预下的特征检查。",
                                  "status、max_feature_difference；不是所有泄漏的穷尽检查。"),
        "evaluation.json": ("learning_lab.execute_rollouts",
                            "记录训练场景和另一验证场景上的学习策略表现。",
                            "heldout_rollout、policy_quality_accepted。"),
        "result.json": ("learning_lab.execute_rollouts / reload_evaluation",
                        "汇总完整工程链路或新进程重载的结果。",
                        "scope；PASS 不表示驾驶安全。"),
        "reload-reference.npz": ("learning_lab.execute_rollouts",
                                 "保存重载对照的输入、预测和轨迹。",
                                 "供新进程比较，不是重新训练的样本。"),
        "reload-command.json": ("learning_lab.execute_rollouts",
                                "记录新进程重载命令。", "选择哪个 run 和输出目录。"),
        "reload.log": ("learning_lab.execute_rollouts / subprocess.run",
                       "保存新进程标准输出与错误输出。", "重载失败时先看它。"),
    }
    if path.name in ("trajectory.csv", "summary.json", "arrays.npz"):
        return {
            "trajectory.csv": (
                "lab_runtime.Lab.rollout / csv.DictWriter",
                "每次 step 后记录自车位置、动作和四项指标，一行对应一步。",
                "先看 step、timestep、x_m、y_m，再看单位和指标。"),
            "summary.json": (
                "lab_runtime.Lab.rollout",
                "把同目录 CSV 对应的 rollout 汇总为步数、起止帧和指标统计。",
                "steps、initial_timestep、final_timestep、valid_frames。"),
            "arrays.npz": (
                "lab_runtime.Lab.rollout / numpy.savez_compressed",
                "保存特征、轨迹等数值数组；expert 分支还有参考动作标签。",
                "先读 CSV/JSON；它不是训练好的模型。"),
        }[path.name]
    if path.suffix == ".png":
        return ("lab_runtime.plot_rollouts / matplotlib",
                "把静态地图点和模拟自车轨迹画成俯视图。",
                "不是相机画面或完整交通动画。")
    return common.get(path.name, (
        "未归类", "本导航没有为此文件定义用途。", "不要据文件名推断成功或安全。"))


def write_file_guide(run, stage, status, include_manifest=True):
    run = Path(run)
    names = {str(path.relative_to(run)) for path in run.rglob("*") if path.is_file()}
    names.update({"FILES.md", "file-guide.json"})
    if include_manifest:
        names.add("artifact-manifest.json")
    records = []
    for name in sorted(names):
        producer, purpose, first_read = describe_artifact(name)
        records.append({
            "name": name, "producer": producer, "purpose": purpose, "first_read": first_read,
        })
    write_json(run / "file-guide.json", {
        "schema_version": 1, "stage": stage, "status": status, "files": records,
        "scope": "文件来源与用途导航，不替代阶段结果和数据完整性校验。",
    })
    stage_notes = {
        "inventory": "本次只做环境和记录盘点，没有执行车辆推进、模型训练或 checkpoint 重载。",
        "baseline": "本次包含 expert 车辆推进和指标，没有训练小模型，也没有生成新 checkpoint。",
        "all": "本次请求完整教学实验；是否完成以 status.json 和各阶段实际结果为准。",
        "reload": "本次只重载已有 checkpoint 并评测，不重新训练。",
    }
    lines = [
        "# 本次运行的文件说明", "", f"- 请求阶段：`{stage}`。", f"- 收尾状态：`{status}`。",
        "- " + stage_notes.get(stage, "请核对实际命令和结果。"),
        "", "先把目录看成档案袋：config 是实验单，environment 是设备清单，",
        "records 是资料编号，CSV 是逐步运动记录，summary 是摘要，manifest 是内容指纹清单。",
        "", "## 先读哪些文件", "",
        "1. `status.json`（若有）：完成到哪里，有没有报错。",
        "2. `environment.json`：是不是 CPU，读的是哪份代码和数据。",
        "3. `records.json`：选了哪两条场景记录。",
        "4. 有轨迹时看 `expert-0/summary.json`、`expert-0/trajectory.csv` 和 `baseline.png`。",
        "", "## 来源与用途", "",
        "| 文件 | 谁生成 | 有什么用 | 初学先看 |", "| --- | --- | --- | --- |",
    ]
    for record in records:
        lines.append(
            f"| [{record['name']}]({record['name']}) | `{record['producer']}` | "
            f"{record['purpose']} | {record['first_read']} |"
        )
    lines += [
        "", "本页由脚本自动生成，不表示学习者已经理解或实验结果已被人工验收。",
        "inventory/baseline 没有 checkpoint 是正常的，不要把后续 all 阶段文件当作本次必需文件。",
        "若本次有 manifest，它在导航之后生成，并覆盖本导航；manifest 本身不做自我哈希。",
        "",
    ]
    (run / "FILES.md").write_text("\n".join(lines), encoding="utf-8")


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def require_cpu_environment():
    for key, expected in CPU_ENVIRONMENT.items():
        if os.environ.get(key) != expected:
            raise ValueError(f"{key} must be {expected!r}; use run-cpu.sh")
    if os.environ.get("LD_PRELOAD"):
        raise ValueError("LD_PRELOAD must be unset for this CPU-only experiment")
    cpus = sorted(os.sched_getaffinity(0))
    if not 1 <= len(cpus) <= 4:
        raise ValueError("CPU affinity must contain 1..4 processors")
    return cpus


def validate_config(config):
    allowed = {
        "schema_version", "source_root", "data_file", "num_scenarios",
        "max_num_objects", "max_num_rg_points", "num_paths",
        "num_points_per_path", "init_steps", "rollout_steps", "seed",
        "training_updates", "hidden_units", "learning_rate", "metrics",
    }
    if not isinstance(config, dict) or set(config) != allowed:
        raise ValueError("configuration has missing or unknown fields")
    bounds = {
        "schema_version": (1, 1), "num_scenarios": (2, 4),
        "max_num_objects": (5, 64), "max_num_rg_points": (16, 30000),
        "num_paths": (1, 30), "num_points_per_path": (1, 200),
        "init_steps": (1, 11), "rollout_steps": (2, 80),
        "seed": (0, 2**31 - 1), "training_updates": (1, 1000),
        "hidden_units": (2, 64),
    }
    for name, (low, high) in bounds.items():
        value = config[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} must be an integer in {low}..{high}")
    if config["init_steps"] + config["rollout_steps"] > 91:
        raise ValueError("requested rollout exceeds the 91-frame fixture")
    rate = config["learning_rate"]
    if type(rate) not in (int, float) or not 0 < rate <= 0.1:
        raise ValueError("learning_rate must be in (0, 0.1]")
    expected_metrics = {
        "log_divergence", "overlap", "offroad", "kinematic_infeasibility",
    }
    if (not isinstance(config["metrics"], list)
            or set(config["metrics"]) != expected_metrics
            or len(config["metrics"]) != len(expected_metrics)):
        raise ValueError("the four bounded learning metrics must be present exactly once")
    for name in ("source_root", "data_file"):
        if not isinstance(config[name], str) or not config[name]:
            raise ValueError(f"{name} must be a local path")
        if "://" in config[name] or any(c in config[name] for c in "*?[]@"):
            raise ValueError(f"remote paths and globs are not allowed: {name}")
    return config


def read_config(path=CONFIG_PATH):
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))


def local_path(value):
    path = Path(value).expanduser()
    return (REPO / path).resolve() if not path.is_absolute() else path.resolve()


def git_snapshot(path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(path), *args], text=True, stderr=subprocess.PIPE,
        ).strip()
    return {
        "root": str(Path(path).resolve()),
        "revision": git("rev-parse", "HEAD"),
        "status": git("status", "--short", "--untracked-files=no").splitlines(),
    }
