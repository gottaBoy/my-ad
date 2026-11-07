# Waymax 本地小数据学习计划

日期：2026-11-07。修改前基线提交：`a747f34`。

准备实验完成后的逐日安排见
[30 天学习与验证计划](waymax-learning-30-days.md)，
实际学习进度见[每日日志](learning/waymax-30-days/README.md)。
下方 P0-P6 是已完成的工程准备，不等于 30 个学习日已完成。

## 目标与边界

复用 `/home/my/workspace/autodrive/` 下已有源码、Python 环境和约 38 MiB
测试 TFRecord，完成可重跑的“数据 → 仿真 → 动作对照 → 小模型训练 →
checkpoint → 新进程评测 → 报告”。

- 仅 CPU；不使用 CUDA、Vulkan、CARLA 或 GPU 容器。
- 不下载数据、模型或依赖，不修改宿主驱动与已有虚拟环境。
- 不修改 Waymax/V-Max 原仓库，不覆盖 `waymax-run/` 的历史图片。
- 首轮最多 2 个场景、32 个对象、每场景 80 步，固定随机种子。
- 小模型为行为克隆教学实验，不是完整 RL、V-Max 训练或可部署驾驶策略。
- Waymax 使用对象状态，不生成原始相机/LiDAR；NPC 的日志回放不等于响应式交通。
- 单个/极少场景不能证明泛化、安全性、真实传感器闭环或实车能力。

CPU 环境变量、线程数和进程 affinity 限制只作用于新进程。
同机运行仍有 CPU、内存和磁盘竞争，不能承诺绝对零影响。

## 分步骤执行

| 步骤 | 学习问题 | 验收证据 | 当前状态 |
| --- | --- | --- | --- |
| P0 基线 | 修改前是什么状态？ | `a747f34`，相关 74 项测试通过 | 已完成 |
| P1 环境与数据 | 实际加载哪个源码？是否只有 CPU？ | 版本、路径、revision、数据 SHA256、CPU device 与 TensorFlow 可见 GPU 清单 | 已通过，明确使用 `waymax-my` |
| P2 基线仿真 | `reset/step`、expert 和指标如何连接？ | 80 步记录、SDC 指标、轨迹与图片 | 已通过，两场景各 80 步 |
| P3 动作对照 | 动作是否真正改变后续状态？ | 同场景、同起点、改变控制后的轨迹差异 | 已通过，最大位置差约 53.69 m |
| P4 最小训练 | 如何从 expert 样本训练一个策略？ | 固定输入特征、训练样本、loss、参数与 checkpoint | 已通过，80 样本、300 更新，loss 下降 |
| P5 新进程评测 | checkpoint 是否真的可重载？ | 新进程重载后的预测/轨迹一致性、独立场景指标 | 已通过，新进程预测/轨迹差异均为 0 |
| P6 回归与记录 | 如何重跑并解释失败？ | 单元测试、重复性检查、命令与学习手册 | 已完成：17 项专项测试、29 项相关回归、重复运行与 46 个留存文件独立校验 |

每个步骤完成后更新本表；失败记录保留，不把部分执行写成 PASS。
依赖或数据不兼容时，先记录具体错误，不自动升级依赖或扩大数据量。

## 已发现的本地基础

| 路径 | 用途与注意事项 |
| --- | --- |
| `../run_waymax_min.py` | 历史 80 步 expert 示例；不直接运行以免覆盖旧图片 |
| `../waymax/waymax/dataloader/testdata/tfrecord_with_routes` | 本轮输入候选；读取和哈希，不改写 |
| `../.venv-waymax` | 已安装 JAX/TF/Optax；其 editable Waymax 指向 `../waymax-rl_planner` |
| `../waymax-rl_planner` | editable 安装指向这里，但与当前 JAX 的 `tree_map` API 不兼容；本轮不修改 |
| `../waymax-my` | 本轮明确选择的标准源码；使用 `jax.tree.map`，通过配置优先加载并校验实际路径 |
| `../v-max` / `../.venv-vmax` | 后续独立训练路线；默认车辆模型与依赖不同，本轮不修改 |

## 后续路线

本轮先建立小数据、CPU、可解释的完整工程证据。之后按需要单独立项：

1. V-Max BC/PPO/SAC 的独立兼容配置与小规模训练。
2. 正式授权 Waymo Motion 数据的多场景训练/测试划分。
3. Autoware Scenario Simulator 的规划控制接口。
4. 真实录制回放的感知/融合与数据闭环。
5. GPU 训练仅在单独确认时间窗、资源与风险后进行。

以下保留实际执行日志；未运行内容不写入成功数字。

## 执行日志

1. P1 初次通过，产物：
   `artifacts/waymax/learning-20261107T103618Z-k7gymij4/`。
   两个不同 scenario ID，fixture 40,073,764 bytes，JAX backend 为 CPU。
2. P2 首次失败，产物：
   `artifacts/waymax/learning-20261107T104337Z-0dl52ez7/`。
   已安装 fork 调用 `jax.tree_map`，当前 JAX 0.11.2 已移除该别名。
   保留错误，不修改 fork 或升级环境；明确将本轮 `source_root` 改为
   `../waymax-my`，其源码已采用受支持的 `jax.tree.map`。
3. P1/P2 复核通过，产物：
   `artifacts/waymax/learning-20261107T104724Z-9blo18b0/`。
   两场景各 80 步、四项 SDC 指标各 80 有效帧；约 10.916 秒，
   峰值 RSS 888.12 MiB。两场景 expert 的平均日志位置差分别约
   0.01642 m / 0.01426 m；不作为独立驾驶策略的效果证明。
4. 首次完整 P1-P5 通过，产物：
   `artifacts/waymax/learning-20261107T105021Z-s0p4uex7/`。
   33.248 秒，父进程峰值 RSS 911.73 MiB；loss 从 0.108001925 降至
   0.0000787876，checkpoint 约 17 KiB，新进程预测/轨迹差异均为 0。
   独立场景学习策略平均偏离约 15.75 m、越界帧比例 65%，故**仅工程通过，
   策略质量未通过**。未掩盖该负面结果。
5. 增加独立产物校验后再次完整通过，正式学习产物：
   `artifacts/waymax/learning-20261107T105737Z-_rn5s941/`。
   34.227 秒，父进程峰值 RSS 917.75 MiB，46 个留存文件校验 PASS。
   本轮与上轮 checkpoint 和五份轨迹 CSV 的 SHA256 完全相同；
   对照记录为 `artifacts/waymax/repeatability-20261107.json`。
6. `make test-waymax`：17 tests PASS；
   Waymax、既有学习手册、NAVSIM、Scenario 合约相关回归：29 tests PASS
   （包含上述 17 项，不重复计数）。
   测试覆盖 CPU 强制策略、规模边界、本地路径、NaN、损坏/缺失 checkpoint、
   symlink 和 manifest 目录逃逸。未声称全仓库或 GPU 测试通过。
7. 单独执行 `make waymax-evaluate WAYMAX_RUN=<正式学习目录>` 再次通过，
   新进程 PID 2598395，80 步评测完成。产物为正式目录下
   `reload-20261107T110140532108Z/`，不覆盖原来的自动重载证据。

## 当前交付

本轮 P0-P6 已完成，验收范围是 CPU 小数据工程链路，不是驾驶策略效果。

- 学习入口：[waymax-learning-lab.md](waymax-learning-lab.md)
- 实测解释：[waymax-learning-results-20261107.md](waymax-learning-results-20261107.md)
- 一键重跑：`make waymax-learning`
- 留存结果校验：`make waymax-verify WAYMAX_RUN=<本轮输出目录>`
- 基线提交：`a747f34`；实现与学习文档纳入当前提交，实际提交号以
  `git log -1` 为准；原始大产物仍留在本机，未推送远端。
