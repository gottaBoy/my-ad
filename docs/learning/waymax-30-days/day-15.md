# Day 15：只改变训练更新次数

## 状态

- 计划日期：2026-11-22。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS` 或 `FAIL_EXPECTED`，由真实结果决定。

## 控制变量

对照默认 `training_updates=300`，只改为 `100`：

```bash
cd /home/my/workspace/autodrive/my-ad
bash scripts/waymax/run-cpu.sh --stage all \
  --config config/waymax/experiments/updates-100.json
```

记录终端打印的真实 `RUN_DIR`，不要覆盖默认档案。

## 比较表

| 项目 | 默认 | updates-100 |
| --- | --- | --- |
| 配置 hash | 待填写 | 待填写 |
| 初始/最终 loss | 已有 | 待运行 |
| 训练场景偏差 | 已有 | 待运行 |
| 验证场景偏差 | 已有 | 待运行 |
| offroad 有效帧比例 | 已有 | 待运行 |

不能预设 300 一定比 100 好。更多更新可能降低训练 loss，也可能加重过拟合
或没有改善闭环轨迹。

## 验收

确认配置只改变 `training_updates`，其余字段完全相同，并保留真实 RUN_DIR。
