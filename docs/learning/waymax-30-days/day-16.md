# Day 16：只改变隐藏层容量

## 状态

- 计划日期：2026-11-23。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS` 或 `FAIL_EXPECTED`。

## 实验

默认隐藏单元为 32，本日使用 16：

```bash
cd /home/my/workspace/autodrive/my-ad
bash scripts/waymax/run-cpu.sh --stage all \
  --config config/waymax/experiments/hidden-16.json
```

参数量从默认的 3938 变为：

```text
120×16+16 + 16×2+2 = 1970
```

容量更小可能拟合不足；但参数更少不自动代表闭环更安全。
比较时只允许改变 `hidden_units`。

## 验收

写出两种模型参数量，说明为什么“参数更少”不能直接推出“表现更好”。
