# Day 24：独立校验保存的证据

## 状态

- 计划日期：2026-12-01。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS`。

## 执行

```bash
cd /home/my/workspace/autodrive/my-ad
LAB_RUN=/absolute/path/to/full-run
make waymax-verify WAYMAX_RUN="$LAB_RUN"
```

校验器不重新训练，也不需要 JAX；它读取 manifest、hash、JSON、CSV，
检查 CPU 记录、阶段状态、有效帧、动作干预和新进程 reload。

baseline-only 目录缺少 P3–P5，不能拿它要求完整校验；这是阶段边界，不是
baseline 失败。

## 验收

列出校验器至少检查的三类东西，并说明为什么 manifest 不是数字签名。
