# Day 07：第一周复盘，能不能讲完整条链路

## 状态

- 计划日期：2026-11-14。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`。复用已有 P1/P2 档案，不自动重跑。

## 一张图讲完整流程

```text
TFRecord
  -> 官方解析器
  -> SimulatorState
  -> reset（历史 0..10，当前 timestep=10）
  -> expert 产生动作
  -> bicycle dynamics
  -> step 到 timestep=11..90
  -> trajectory.csv + summary.json
  -> metrics（米/帧/有效性）
```

第一周不要求训练模型。先能解释：

- 数据提供“原来发生了什么”。
- `reset` 提供可重复起点。
- action 是 `[加速度, 转向曲率]`。
- dynamics 计算下一状态。
- metric 衡量结果，但不是安全认证。

## 复盘命令

```bash
cd /home/my/workspace/autodrive/my-ad
cat artifacts/waymax/learning-20261107T163242Z-3nn37_xl/expert-0/summary.json
head -n 3 artifacts/waymax/learning-20261107T163242Z-3nn37_xl/expert-0/trajectory.csv
```

不要把 `learning-20261107T152431Z-ym466a_w`（inventory）和
`learning-20261107T163242Z-3nn37_xl`（baseline）混为一谈。

## G1 门槛

不用看文档，尝试用 2 分钟说明：

1. 一个 record、一个 scene、一个 timestep 的区别。
2. 为什么 reset 后是 10 而不是 0。
3. expert 为什么不是部署策略。
4. 为什么 80 步是 8 秒模拟时间。
5. 为什么工程 PASS 不等于策略 PASS。

如果第 3–5 点说不清，停在第一周复习，不进入调参。这里的“停”是学习
顺序控制，不是实验失败。
