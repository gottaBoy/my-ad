# Day 14：新 Python 进程能否还原同一个模型

## 状态

- 计划日期：2026-11-21。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS`（重载一致性），不代表策略质量。

## 执行

```bash
cd /home/my/workspace/autodrive/my-ad
LAB_RUN=/absolute/path/to/full-run
make waymax-evaluate WAYMAX_RUN="$LAB_RUN"
cat "$LAB_RUN/reload/result.json"
```

重载过程会在新的 Python 进程中：

1. 重新检查 CPU 环境、代码、版本和数据 hash。
2. 读取 checkpoint 的参数与归一化信息。
3. 用保存的 features 重算 prediction。
4. 在验证场景重新 rollout。
5. 比较预测和轨迹误差。

已有档案结果：

```text
fresh_process_pid 与原进程不同
prediction_max_abs_difference = 0
trajectory_max_abs_difference_m = 0
status = PASS
```

这说明保存/加载可复现。一个“稳定地跑偏”的模型也可能重载完全一致，
所以一致性不是驾驶效果。

## 验收

区分：

- reproducibility：同一证据能否重现同一输出；
- policy quality：轨迹是否接近参考、是否越界；
- safety：是否满足真实车辆安全要求。

本地实验只覆盖前两项中的工程一致性和小样本指标，不覆盖实车安全。
