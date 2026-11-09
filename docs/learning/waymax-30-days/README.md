# Waymax 每日学习日志

课程：[30 天计划](../../waymax-learning-30-days.md)。
记录格式：[每日模板](TEMPLATE.md)。
已完成的环境准备：[2026-11-07 准备记录](preparation-20261107.md)。
就业补强教程：[从 Waymax 到初级岗位](../../learning/autodrive-job-track.md)。

**准备实验已完成；Day 01、Day 03、Day 04 和 Day 05 已开始讨论，理解待核对。
Day 02 课单已交付但尚未收到学习者验收记录；Day 06–30 课单已建立，
但均未开始。任何课单存在都不等于学习者已完成。**
建议日期可顺延；实际日期和证据满足后再更新状态，不能按日历自动打勾。

## 状态规则

- `NOT_STARTED`：当天尚未学习/执行。
- `IN_PROGRESS`：已经开始，尚未满足当天门槛。
- `VERIFIED`：当天要求的阅读解释、操作与证据均已检查。
- `REVIEW_REQUIRED`：存在需要定位的失败或未知项，不能继续升级结论。

预期负测中，实验可以 `FAIL_EXPECTED`，同时学习任务为 `VERIFIED`；
两种状态必须在每日记录中分开写。

## 进度表

`day-NN.md` 是约定文件名；尚未创建的文件不放失效链接。
建立并填写日志后，将“记录”列更新为该文件的链接。

| Day | 建议日期 | 实际日期 | 状态 | 记录 | 证据目录 |
| --- | --- | --- | --- | --- | --- |
| 01 | 2026-11-08 | 2026-11-07 | IN_PROGRESS | [概念、文件与基线演示](day-01.md) | 学习者 P1 `...T152431Z-ym466a_w`；助手 P2 `...T163242Z-3nn37_xl`；理解待核对 |
| 02 | 2026-11-09 | - | NOT_STARTED | [待学课单：代码身份](day-02.md) | 复用已有 P1，不新增运行 |
| 03 | 2026-11-10 | 2026-11-08 | IN_PROGRESS | [TFRecord、场景与数量](day-03.md) | 复用 P1；助手只读前两条原始记录，无新运行目录 |
| 04 | 2026-11-11 | 2026-11-08 | IN_PROGRESS | [reset、step 与时间轴](day-04.md) | 复用既有 baseline 首尾 CSV；无新仿真 |
| 05 | 2026-11-12 | 2026-11-08 | IN_PROGRESS | [动作、单位与边界](day-05.md) | 复用 baseline CSV 和动力学源码；无新仿真 |
| 06 | 2026-11-13 | - | NOT_STARTED | [指标与有效帧](day-06.md) | 复用 baseline；未新增运行 |
| 07 | 2026-11-14 | - | NOT_STARTED | [第一周复盘](day-07.md) | G1 尚未验收 |
| 08 | 2026-11-15 | - | NOT_STARTED | [动作因果对照](day-08.md) | 完整运行要求保留；未独立执行 |
| 09 | 2026-11-16 | - | NOT_STARTED | [观测与未来信息](day-09.md) | 复用已有 observation-check |
| 10 | 2026-11-17 | - | NOT_STARTED | [训练/验证划分](day-10.md) | 复用已有 training.json |
| 11 | 2026-11-18 | - | NOT_STARTED | [TinyPolicy 前向](day-11.md) | 只读源码 |
| 12 | 2026-11-19 | - | NOT_STARTED | [loss 与梯度更新](day-12.md) | 复用已有 training.json |
| 13 | 2026-11-20 | - | NOT_STARTED | [checkpoint 内容](day-13.md) | 复用已有 checkpoint |
| 14 | 2026-11-21 | - | NOT_STARTED | [新进程重载](day-14.md) | reload 尚未由学习者执行 |
| 15 | 2026-11-22 | - | NOT_STARTED | [更新次数对照](day-15.md) | 配置已备；实验 NOT_RUN |
| 16 | 2026-11-23 | - | NOT_STARTED | [模型容量对照](day-16.md) | 配置已备；实验 NOT_RUN |
| 17 | 2026-11-24 | - | NOT_STARTED | [学习率对照](day-17.md) | 配置已备；实验 NOT_RUN |
| 18 | 2026-11-25 | - | NOT_STARTED | [随机种子对照](day-18.md) | 配置已备；实验 NOT_RUN |
| 19 | 2026-11-26 | - | NOT_STARTED | [loss 与闭环偏差](day-19.md) | 复用已有完整档案 |
| 20 | 2026-11-27 | - | NOT_STARTED | [失败帧定位](day-20.md) | 复用已有 learned-evaluation |
| 21 | 2026-11-28 | - | NOT_STARTED | [第三周矩阵复盘](day-21.md) | 四个对照尚未执行 |
| 22 | 2026-11-29 | - | NOT_STARTED | [冻结复现条件](day-22.md) | 只读已有身份文件 |
| 23 | 2026-11-30 | - | NOT_STARTED | [重复性验证](day-23.md) | 尚未新增重复运行 |
| 24 | 2026-12-01 | - | NOT_STARTED | [独立证据校验](day-24.md) | 完整运行校验待执行 |
| 25 | 2026-12-02 | - | NOT_STARTED | [checkpoint 篡改负测](day-25.md) | 只允许临时副本 |
| 26 | 2026-12-03 | - | NOT_STARTED | [缺失文件负测](day-26.md) | 只允许临时副本 |
| 27 | 2026-12-04 | - | NOT_STARTED | [CPU 资源测量](day-27.md) | 对照尚未执行 |
| 28 | 2026-12-05 | - | NOT_STARTED | [能力边界](day-28.md) | 只读 V-Max 文档 |
| 29 | 2026-12-06 | - | NOT_STARTED | [独立项目彩排](day-29.md) | 尚未开始 |
| 30 | 2026-12-07 | - | NOT_STARTED | [最终演示与验收](day-30.md) | 尚未开始 |

## 每周交付

| 周 | 门槛 | 记录文件 | 当前状态 |
| --- | --- | --- | --- |
| 第 1 周 | G1：数据、reset、动作、指标解释 | `week-01.md` | NOT_STARTED |
| 第 2 周 | G2：观测、监督训练与重载解释 | `week-02.md` | NOT_STARTED |
| 第 3 周 | G3：单变量实验与负面结果 | `week-03.md` | NOT_STARTED |
| 第 4 周 | G4：复现、故障验证与最终演示 | `week-04.md`、`final-report.md` | NOT_STARTED |

日志与小配置可以进入 git；`artifacts/` 中的原始输出仍留在本机。
报告中引用明确 RUN_DIR、输入/代码/配置 hash 和提交号，不用“最新运行”代替。
