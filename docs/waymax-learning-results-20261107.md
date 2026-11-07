# Waymax 小数据实测记录

日期：2026-11-07。计划 P0-P6 已完成，**仅工程链路通过，策略质量未通过**。

## 可直接复习的产物

正式学习运行目录：

`artifacts/waymax/learning-20261107T105737Z-_rn5s941/`

- 完整计划：[waymax-learning-plan.md](waymax-learning-plan.md)
- 逐步讲解：[waymax-learning-lab.md](waymax-learning-lab.md)
- [结果 JSON](../artifacts/waymax/learning-20261107T105737Z-_rn5s941/result.json)
- [动作对照图](../artifacts/waymax/learning-20261107T105737Z-_rn5s941/action-comparison.png)
- [独立场景对照图](../artifacts/waymax/learning-20261107T105737Z-_rn5s941/heldout-evaluation.png)

`artifacts/` 被 git 忽略，图、checkpoint 和原始输出留在本机；
文档保留命令、路径和实测摘要，不假装这些二进制已经进入 git。

## 实验规格

| 项 | 实际选择 |
| --- | --- |
| 后端 | JAX CPU，TensorFlow 可见 GPU 为空 |
| CPU affinity | 2 个处理器 |
| 数据 | 本地 `tfrecord_with_routes`，40,073,764 bytes，只读取前 2 个记录 |
| 训练场景 | `d27157fded0ddd5e` |
| 独立评测场景 | `543908aa309f5a0b` |
| 每场景 | 32 个对象，11 帧初始化历史，80 次 step |
| 策略 | expert、固定制动、120 维观测的单隐层行为克隆模型 |
| 训练 | 80 个有效样本，32 个隐层单元，300 次 Adam 更新，seed=0 |
| 版本 | JAX/jaxlib 0.11.2、TF 2.21.0、Flax 0.12.10、Optax 0.2.8 |
| 用时 | 34.227 秒，包含新进程重载与再次 rollout |
| 峰值 RSS | 父进程 917.75 MiB；不是父子作业总内存硬上限 |
| checkpoint | 约 17 KiB，NPZ，不使用 pickle |

输入文件的 SHA256、完整版本、代码 revision 和执行脚本哈希以
`environment.json`、`code.json` 和 `artifact-manifest.json` 为准。
选定源码为现有 `waymax-my`，没有改写已安装的 editable 注册。

## 工程结果

| 检查 | 结果 |
| --- | --- |
| 两个不同的本地 scenario ID | PASS |
| 两个场景 expert 各 80 步 | PASS；每项 SDC 指标均有 80 有效帧 |
| 固定制动改变状态 | PASS；最大位置差约 53.69036 m |
| 修改未来日志位置后，当前模型特征不变 | PASS；特征最大差为 0 |
| 最小训练 loss 下降 | `0.108001925` → `0.0000787876` |
| 新进程重载预测 | PASS；最大差为 0 |
| 新进程重载后完整评测轨迹 | PASS；最大差为 0 m |
| 两轮 checkpoint 与五份轨迹 CSV | SHA256 相同 |
| 留存文件与独立 CSV 算式校验 | PASS，46 个文件 |
| 专项单元测试 | 17 tests PASS |
| 与既有学习/NAVSIM/Scenario 合约相关回归 | 29 tests PASS，包含上述 17 项 |

checkpoint SHA256：

```text
629ee41838ef94933e8d94b546e8a344e512d472d35b57e9789bf9c755f8bfa5
```

重复运行对照位于 `artifacts/waymax/repeatability-20261107.json`。
这只证明本机、相同输入/配置下两次输出一致，不承诺所有平台数值逐位相同。

## 为什么不能说“策略已经学会开车”

所有数字都是 SDC 的 80 帧汇总，非事故次数或大规模场景通过率：

| 策略/场景 | 平均日志位置差 | 越界帧比例 | 重叠帧比例 |
| --- | ---: | ---: | ---: |
| expert / 训练场景 | 约 0.01642 m | 0% | 0% |
| 学习策略 / 训练场景 | 约 4.43935 m | 18.75% | 0% |
| expert / 独立场景 | 约 0.01426 m | 0% | 0% |
| 学习策略 / 独立场景 | 约 15.74777 m | 65% | 0% |

这是本次最值得学习的结果：**监督 loss 很低，rollout 仍可明显偏离**。

训练仅使用一个场景的 expert 状态；运行时模型会看到由自己动作产生的新状态。
再加上模型很小、观测不包含完整导航任务等限制，不能据此期待可靠驾驶。
以上是需要继续研究的因素，不是已通过因果实验确认的唯一误差来源。

碰撞标记为零也不代表安全：本次模型已有明显越界，NPC 仍按日志回放。
因此报告固定保留 `engineering_checks_only=true`、
`policy_quality_accepted=false`。

## 过程中保留的失败

首次 P2 使用已有 editable fork `waymax-rl_planner`，失败于：

```text
AttributeError: module 'jax' has no attribute 'tree_map'
```

保留目录：`artifacts/waymax/learning-20261107T104337Z-0dl52ez7/`。
选择旁边已经采用 `jax.tree.map` 的标准源码 `waymax-my` 后通过。
没有临时补 `jax.tree_map` 别名、修改 fork、安装新依赖或覆盖历史图片。

## 重跑顺序

```bash
cd /home/my/workspace/autodrive/my-ad
make waymax-check
make waymax-baseline
make waymax-learning
make test-waymax
make waymax-verify WAYMAX_RUN=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-20261107T105737Z-_rn5s941
```

从 `trajectory.csv` 开始读，结合两张图，再读训练 loss 和独立场景指标。
不要跳过独立场景，只展示训练 loss。

本轮未覆盖：V-Max PPO/SAC、GPU 加速、真实相机/LiDAR、响应式 NPC、
Autoware 适配、完整数据平台和实车能力。这些是后续独立工作，不属于本轮 PASS。
