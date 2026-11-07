# 实验目录里的文件：哪里来，有什么用

日期：2026-11-07（学习者日期）。先读[概念图解](waymax-concepts-illustrated.md)，
再把本页当作“打开结果文件夹时的导航”。
根目录现在只展示三份主要结果，旧结果移到 `_archive/`；
位置与保留规则见[归档说明](waymax-artifact-retention.md)，不是删除历史证据。

## 1. 谁创建了这些文件

这些 JSON、CSV 和图片不是 Waymo 原始数据里自带的成绩单，
是**本仓库的教学脚本在运行时生成的实验记录**。

```text
你输入 make waymax-check / make waymax-baseline
    |
    v
Makefile 找到对应任务
    |
    v
run-cpu.sh：设置本进程 CPU/GPU 可见性、线程数和超时
    |
    v
learning_lab.py：读取配置，建立新的 RUN_DIR 档案袋
    |
    +-- 复制实验单 config.json、脚本 source/，记下命令
    +-- 查询环境 -> environment.json
    +-- 打开本地 TFRecord -> records.json
    +-- 如果要求 baseline -> expert 动作 + Waymax.step -> 轨迹/指标/图片
    +-- 更新 status.json
    +-- 自动写 FILES.md、file-guide.json
    +-- 最后计算各文件的大小和指纹 -> artifact-manifest.json
```

Waymo 格式解析、Waymax 状态推进与指标、我们保存的教学报告，是三层不同东西。
例如，`Lab.rollout` 调用 Waymax 推进，然后由本仓库的 CSV writer 写出逐步记录；
不是自己编造车辆运动结果。

## 2. 把目录想成一次实验的档案袋

| 档案袋里的东西 | 实际文件 | 类比 |
| --- | --- | --- |
| 这次打算怎么做 | `config.json` | 实验申请单 |
| 这次确实怎么启动 | `command.json` | 操作记录 |
| 用了什么设备、代码、资料 | `environment.json` | 设备与材料清单 |
| 资料中选了哪两段 | `records.json` | 样本编号表 |
| 做到了哪一步 | `status.json` | 进度与结束记录 |
| 小车每一步的运动 | `trajectory.csv` | 逐步观察表 |
| 全部步骤的统计 | `summary.json` | 实验摘要 |
| 地图和轨迹 | `baseline.png` | 俯视结果图 |
| 文件是否被改动 | `artifact-manifest.json` | 内容指纹核对单，不是安全证书 |

## 3. 盘点阶段就会有的文件

对应命令：

```bash
make waymax-check
```

| 文件 | 如何产生 / 对应函数 | 有什么用 | 第一次只看什么 |
| --- | --- | --- | --- |
| `config.json` | `learning_lab.main` 读取并校验选定配置后保存副本 | 以后默认配置变了，仍知道这次的设置 | `num_scenarios`、`rollout_steps`、`source_root` |
| `command.json` | `main` 记录 `sys.argv`、解释器和部分环境变量 | 知道这次是否传了 `--stage inventory` | `argv` |
| `source/` | `main` 用 `shutil.copy2` 复制本仓库实验脚本 | 保留这次实际脚本，不随以后编辑变化 | 现在不用读；排查复现问题再看 |
| `code.json` | `main` 对脚本副本计算 SHA256 | 核对脚本内容是否变过 | 先知道它是代码指纹表 |
| `environment.json` | `inventory` 查询 JAX、TF、版本、git、数据文件并检查 | 证明实际用了哪份环境、代码和输入 | `jax_backend`、`tensorflow_visible_gpus`、`data_file` |
| `records.json` | `inspect_records` 解析所选 TFRecord 的字段与身份 | 避免重复记录、确认数据划分依据 | 两个 `scenario_id` |
| `status.json` | `main`/`progress` 运行中更新，结束时保存耗时与 RSS | 失败后知道卡在哪；成功后知道完成哪一阶段 | `stage`、`status`、`phases` |
| `FILES.md` | `write_file_guide` 在运行收尾时根据实际文件生成 | 打开本次目录后的中文阅读导航 | 从这份 Markdown 开始读 |
| `file-guide.json` | 与 `FILES.md` 同时生成的结构化说明 | 工具可以读，内容有文件名、生成者、用途 | 不必与 Markdown 重复阅读 |
| `artifact-manifest.json` | `main` 最后枚举文件并计算大小与 SHA256 | 检查缺失、损坏、改动，不证明结果正确 | 指纹字段与 `files` |

`FILES.md` 和 `file-guide.json` 从本次修改后开始自动生成。
你之前的 `learning-20261107T152431Z-ym466a_w/` 没有这两个文件是正常的；
**不回填修改旧档案**，保留其原有内容和 manifest。

配置里的 `training_updates=300` 只是“如果训练时采用的设置”，
不代表 `make waymax-check` 已经执行了训练。判断实际做过什么要看阶段和结果。

## 4. baseline 阶段新增什么

对应命令：

```bash
make waymax-baseline
```

它会在一个**新的**结果目录重新执行 P1，然后构造场景、执行 expert 车辆推进。
不会把上一份 inventory 目录接着改写。

```text
新的 learning-.../
    environment.json、records.json 等共同记录
    baseline.json
    baseline.png
    expert-0/
        trajectory.csv
        summary.json
        arrays.npz
    expert-1/
        trajectory.csv
        summary.json
        arrays.npz
    FILES.md
    file-guide.json
    artifact-manifest.json
```

`SimulatorState` 是构造在内存里的 Python/JAX 数据结构，
**本实验不会默认写一个叫 `SimulatorState.json` 的文件**。
目录里找不到这个名字，不是丢数据。

| 文件 | 如何产生 / 对应函数 | 有什么用 | 不要误读为 |
| --- | --- | --- | --- |
| `expert-0/trajectory.csv` | `Lab.rollout` 每次 `step` 后取自车位置与指标，最后用 `csv.DictWriter` 写出 | 看每一步发生了什么；可用文本或表格软件打开 | 所有车辆的完整原始记录 |
| `expert-0/summary.json` | `Lab.rollout` 汇总同次 rollout 的指标与有效帧 | 先看步数，再看平均值/最大值 | 一个文件存在就等于驾驶安全 |
| `expert-0/arrays.npz` | `numpy.savez_compressed` 保存特征、参考动作、标签有效性与 xy 数组 | 后续训练或数值比较使用 | 已经训练好的 checkpoint |
| `expert-1/` 同名文件 | 对第二条场景记录重复相同流程 | 比较两个场景，不把它当第 1 秒 | 两个车辆目录 |
| `baseline.json` | `execute_rollouts` 汇总两个 expert 的结果 | 快速找到 ID、步数、指标范围 | 小模型的驾驶成绩 |
| `baseline.png` | `plot_rollouts` 把静态地图点和场景 0 的 expert xy 轨迹画成图 | 直观看车辆走向 | 摄像头画面、完整交通动画或 GPS 地图服务 |

### CSV 一行是什么

一个 CSV 可以看作普通表格；本实验每行是 **SDC 自车推进一步后的记录**。
第一行是列名，不算一次 step。

| 列名 | 中文与单位 |
| --- | --- |
| `step` | 本轮第几次推进，从 1 到 80 |
| `timestep` | 原场景时间索引，从 11 到 90；不是直接的秒数 |
| `x_m` / `y_m` | 自车全局坐标，单位米，不是图像像素 |
| `accel_m_s2` | 这一转换所用的加速度，单位 m/s² |
| `steering_curvature_m_inv` | 转向曲率，单位 m⁻¹，不是方向盘角度 |
| `log_divergence` | 与同一时刻日志自车位置的距离，单位米 |
| `overlap` | 自车包围盒是否与其它对象重叠的标记 |
| `offroad` | 越界标记 |
| `kinematic_infeasibility` | 运动学约束违规标记 |

先做了 11 帧初始化历史后，reset 处于索引 10；
第一次推进记录索引 11，第 80 次到 90。
动作列对应这一步转换，位置与指标对应转换后的状态，不要错开一帧比较。

### summary 的 mean、max、valid_frames

把 80 行中某项有效指标收集起来：

```text
valid_frames：有多少行指标有效
mean：这些有效值的平均值
max：这些有效值中的最大值
```

如果指标是 0/1 标记，均值可表示标记出现的帧比例，
**不是事故次数，也不是 80 个独立场景的通过率**。
无效指标在 CSV 中为空、汇总中可能为 `null`，不能当成零分或零错误。

## 5. 之后训练才会出现的文件

以下属于 `make waymax-learning` 的完整教学实验，今天继续 baseline
**不会生成这些新文件**。先知道用途，不要求现在全部打开。

| 文件 | 生成者 | 用途 |
| --- | --- | --- |
| `brake-0/` | `Lab.rollout(policy="brake")` | 固定负加速度对照，不是训练样本来源 |
| `action-comparison.json` | `execute_rollouts` | 比较 expert/固定动作产生的位置差 |
| `observation-check.json` | `check_future_observation_independence` | 保留未来日志位置干预检查结果 |
| `training-samples.npz` | `train_policy` | 真正参与训练的特征 X、expert 标签 Y |
| `training.json` | `train_policy` | 训练/验证记录身份、样本数、loss、checkpoint 指纹 |
| `checkpoint.npz` | `train_policy` | 学到的网络参数和训练场景归一化统计 |
| `learned-training/` | `Lab.rollout(policy="learned")` | 学习策略在训练场景中自己控制后的轨迹/指标 |
| `learned-evaluation/` | 同上，另一场景 | 学习策略在开发/验证场景中的表现，不是盲测成绩 |
| `evaluation.json` | `execute_rollouts` | 汇总 expert 和学习策略的表现，保留未验收边界 |
| `action-comparison.png` / `heldout-evaluation.png` | `plot_rollouts` | 地图/轨迹对照图 |
| `reload-reference.npz` | `execute_rollouts` | 给新进程的对照输入、预测和轨迹 |
| `reload-command.json` | `execute_rollouts` | 新 Python 进程重载的实际命令 |
| `reload.log` | `subprocess.run` 的输出归档 | 新进程是否报错，为什么没通过 |
| `reload/` 或 `reload-*/` | `reload_evaluation` | 重载后的环境、记录身份、轨迹及比较结果 |
| `result.json` | `execute_rollouts` 或 `reload_evaluation` | 总结工程流程/重载检查，不能替代驾驶质量判断 |

NPZ 是压缩数值数组格式，不能像 JSON 一样直接用 `cat` 读懂。
后续用 NumPy 按数组名读取，本实验明确设置 `allow_pickle=False`。

## 6. 异常、修改与缺失怎么判断

- `error.txt`：只在主运行捕获到 Python 异常时写出错误回溯。
- `status.json`：可能在中断或强制超时后仍是 RUNNING，应调查，不补成 PASS。
- 主进程完整终端 stdout **没有默认保存为 run.log**；`command.json`
  是启动信息，不是完整终端日志。`reload.log` 只记录重载子进程输出。
- 程序过早退出时，部分文件没有生成是正常的失败现场，不能伪造补齐。
- `artifact-manifest.json` 不做自己的哈希，也不是数字签名。
- `waymax-verify` 验证完整 P1–P5 产物；不要用它要求 inventory/baseline
  拥有训练文件，否则会因为阶段不匹配而失败。
- 历史档案不要手工改；学习笔记写在 `docs/learning/`，新运行使用新目录。

代码入口与讲解分别可从
[操作手册](waymax-learning-lab.md)和[每日学习日志](learning/waymax-30-days/README.md)继续。
