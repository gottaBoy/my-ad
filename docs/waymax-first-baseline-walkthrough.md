# 继续第一课：让参考车辆走起来，再读它的记录

学习日期：2026-11-07（America/Los_Angeles）。
本次由助手执行 `make waymax-baseline`；不是学习者已经独立完成或解释通过。

## 1. 这一小步比上一步多做什么

上一步 `make waymax-check` 只是检查设备、代码和两条资料的身份。
这一步是：

```text
打开同样两条记录
    -> 构造 Waymax SimulatorState
    -> 初始化历史
    -> expert 根据日志反推参考动作
    -> Waymax 动力学计算下一状态
    -> 记录自车位置与指标
    -> 重复 80 步
```

两个场景分别从自己的起点开始，**没有训练小模型**。
expert 是能参考下一帧日志的教学基线，不是已经学好的驾驶模型。

## 2. 本次运行与文件

实际目录：

`artifacts/waymax/learning-20261107T163242Z-3nn37_xl/`

终端输出：

```text
P1 PASS: backend=cpu, source=/home/my/workspace/autodrive/waymax-my, fixture=40073764 bytes
P1 PASS: 2 distinct local records
rollout expert-0: expert, 80 steps
rollout expert-1: expert, 80 steps
RUN_DIR=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-20261107T163242Z-3nn37_xl
```

这里真正新增的是两行 `rollout ... 80 steps`：
它们分别说明两个场景完成了 80 次状态推进，不是 80 次模型训练。

本次 JAX 后端为 CPU，TF 可见 GPU 为 `[]`，CPU affinity `[0, 1]`。
主运行耗时 12.028 秒，父进程峰值 RSS 870.16 MiB；
80 步 × 0.1 秒对应 8 秒模拟时间，和计算耗时不同。

新目录自动带有 `FILES.md` 和 `file-guide.json`。
完整文件来源/用途见[文件说明](waymax-files-explained.md)；
每个新运行目录的 FILES.md 只列本次实际文件与收尾清单，不把训练文件列为必需。
本次盘点与此前输入文件的 SHA256 一致，没有修改原数据。

## 3. 先读三样，不必读整个目录

```bash
cd /home/my/workspace/autodrive/my-ad
BASELINE_RUN=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-20261107T163242Z-3nn37_xl
cat "$BASELINE_RUN/FILES.md"
cat "$BASELINE_RUN/expert-0/summary.json"
head -n 4 "$BASELINE_RUN/expert-0/trajectory.csv"
```

这几条只是读文件，不重新运行仿真。
再打开该目录的 `baseline.png` 看俯视轨迹图，不用先打开 NPZ。

注意：前一份 `...T152431Z-ym466a_w` 是盘点档案；
这一份 `...T163242Z-3nn37_xl` 才是本次基线仿真档案，两个目录不要混用。

## 4. 先把 CSV 看成普通表格

下面直接摘录本次场景 0 的第一、第二和最后一步，显示时进行了四舍五入：

| 本轮 step | 场景 timestep | x_m | y_m | log_divergence |
| --- | --- | --- | --- | --- |
| 1 | 11 | 1178.631 | -1846.522 | 0.002132 m |
| 2 | 12 | 1177.568 | -1846.657 | 0.003440 m |
| 80 | 90 | 1099.803 | -1854.802 | 0.028932 m |

逐项读：

1. `step=1` 是本轮第一次推进，`step=80` 是第 80 次推进。
2. `timestep=11` 是原场景索引，不是第 11 秒。前面已经填了初始化历史。
3. `x_m`、`y_m` 是地图坐标，单位米；它们在变，说明自车位置在变。
4. x 变小不自动表示倒车，y 为负也不表示错误；要结合地图方向和车辆朝向，
   不能只看坐标的正负号判断运动。
5. `log_divergence` 是模拟自车和同一时刻日志自车的位置差。
   第一步约 0.002132 m，是约 2.13 mm；不是 0.002132 的“成功率”。

CSV 中动作列记录这次推进用的控制量；位置和指标是推进之后的结果。
你可以把一行读成：

> 做了第 N 次动作后，自车来到这个位置，与参考记录差了这么多。

## 5. summary 是怎样从 80 行得到的

本次场景 0：

| 字段 | 实际值 | 中文解释 |
| --- | --- | --- |
| `policy` | `"expert"` | 本次使用参考策略，不是学习模型 |
| `steps` | `80` | 记录了 80 次推进 |
| `initial_timestep` | `10` | reset 完成时所在索引 |
| `final_timestep` | `90` | 推进结束索引 |
| `is_done` | `true` | 到达该记录的时间结尾，不是驾驶任务评分 PASS |
| `log_divergence.valid_frames` | `80` | 80 帧的位置差有效 |
| `log_divergence.mean` | 约 `0.016418` m | 80 个位置差的平均值，约 1.64 cm |
| `offroad.mean` | `0` | 本次 80 帧中没有标记越界 |
| `overlap.mean` | `0` | 本次 80 帧中没有标记自车包围盒重叠 |

场景 1 的 expert 同样完成 80 步、各指标 80 有效帧，
平均日志位置差约 0.014255 m。

本次额外用标准库重算了两份 CSV 的行数、step/timestep 和各指标均值，
并核对 21 个留存文件的哈希与 FILES.md 链接，结果通过。
这些是本次基线的检查，不是要求完整训练校验器接受 baseline-only 目录。

**这只能证明参考策略基线可以在这两条小记录上运行。**
它不证明通用驾驶安全，不证明学习模型表现，也不证明 NPC 会智能响应自车。

## 6. 本次先停在这里

这次没有生成新 `checkpoint.npz`、`training.json` 或学习策略轨迹，
这是正常的 baseline 阶段，不是漏文件。

接下来先尝试说出：

- `trajectory.csv` 一行代表什么？
- `steps=80` 和 `timestep=90` 为什么不矛盾？
- `log_divergence.mean` 用什么单位？
- 本次为什么还不能说“模型训练成功”？

答案可以从上面的实际表格找到，不要求先阅读全部源代码。
理解了轨迹和摘要，下一小步才是“同一起点换成固定动作，看轨迹是否改变”。
