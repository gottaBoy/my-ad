# Waymax 30 天学习与验证计划

制定日期：2026-11-07。建议 Day 01 从 **2026-11-08** 开始，Day 30 到
**2026-12-07** 结束；日期可整体顺延，不要求补跑“错过的天数”。
每天建议 45–90 分钟，只推进已满足前置验证的内容。

**这是后续学习安排，不是 30 天已经完成的记录。** 工程准备已完成，
Day 01 已开始学习但尚未验收；Day 02 只有备好的课单，尚未开始。
日程与实际完成状态分开维护：

- [每日进度与日志入口](learning/waymax-30-days/README.md)
- [每日记录模板](learning/waymax-30-days/TEMPLATE.md)
- [准备阶段实测](learning/waymax-30-days/preparation-20261107.md)
- [已有操作手册](waymax-learning-lab.md)
- [已有工程结果与负面结果](waymax-learning-results-20261107.md)
- [先读：入门图解与中英文词表](waymax-concepts-illustrated.md)

学习补记：Day 01 于 2026-11-07 提前开始概念讨论，尚未通过学习验收，
具体记录在每日日志。下方建议日期保留，不改变实际完成状态。

## 1. 30 天后的目标

能独立说明并验证：

```text
本地数据和版本
  -> 场景、历史与当前状态
  -> expert / 固定动作 / 学习策略
  -> Waymax 动力学 step
  -> 逐帧 SDC 指标
  -> 小模型训练与 checkpoint
  -> 独立进程重载
  -> 可追溯的对照实验与失败复现
```

最终交付不是“学会无人驾驶”，而是一份可重跑的小实验、完整证据和你对
结果的解释。当前小模型已有越界问题；本课程不预设指标一定改善。

## 2. 固定边界

- 使用已有本地测试 TFRecord，40,073,764 bytes；默认只取 2 个场景。
- 默认每场景 32 个对象、80 步、2 个 CPU affinity，不下载大数据。
- 不修改原始数据、虚拟环境、Waymax/V-Max 原仓库或系统驱动。
- 不启动 GPU、Vulkan、CARLA、Isaac 或 V-Max 的大规模训练默认配置。
- 本月使用行为克隆教学模型，不把它称为 PPO/SAC 或完整 RL。
- 本地测试数据的使用范围和后续用途需分别核对，不默认获得其它数据授权。
- `PASS` 分为工程通过、实验结果、策略质量；三者不可混用。

两个场景的使用：

| 角色 | scenario ID | 使用规则 |
| --- | --- | --- |
| 训练场景 | `d27157fded0ddd5e` | 产生 expert 监督样本与归一化统计 |
| 开发/验证场景 | `543908aa309f5a0b` | 分析错误和对照；已经观察过，不能再称为盲测集 |

本月对验证场景反复分析，因此不能据此报告严格的测试集泛化成绩。
新增盲测、多场景统计和正式数据划分是后续独立工作，不为凑指标扩大数据。

## 3. 每天的统一流程

1. **回顾 5 分钟**：昨天的结论、失败和下一步。
2. **阅读 15–25 分钟**：只追踪当天涉及的函数和数据。
3. **操作 20–40 分钟**：固定输入与版本，每次最多改变一个变量。
4. **验证 10–15 分钟**：核对预期、有效帧、退出码和原始产物。
5. **记录 10 分钟**：写实际结果与解释；未执行就保留 `NOT_RUN`。

每次都在仓库根目录执行：

```bash
cd /home/my/workspace/autodrive/my-ad
```

命令结束后，以终端打印的 `RUN_DIR` 设置自己的变量，不自动选择“最新目录”：

```bash
LAB_RUN=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-某次实际输出目录
```

上面是占位示例，不可原样执行后认为路径存在。后续命令中的 `"$LAB_RUN"`
都必须指向你已经确认过的那一次完整运行。baseline-only 输出不能代替完整运行。

## 4. 第一周：环境、数据、仿真与指标

阅读入口：
`scripts/waymax/lab_support.py`、`learning_lab.py`、
`lab_runtime.py`，以及选定源码 `../waymax-my/waymax/`。

| Day | 建议日期 | 学习重点 | 操作与阅读 | 留存产物 | 当天验收 |
| --- | --- | --- | --- | --- | --- |
| 01 | 2026-11-08 | 安全边界与入口 | 读操作手册；执行 `make waymax-check`、`make test-waymax` | 日志、`environment.json`、测试结果 | JAX 为 CPU、TF 可见 GPU 为空；能说清本月不验证什么 |
| 02 | 2026-11-09 | 代码与环境身份 | 追踪 `inventory`、`git_snapshot`、`source_root`；对照 Python 路径与 editable 安装来源 | 一张“解释器—安装注册—实际源码”关系图 | 能解释为什么选择 `waymax-my`，而不是静默使用旧 fork |
| 03 | 2026-11-10 | TFRecord 与场景 | 读 `inspect_records`、`load_scenarios`；查看 `records.json` 与配置 | 两个 ID、输入 hash、对象/地图点规模说明 | 两个 ID 不同；原始输入不变；说明训练与验证角色 |
| 04 | 2026-11-11 | reset、历史与时间 | 执行 `make waymax-baseline`；读 `BaseEnvironment.reset` 和 `Lab.rollout` | baseline RUN_DIR、首尾 CSV 行 | 理解 reset 后 timestep=10，80 次 step 到 90；不是 80 秒 |
| 05 | 2026-11-12 | 动力学与动作单位 | 读 `InvertibleBicycleModel`、expert 逆动力学和动作边界 | 动作字段、单位、边界表 | 区分加速度、转向曲率与方向盘角度；解释 expert 为什么使用下一帧日志 |
| 06 | 2026-11-13 | 指标与有效性 | 读 `Lab.metrics` 调用与 `trajectory.csv`；独立核对一项均值 | 指标表、有效帧数与手算/标准库计算 | 区分米、帧比例、事故次数；不把 `null` 当 0 |
| 07 | 2026-11-14 | 第一周复盘 | 重跑 baseline；用配置、ID、CSV、图片解释全过程 | `week-01.md` 和新 baseline RUN_DIR | P1/P2 均通过，两场景各 80 有效帧；不要求运行完整结果校验器 |

Day 02 的只读操作步骤已备好：
[解释器、安装登记与实际源码](learning/waymax-30-days/day-02.md)。
不用重跑实验或新增结果目录，也不表示该学习日已经完成。

**G1 周门槛**：能不看说明讲清“数据 → reset → action → step → metric”，
并指出 expert 的答案来源。未满足就复习，不进入调参。

## 5. 第二周：动作因果、观测与最小训练

`make waymax-learning` 会一次生成 P1–P5 产物。本周按天拆开阅读这些产物，
不是声称脚本具有按 Day 分阶段执行的接口。

| Day | 建议日期 | 学习重点 | 操作与阅读 | 留存产物 | 当天验收 |
| --- | --- | --- | --- | --- | --- |
| 08 | 2026-11-15 | 动作真的改变状态 | 执行 `make waymax-learning`；对照 expert 与 brake 的 CSV、`action-comparison.json` | 完整 LAB_RUN、对照图与结论 | 同起点、同场景；位置差大于 0；理解固定制动不是安全控制器 |
| 09 | 2026-11-16 | 当前观测与未来信息 | 读 `features_from_state`、`check_future_observation_independence` | 120 维特征分组图、`observation-check.json` | 说明模型输入与监督标签的区别；只对已执行的未来 x/y 干预作结论 |
| 10 | 2026-11-17 | 监督数据与划分 | 读 expert 标签生成、有效样本筛选和训练归一化 | `training.json` 的输入引用、样本数、划分说明 | 归一化只来自训练场景；验证场景不进入训练 |
| 11 | 2026-11-18 | 神经网络前向 | 读 `TinyPolicy`、`make_predictor`；手绘维度变化 | `120 → 32 → 2` 结构图与参数数量推导 | 能解释 tanh、归一化动作和物理动作之间的关系 |
| 12 | 2026-11-19 | loss 与梯度更新 | 读 `train_policy` 中 loss、`value_and_grad` 和 Adam | loss 曲线/表、更新次数与 checkpoint hash | loss 有限且下降；明确这是行为克隆，不是强化学习 |
| 13 | 2026-11-20 | checkpoint 包含什么 | 读 `load_predictor`；执行 `sha256sum "$LAB_RUN/checkpoint.npz"` | 参数/归一化/schema 清单与 hash | 解释为什么不能只保存网络权重、为什么拒绝 hash/schema 不匹配 |
| 14 | 2026-11-21 | 新进程与第二周复盘 | 执行 `make waymax-evaluate WAYMAX_RUN="$LAB_RUN"` | 新 `reload-*` 结果、`week-02.md` | 新 PID、80 步、预测差不超过 1e-6、轨迹差不超过 1e-5 m；不把重载一致当效果良好 |

**G2 周门槛**：能说明训练使用了什么信息，checkpoint 如何重建相同策略，
以及为什么很低的监督 loss 仍可能伴随越界。

## 6. 第三周：小规模单变量实验

已备好四份配置，均只改变默认配置中的一个字段；其它输入保持不变。
所有实验仍为 CPU、两个本地场景，不需要新数据。

| Day | 建议日期 | 学习重点 | 操作与阅读 | 留存产物 | 当天验收 |
| --- | --- | --- | --- | --- | --- |
| 15 | 2026-11-22 | 更新次数对照 | 执行下方 `updates-100` 命令，对照默认 300 更新 | 两份配置与训练/验证指标 | 只改变更新次数；记录好坏结果，不预设 300 一定更好 |
| 16 | 2026-11-23 | 模型容量对照 | 执行 `hidden-16`，对照默认 32 单元 | 参数维度、loss、rollout 误差表 | 只改变容量；区分拟合能力与闭环效果 |
| 17 | 2026-11-24 | 学习率对照 | 执行 `lr-0003`，对照默认 0.01 | loss 变化与稳定性说明 | 只改变学习率；不得再顺手改变 seed 或训练次数 |
| 18 | 2026-11-25 | 随机种子对照 | 执行 `seed-1`，对照默认 seed=0 | 两个 seed 的训练/验证结果 | 理解单次结果不能代表稳定统计；两个 seed 也不是充分统计 |
| 19 | 2026-11-26 | 低 loss 与 rollout 误差 | 比较 `learned-training`、`learned-evaluation` 和 expert | 至少一例“loss 与驾驶指标不一致”的分析 | 分开报告训练拟合和验证表现；只提出假设，不宣布唯一根因 |
| 20 | 2026-11-27 | 失败片段定位 | 从 CSV 找首次越界或明显偏离的时间窗口，对照图片 | 一个坏例子的 timestep、动作、位置、指标与截图引用 | 能从汇总数字回到具体帧；没有失败则如实记录，不伪造 |
| 21 | 2026-11-28 | 第三周实验复盘 | 汇总默认与四个单变量实验，执行 `make test-waymax` | `week-03.md`、实验矩阵、测试结果 | 每行有真实 RUN_DIR 与配置 hash；允许“无改善/变差”，禁止挑最好一行隐藏其它结果 |

实验命令，每条独立执行并分别记录其 `RUN_DIR`：

```bash
bash scripts/waymax/run-cpu.sh --stage all --config config/waymax/experiments/updates-100.json
bash scripts/waymax/run-cpu.sh --stage all --config config/waymax/experiments/hidden-16.json
bash scripts/waymax/run-cpu.sh --stage all --config config/waymax/experiments/lr-0003.json
bash scripts/waymax/run-cpu.sh --stage all --config config/waymax/experiments/seed-1.json
```

对照矩阵至少有：配置路径/hash、输入 hash、源码 revision、seed、初末 loss、
训练/验证位置误差、越界/重叠有效帧、耗时、父进程 RSS、checkpoint hash。
**这些配置目前只是已准备和静态校验，不代表四个未来实验已经执行。**

**G3 周门槛**：能保持控制变量、保留负面结果，并明确验证场景已经被反复观察，
不能用它声称盲测泛化。

## 7. 第四周：复现、故障验证与独立交付

| Day | 建议日期 | 学习重点 | 操作与阅读 | 留存产物 | 当天验收 |
| --- | --- | --- | --- | --- | --- |
| 22 | 2026-11-29 | 冻结复现条件 | 读 `environment.json`、`code.json`、manifest；记录 `git rev-parse HEAD` | 固定输入/代码/版本/CPU 数清单 | 能指出哪些在 git、哪些只在本机；不声称干净新机器已复现 |
| 23 | 2026-11-30 | 重复性验证 | 同配置、同环境、同 CPU 数再跑一次完整实验 | checkpoint/CSV hash 对照 | 当前基线应可重复；不一致时保留并调查，不临时放宽容差掩盖 |
| 24 | 2026-12-01 | 独立结果校验 | 执行 `make waymax-verify WAYMAX_RUN="$LAB_RUN"`；读 `verify_run` | 校验器输出与 covered files 数量 | 完整工程产物通过；不对 baseline-only 目录要求 P1–P5 |
| 25 | 2026-12-02 | checkpoint 篡改负测 | 只在临时副本中改变 checkpoint，再运行校验器 | 原始副本路径、变更、exit 2 与错误 | 学习任务可完成，但被破坏实验必须 FAIL；不得改原 checkpoint |
| 26 | 2026-12-03 | 缺失与路径负测 | 在另一临时副本中移走 CSV；阅读 symlink/目录逃逸测试 | 缺失文件报错和相关单元测试结果 | 能解释为什么“程序 exit 0”不足以证明证据完整 |
| 27 | 2026-12-04 | 资源测量 | 以 `WAYMAX_CPU_COUNT=1 make waymax-learning` 对照默认 2 CPU | 两份 affinity/耗时/RSS 记录 | 两次都只用 CPU；不承诺加核一定加速，也不把父进程 RSS 当作业总内存 |
| 28 | 2026-12-05 | 能力边界与后续模块 | 只读 V-Max 训练文档、Scenario/NAVSIM 路线；不运行其默认训练 | 一张“已有/未实现/需适配”表 | 区分 BC、RL、ROS 控制、原始传感器与状态仿真 |
| 29 | 2026-12-06 | 独立小项目彩排 | 选择默认或一个已解释过的配置，冻结协议，独立从检查到评测 | `final-report.md` 草稿、命令、原始输出引用 | 他人按记录能找到同一输入与产物；没有结果的项目明确 NOT_RUN |
| 30 | 2026-12-07 | 最终演示与验收 | 完整运行、重载、校验、测试；用 10 分钟讲述全链路 | 最终报告、进度表、学习笔记提交 | 工程门禁通过且能解释负面结果；策略质量仍须单独判断，不升级为安全或实车通过 |

**G4 最终门槛**：

1. 本地数据、源码、配置和依赖版本可追溯，明确没有 GPU/新下载。
2. 两个场景各 80 步，所有指标结合有效帧解释。
3. 至少一个动作干预与一个单变量模型实验有完整对照。
4. checkpoint 新进程重载通过，独立产物校验通过。
5. 至少两个预期失败检查确实拒绝损坏/缺失证据。
6. 保留全部对照和失败，不把工程通过、验证场景表现和驾驶安全混为一谈。

## 8. 负向检查的安全操作

只对完整运行的副本操作，原始 `LAB_RUN` 保持不变：

```bash
: "${LAB_RUN:?先设置已确认的完整运行目录}"
SCRATCH="$(mktemp -d /tmp/waymax-study.XXXXXX)"
cp -a "$LAB_RUN" "$SCRATCH/run"
printf 'deliberate-test-corruption' >> "$SCRATCH/run/checkpoint.npz"
python3 scripts/waymax/verify_learning_run.py "$SCRATCH/run"
```

预期最后一条命令 exit 2 并打印 FAIL。记录“**学习验证符合预期**”和
“**被篡改实验 FAIL**”两种状态，不把故障实验改写成 PASS。
Day 26 使用新的临时副本，把 `expert-0/trajectory.csv` 移到副本外后再校验。
不要对原始运行目录执行破坏操作，不使用全局清理或 git 强制回退。

## 9. 记录与停止规则

每一天在日志目录下建立 `day-NN.md`，按模板填实际日期、提交号、配置/hash、
命令、退出码、RUN_DIR、观察、推断、未知项、门槛和下一步，再更新进度表。
没有执行的日子保持 `NOT_STARTED`；读过文档但没有验证，不自动记作 `VERIFIED`。

每周复盘保存 `week-01.md` 至 `week-04.md`，最后一周另有 `final-report.md`。
只提交实际写过的笔记和声明过的配置，不提交数据、虚拟环境或大 checkpoint。
不要为了完成课程而修改已有测试去接受错误结果。

遇到以下情况停止当前步骤，记为 `REVIEW_REQUIRED`：

- 出现非 CPU backend、可见 GPU、异常资源占用或新的系统异常。
- 输入/源码/版本漂移、数据解析失败、checkpoint/hash/schema 不匹配。
- 指标没有有效帧、出现 NaN、样本身份重复或证据不全。
- 需要安装依赖、下载新数据或更改原仓库才能继续。

停止不等于学习失败。留下可解释的失败和下一步，优于勾选没有证据的完成项。
