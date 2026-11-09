# 就业强化课程包

主入口：[从 Waymax 到初级岗位](../autodrive-job-track.md)。

本目录把 12 周路线拆成可执行课件。每周都包含：

```text
目标 -> 关键词 -> 阅读 -> 动手任务 -> 验收 -> 作品集产物
```

状态不自动完成。每周开始时为 `NOT_STARTED`，实际执行后再记录
命令、输出、测试和未知项。

## 12 周索引

| 周 | 主题 | 课件 | 主要产物 |
| --- | --- | --- | --- |
| 01 | C++/Linux/CMake | [week-01](week-01.md) | `cpp-basics` |
| 02 | 坐标与车辆运动学 | [week-02](week-02.md) | bicycle model |
| 03 | PID/Pure Pursuit/Stanley | [week-03](week-03.md) | controller |
| 04 | 路径、轨迹、碰撞 | [week-04](week-04.md) | planner |
| 05 | ROS 2 节点通信 | [week-05](week-05.md) | state/controller/metrics nodes |
| 06 | Waymax 与 C++ 管线 | [week-06](week-06.md) | CSV replay bridge |
| 07 | PyTorch 训练工程 | [week-07](week-07.md) | tiny policy checkpoint |
| 08 | 闭环错误分析 | [week-08](week-08.md) | failure report |
| 09 | 性能与调试 | [week-09](week-09.md) | benchmark report |
| 10 | 故障边界与负测 | [week-10](week-10.md) | rejection tests |
| 11 | 作品集整理 | [week-11](week-11.md) | project README |
| 12 | 面试与投递 | [week-12](week-12.md) | resume/interview pack |

## 建议节奏

每周 5 次、每次 45–90 分钟：

1. 读课件和相关源码。
2. 完成一个最小实现。
3. 写至少一个正常测试和一个负测试。
4. 保存运行命令、版本、输入和结果。
5. 用自己的话写周总结。

## 资源边界

- 默认 CPU-only。
- 不下载大数据。
- 不启动 CARLA、Vulkan 或默认 GPU 训练。
- 不修改 `../waymax`、`../waymax-my`、`../v-max` 和系统驱动。
- ROS 2 只有在本机已经存在并明确选择后才运行；否则先完成消息接口设计
  和离线模拟，不把未执行写成已完成。

## 最低毕业线

完成 week-01 至 week-06 后，可以重点准备仿真、评测、规划控制实习/初级岗位。
完成 week-07 至 week-12 后，形成更完整的初级岗位作品集。
这不等于精通智能驾驶，也不等于真实车辆安全资质。

## 记录模板

每周记录至少包括：

```text
日期
学习状态
代码提交号
执行命令
退出码
测试结果
观察事实
当前推断
未知项
作品集路径
下一周入口
```

文件：[周记录模板](TEMPLATE.md)。
