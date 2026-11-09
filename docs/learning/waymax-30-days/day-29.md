# Day 29：独立小项目彩排

## 状态

- 计划日期：2026-12-06。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`，执行后再填写报告。

## 推荐协议

选择默认配置，按顺序执行：

```text
1. CPU inventory
2. baseline 或完整 learning
3. 记录真实 RUN_DIR
4. 读取 environment/records/config
5. 读取 summary/trajectory/evaluation
6. 新进程 reload
7. 独立 verify
8. 保存命令、hash、指标和负面结果
```

最终报告至少写清：

- 用了哪一个本地输入；
- 哪个 scene 训练、哪个 scene 评估；
- action、reset、step、metric 的定义；
- 工程链路是否通过；
- 策略质量是否接受；
- 没有做什么（GPU、传感器、CARLA、盲测）。

没有运行的部分明确写 `NOT_RUN`，不要用“计划执行”代替证据。

## 验收

让另一个人只看报告，就能定位输入 hash、源码 revision、配置和 RUN_DIR。
