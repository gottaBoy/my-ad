# Week 12：面试、简历和投递

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`

## 项目讲解模板

用 90 秒回答：

```text
我做了什么
输入是什么
我负责哪一部分
如何验证
最重要的失败是什么
下一步怎么改
```

## 必答项目问题

1. 为什么 Waymax 不等于 CARLA？
2. 为什么只控制 SDC，NPC 用日志 fallback？
3. 为什么训练 loss 很低但闭环轨迹很差？
4. 如何保证 reset 起点一致？
5. checkpoint 为什么需要 normalization 和 hash？
6. C++ 接口如何表达单位和非法输入？
7. ROS 2 中 timestamp 和 frame_id 为什么重要？
8. 如果没有 GPU，哪些能力仍然可以验证？

## 算法题范围

- 数组、哈希、排序、二分；
- 队列、栈、图；
- 时间/空间复杂度；
- C++ STL、引用、移动；
- 基本线程和锁。

## 简历项目句式

不要写：

> 精通自动驾驶、精通 Waymax。

可以写：

> Built a CPU-only Waymax state-simulation lab with expert/brake/behavior-cloning
> comparisons, checkpoint reload verification, per-frame metrics, and failure
> artifact validation.

如果补强项目完成，再写：

> Implemented a tested C++17 bicycle-model/controller pipeline with explicit
> units, bounded actions, deterministic replay, and negative-input checks.

## 投递顺序

1. 仿真/评测实习；
2. 规划控制实习或初级岗位；
3. 数据/回放工具开发；
4. 具备作品和面试反馈后，再扩大岗位范围。

## 最终验收

- 能现场构建一个小项目；
- 能现场解释一次失败；
- 能说出未实现的边界；
- 能给出复现命令；
- 不把工程 PASS 说成真实车辆安全。
