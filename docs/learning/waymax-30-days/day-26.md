# Day 26：缺失文件和路径逃逸负测

## 状态

- 计划日期：2026-12-03。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`FAIL_EXPECTED`。

## 缺失文件测试

在新的临时副本中移走 CSV：

```bash
cd /home/my/workspace/autodrive/my-ad
LAB_RUN=/absolute/path/to/full-run
SCRATCH="$(mktemp -d /tmp/waymax-study.XXXXXX)"
cp -a "$LAB_RUN" "$SCRATCH/run"
mv "$SCRATCH/run/expert-0/trajectory.csv" "$SCRATCH/"
python3 scripts/waymax/verify_learning_run.py "$SCRATCH/run"
```

校验器应拒绝缺失或 manifest hash 不一致。目录逃逸、符号链接和空 manifest
也有对应单元测试，不要对原始目录做这些操作。

## 学习重点

一个程序返回 0，只说明它认为流程结束；证据校验还要检查：

- 必需文件是否存在；
- 文件是否仍在 run 目录内；
- 内容 hash 和大小是否一致；
- 指标是否有足够有效帧。

## 验收

说明为什么“程序能启动”与“结果证据完整”是两个门槛。
