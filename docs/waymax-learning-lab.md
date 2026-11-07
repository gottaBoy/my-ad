# Waymax CPU 小数据学习手册

对应计划与执行历史：[waymax-learning-plan.md](waymax-learning-plan.md)。
本实验复用已有本地环境，不安装依赖、不下载数据、不申请 GPU。
逐日学习请按[30 天计划](waymax-learning-30-days.md)，
在[每日进度与日志](learning/waymax-30-days/README.md)记录验证结果。

## 1. 学习地图

```text
本地 TFRecord
    -> 官方解析器 -> 两个 SimulatorState
    -> 场景 0：expert -> 当前观测 / expert action -> 行为克隆训练
    -> 场景 0：固定制动对照 -> 验证动作改变轨迹
    -> 场景 1：独立评测 -> expert 与学习策略的轨迹/指标
    -> checkpoint.npz -> 新 Python 进程 -> 相同预测/轨迹核对
```

要区分三个概念：

- **expert** 使用下一帧日志推算动作，相当于有答案的教学基线，不是可部署策略。
- **学习策略** 只使用当前模拟对象和静态地图特征，输出动作后由真实 Waymax
  动力学推进 SDC；不直接把未来 GT 坐标写成预测。
- **NPC** 仍使用日志回放，不会因 SDC 的改变而智能响应；这不是完整响应式交通。

## 2. 固定的本地输入

配置文件：`config/waymax/learning.json`。

| 输入 | 选择 |
| --- | --- |
| Python | `../.venv-waymax/bin/python`，不改安装内容 |
| Waymax 源码 | `../waymax-my`，通过显式路径优先加载并核对 |
| 数据 | `../waymax/waymax/dataloader/testdata/tfrecord_with_routes` |
| 规模 | 前 2 个不同 scenario ID，各最多 32 个对象 |
| 时间 | 11 帧初始化历史，80 次 step，dt=0.1 秒 |
| 划分 | 场景 0 训练，场景 1 评测；不是按同场景相邻帧假装独立数据 |
| 模型 | 当前状态特征 → 32 单元 tanh 隐层 → 2 维 tanh 动作 |
| 训练 | Optax Adam，300 次全批量更新，固定 seed=0 |

`waymax-my` 的选择是有原因的：现有虚拟环境 editable 安装原本指向
`waymax-rl_planner`，其 expert 代码使用当前 JAX 已移除的 `jax.tree_map`。
本实验不 monkey-patch JAX 或修改 fork，而是明确选择已有兼容源码。
不能直接省略本仓库入口，假定 `import waymax` 自动选到同一版。

## 3. 第一步：环境与数据

在当前仓库运行：

```bash
cd /home/my/workspace/autodrive/my-ad
make waymax-check
```

关注输出中的 `RUN_DIR`。每次运行使用新目录，不覆盖历史结果：

```text
artifacts/waymax/learning-<UTC>-<随机后缀>/
```

先阅读：

- `environment.json`：实际解释器、代码路径/revision、版本、CPU 后端与数据哈希。
- `records.json`：两个不同的 scenario ID、record SHA256、字段数量与数组规模。
- `config.json`：本轮实际配置，不依赖以后可能变化的默认值。
- `status.json`：阶段状态、耗时与父进程峰值 RSS。
- `source/`、`code.json`：运行时脚本副本与哈希。

数据文件约 38 MiB，但只解析所选的两个记录，不遍历下载整个 Waymo 数据集。
仓库测试数据不是自己的真实采集数据；如后续用于其它用途，应另行核对许可。

## 4. 第二步：expert 基线

```bash
make waymax-baseline
```

每个场景的 `expert-0/`、`expert-1/` 都包含：

- `trajectory.csv`：每一步的时间索引、SDC 位置、动作与指标。
- `summary.json`：各指标的有效帧数量和汇总值。
- `arrays.npz`：观测特征、expert 标签、标签有效性和 SDC 轨迹。

`baseline.png` 是实际地图与 expert 轨迹。

建议学习顺序：

1. 看配置的 `init_steps=11`，再看 CSV 的 timestep 为什么从 11 开始。
2. 找 `Lab.rollout` 中 `state = self.step(state, action)`：动作在哪里进入动力学。
3. 比较 x/y 与 `log_divergence`，理解 expert 逆动力学与直接复制日志并不相同。
4. 指标必须连同 `valid_frames` 阅读，不能把没有有效数据的 `null` 解释为 0。

## 5. 第三步：动作、训练与完整回路

```bash
make waymax-learning
```

它先重跑基线，然后：

1. 用相同场景/起点改为固定制动动作，验证位置发生变化。
2. 从场景 0 的有效 expert 样本构建监督数据。
3. 用当前观测训练小模型；归一化统计仅取自训练场景。
4. 在场景 0 和独立场景 1 中，以学习策略自身输出的动作推进模拟。
5. 保存 checkpoint；启动新的 Python 进程重载并再次评测场景 1。

额外的观测干预测试会把未来日志 x/y 改动 1000 米，但保持当前模拟状态和
地图不变，检查模型输入特征不变。它只是这组干预的检查，不是所有信息泄漏的证明。

### 动作的单位

Waymax `InvertibleBicycleModel` 的动作是：

```text
[acceleration_m_s2, steering_curvature_m_inv]
```

第二项是转向曲率，不是方向盘角度。默认边界为加速度 ±6 m/s²、
曲率 ±0.3 m⁻¹。小模型输出归一化动作，再恢复到实际单位。
固定制动策略只用于说明动作会改变状态，不是一个安全控制器。

### 当前观测

120 维教学特征包含：

- SDC 当前自身坐标系下的速度和车身尺寸。
- 最近 4 个有效其它对象的相对位置、相对速度及有效标志。
- 16 个最近静态地图点的位置、方向、类型和有效标志。

坐标变换和附近地图筛选复用 Waymax 实现。
本小模型没有完整路线任务、交通灯决策、长期记忆或安全约束，不应期待其
在一个场景的 80 个标签上学到通用驾驶能力。

## 6. 如何读结果

| 文件 | 学习重点 |
| --- | --- |
| `action-comparison.json` / `.png` | 同起点改变动作，轨迹是否真的不同 |
| `observation-check.json` | 未来日志位置干预是否影响当前模型特征 |
| `training.json` | 样本、seed、loss、划分、checkpoint hash |
| `training-samples.npz` | 真正进入训练的特征与标签 |
| `checkpoint.npz` | 不使用 pickle 的参数和归一化数组 |
| `learned-training/` | 训练场景上的实际闭环表现，不只是监督 loss |
| `learned-evaluation/` | 独立场景上的实际闭环表现 |
| `heldout-evaluation.png` | 独立场景中 expert 与学习策略轨迹 |
| `reload.log` / `reload/result.json` | 新进程预测和轨迹能否复现 |
| `result.json` | 工程检查与未验收范围的总览 |
| `artifact-manifest.json` | 本次生成文件的大小和 SHA256，供独立校验 |

四项指标均只汇总 SDC：

| 指标 | 含义 |
| --- | --- |
| `log_divergence` | 与同一时刻日志位置的 L2 距离，单位米 |
| `overlap` | 当前帧发生包围盒重叠的标记；帧均值不是独立事故次数 |
| `offroad` | 当前帧越界标记；帧均值不是场景级通过率 |
| `kinematic_infeasibility` | 运动学约束违规标记 |

**loss 下降不等于闭环轨迹变好。** 尤其需要看独立场景中的偏离、
重叠和越界，即使工程结果为 PASS，策略质量仍可能很差。

本实验的 `PASS` 只验收数据、运行、训练、序列化和重载这条工程链路。
报告固定保留 `policy_quality_accepted=false`，不把“跑通”写成“驾驶安全”。

## 7. 单独重载与测试

```bash
make waymax-evaluate WAYMAX_RUN=/绝对路径/到/某次learning目录
make waymax-verify WAYMAX_RUN=/绝对路径/到/某次learning目录
make test-waymax
```

重载会建立新的 `reload-*` 子目录，不覆盖已有结果。输入版本、checkpoint
哈希或归一化/参数 schema 不匹配时应失败，不静默恢复默认值。
`waymax-verify` 不导入 JAX，只用标准库核对留存文件、输入哈希、训练/评测
划分，并从 CSV 独立重算动作造成的位置差；缺失、篡改和越界文件路径都会失败。
它适用于新增 artifact manifest 后的完整运行；早期探索记录不冒充同级别验收。

CPU 数可进一步降低：

```bash
WAYMAX_CPU_COUNT=1 make waymax-learning
```

默认限制 2 个 CPU affinity、TF/BLAS 线程数，单次命令最长 900 秒。
脚本拒绝超过 4 个 CPU 或超过 1800 秒的覆盖参数，所有输出只写到本仓库
`artifacts/waymax/`。这是有界学习实验，不是通用的大规模训练入口。

RSS 是进程测量，不是整个作业的内存硬限制；新进程重载时父子进程短时共存。
同机仍有资源竞争，不承诺绝对零影响。
