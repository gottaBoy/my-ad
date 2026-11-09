# Day 25：checkpoint 篡改负测

## 状态

- 计划日期：2026-12-02。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`FAIL_EXPECTED`（被破坏副本必须拒绝）。

## 安全做法

只复制完整运行目录，不碰原始档案：

```bash
cd /home/my/workspace/autodrive/my-ad
LAB_RUN=/absolute/path/to/full-run
SCRATCH="$(mktemp -d /tmp/waymax-study.XXXXXX)"
cp -a "$LAB_RUN" "$SCRATCH/run"
printf 'deliberate-test-corruption' >> "$SCRATCH/run/checkpoint.npz"
python3 scripts/waymax/verify_learning_run.py "$SCRATCH/run"
```

预期退出码是 `2`，并报告 hash 或 manifest 不一致。
这里的 FAIL 是测试对象的预期结果；学习任务可以因此 VERIFIED。

## 禁止事项

- 不改原始 checkpoint。
- 不把失败副本重新标为 PASS。
- 不用 `git reset` 或全局清理恢复。

## 验收

解释为什么“文件还能被 NumPy 打开”不等于 checkpoint 仍可信。
