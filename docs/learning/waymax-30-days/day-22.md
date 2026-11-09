# Day 22：冻结复现条件

## 状态

- 计划日期：2026-11-29。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`。

## 复现清单

完整运行至少要固定：

```text
输入文件路径 + SHA256
训练/验证 scenario ID
配置 JSON + SHA256
Waymax 源码 revision
my-ad revision
Python 与依赖版本
JAX backend=cpu
TensorFlow visible GPU=[]
CPU affinity
```

读取已有档案：

```bash
cd /home/my/workspace/autodrive/my-ad
RUN=artifacts/waymax/learning-20261107T105737Z-_rn5s941
cat "$RUN/environment.json"
cat "$RUN/code.json"
cat "$RUN/config.json"
git rev-parse HEAD
```

注意：运行时记录的 project revision 可能与今天工作区 HEAD 不同。
旧证据按它自己的身份解释，不能事后用新提交覆盖。

## 验收

把“git 中可追踪的文件”和“只在本机 artifacts 中的运行输出”分成两列。
