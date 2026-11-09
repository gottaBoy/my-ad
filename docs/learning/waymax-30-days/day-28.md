# Day 28：能力边界和后续模块

## 状态

- 计划日期：2026-12-05。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`；不运行 V-Max 默认训练。

## 现有小实验覆盖

| 能力 | 当前状态 |
| --- | --- |
| 本地 TFRecord 解析 | 已有 CPU 小样本 |
| 状态级 SimulatorState 仿真 | 已跑 2 场景 × 80 步 |
| expert 逆动力学 | 已有 |
| 固定动作因果对照 | 已有 |
| 小型行为克隆 | 已有工程链路 |
| 新进程 checkpoint 重载 | 已有 |
| RGB/LiDAR 原始传感器 | 未覆盖 |
| Reactive NPC | 未覆盖，NPC 使用日志 fallback |
| PPO/SAC/RL 大规模训练 | 未覆盖 |
| CARLA/Vulkan/实车安全 | 明确排除 |

V-Max 文档里可以看到 BC、PPO、SAC 等训练入口，但“代码存在”不等于
它已经适配本实验的本地 Waymax 状态数据、资源边界和验收协议。

## 只读入口

```bash
cd /home/my/workspace/autodrive/my-ad
sed -n '1,220p' ../v-max/docs/2_training_pipeline.md
sed -n '1,180p' ../v-max/docs/1_config.md
```

不要执行 V-Max 默认训练脚本；它不属于本月 CPU 小实验。

## 验收

画三列：已有、未实现、需要适配；至少区分 BC、RL、传感器和状态仿真。
