# Day 27：测量 CPU 资源，而不是猜性能

## 状态

- 计划日期：2026-12-04。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS`（资源边界）；不承诺加核一定更快。

## 对照

默认使用 2 个 CPU affinity；本日改成 1：

```bash
cd /home/my/workspace/autodrive/my-ad
WAYMAX_CPU_COUNT=1 make waymax-learning
```

记录两次运行的：

- `environment.json.cpu_affinity`；
- `status.json.seconds`；
- `status.json.peak_rss_mib`；
- CPU backend 和可见 GPU。

`peak_rss_mib` 是父进程观察到的峰值，不等于整个系统所有进程的总内存。
耗时差异也可能来自 JIT、文件缓存和系统负载，不能仅凭一次运行下结论。

## 验收

证明两次都满足 `jax_backend=cpu`、`tensorflow_visible_gpus=[]`，
并解释为什么 CPU 数量与模拟时间不是同一个概念。
