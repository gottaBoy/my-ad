# Day 08：动作真的改变了状态吗

## 状态

- 计划日期：2026-11-15。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS`（工程因果对照）；不能预设策略质量。

## 实验问题

同一个场景 0、同一个 reset 起点，分别运行：

```text
expert：参考动作
brake：固定 [-2.0 m/s², 0.0 m⁻¹]
```

只要两条 SDC 轨迹不同，就说明动作已经接入动力学并改变了状态。
这不是在证明 brake 安全，更不是训练。

## 执行命令

```bash
cd /home/my/workspace/autodrive/my-ad
make waymax-learning
# 复制终端打印的 RUN_DIR，不要猜目录
LAB_RUN=/absolute/path/to/that/run
cat "$LAB_RUN/action-comparison.json"
```

现有完整档案已经有一份历史结果：

```text
max_position_difference_m ≈ 53.690357
```

它说明动作影响明显。brake 还有 overlap 帧，正好提醒我们：
“轨迹不同”与“控制安全”是两个问题。

## 要核对的控制变量

- 同一个 `scenario_id`。
- 同一个 `reset` 起点。
- 同一个 `dt`、源码、配置和 CPU 约束。
- 只改变 action policy。

## 验收

解释 `action-comparison.json` 的 `PASS` 证明什么、不证明什么。
如果 expert 和 brake 从不同 timestep 开始，为什么结论不可靠？
