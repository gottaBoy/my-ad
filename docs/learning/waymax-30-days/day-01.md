# Day 01：先分清数据、仿真和学习实验

## 身份与状态

- 计划日期：2026-11-08。
- 实际开始日期：2026-11-07，提前开始概念学习。
- 学习时长：未测量。
- 学习状态：`IN_PROGRESS`。
- 实验预期：P1 盘点检查与专项测试通过；不执行车辆仿真或训练。
- 实验实际：学习者已执行 `make waymax-check` 和 `make test-waymax`；
  P1 `PASS`，26 项测试 `OK`。两条命令未单独粘贴 `$?`，不虚构退出码采集记录。
- 仓库基线：`139a317`；本次概念文档为基线后的新增修改。
- 新 RUN_DIR：
  `artifacts/waymax/learning-20261107T152431Z-ym466a_w/`。
  这是学习者本次盘点结果，不是此前准备阶段的仿真运行。

## 本次讨论了什么

1. Waymo 数据、Waymax、我们写的实验，各自负责什么。
2. TFRecord、SimulatorState、场景 0/1、expert、观测、动作分别是什么。
3. “制作监督样本”“动作干预对照”“独立场景验证”“保存/重载”为何要分开。
4. 为什么 loss 很低、保存/重载正确，模型仍然可以越界。
5. 中英文术语如何与一个具体的交通例子对应。

学习者反馈仍然缺少上下文、概念难理解。本记录只证明讨论与文档补充发生，
**不证明学习者已掌握，也不自动记作 VERIFIED**。

## 目前的讲解材料

- [入门图解与中英文词表](../../waymax-concepts-illustrated.md)
- [操作手册](../../waymax-learning-lab.md)
- [准备阶段实际结果](../../waymax-learning-results-20261107.md)
- [本次终端输出逐行解读](../../waymax-reading-first-output.md)
- [文件从哪里来、有什么用](../../waymax-files-explained.md)
- [继续：第一轮基线轨迹讲解](../../waymax-first-baseline-walkthrough.md)

图解包含两张静态教学图：

- 数据记录表、计算沙盘、练习司机各自负责什么。
- 动作有效性、模型表现、保存/恢复三组实验各回答什么问题。

示意中的车速、位置和道路是讲解构造；准备阶段的 loss、位置偏离、越界比例
明确标为既有本地实测。两种数字不混用。

## 需要学习者用自己的话回答

以下暂未收到独立回答，不预填成功：

| 问题 | 自己的解释 | 状态 |
| --- | --- | --- |
| 谁保存过去记录，谁计算下一状态，谁选择动作？ | 待填写 | NOT_VERIFIED |
| 场景 0/1 是两个时刻、两辆车，还是两段记录？ | 待填写 | NOT_VERIFIED |
| expert 制作标签可用下一帧，为什么模型输入不能直接用？ | 待填写 | NOT_VERIFIED |
| 80 次 step 与 300 次参数更新有什么区别？ | 待填写 | NOT_VERIFIED |
| 重载一致但车仍越界，说明哪项通过、哪项未通过？ | 待填写 | NOT_VERIFIED |

## 本次实际操作与复核

学习者粘贴了实际执行的两条命令与终端输出：

```bash
cd /home/my/workspace/autodrive/my-ad
make waymax-check
make test-waymax
```

本地结果 JSON 已复核：

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| JAX 后端 | CPU，device `cpu:0` | 本次 `environment.json` |
| TensorFlow 可见 GPU | 空列表 `[]` | 本次 `environment.json` |
| CPU affinity | `[0, 1]` | 本次 `environment.json` |
| Waymax 来源 | `waymax-my`，revision `a64dfec9be8576b60d9cecc94f406d9812d4a7d0` | 本次 `environment.json` |
| 本地输入 | 40,073,764 bytes | 本次 `environment.json` |
| 不同场景 ID | `d27157fded0ddd5e` / `543908aa309f5a0b` | 本次 `records.json` |
| P1 结果 | `PASS`，stage `inventory` | 本次 `status.json` |
| 检查耗时与父进程 RSS | 2.859 秒 / 674.43 MiB | 本次 `status.json` |
| 专项测试 | 26 tests，0.072 秒，`OK` | 学习者粘贴的终端输出，非新的驾驶试验 |

上述学习者两条命令做的是环境/数据盘点和保护规则测试，尚未执行 baseline/all。
没有新车辆轨迹、训练 loss 或 checkpoint，不能将此前准备结果冒充本次结果。

## 后续讨论与助手演示

学习者已在后续消息中指出：

```json
"jax_backend": "cpu",
"tensorflow_visible_gpus": []
```

这证明已找到相关字段；没有将其它概念题自动改为已掌握。
应要求把 CPU、空 GPU 列表与“这次运行阶段”联系起来解释。

根据“补文件说明，然后继续”的请求：

1. 新增文件地图，说明配置、环境、记录身份、状态、CSV/JSON/NPZ/图片的生成者和用途。
2. 新运行自动写 `FILES.md` 与 `file-guide.json`，不修改旧运行目录。
3. 助手执行 `make waymax-baseline`，另建
   `artifacts/waymax/learning-20261107T163242Z-3nn37_xl/`。
4. 两个场景 expert 各 80 步，P1/P2 PASS，CPU 后端，TF 无可见 GPU。
   主运行 12.028 秒，父进程峰值 RSS 870.16 MiB。
5. 独立重算两份 CSV 的行数、时间索引、有效帧和均值，并核对 21 个留存文件
   哈希与导航链接，通过。该次未训练小模型。
6. 增加文件导航与讲解检查后，助手执行 `make test-waymax`：33 tests PASS；
   包含这些测试及既有学习/NAVSIM/Scenario 合约的相关回归：45 tests PASS。
   这与上方学习者此前的 26 项测试是不同时间的记录，不改写历史输出。

本段为助手演示，不把 Day 02–04 自动标为学习者完成。
新的讲解与产物不覆盖上一段学习者 inventory 的结果与记录。

## 结果目录整理

学习者确认“每阶段保留一份主要结果，其余归档”的建议后：

- 根目录保留学习者盘点、当前 expert 基线、已有完整教学实验三份结果。
- 四个较早运行和历史重复性报告移到 `artifacts/waymax/_archive/`，没有删除。
- 迁移 79 个文件、保留 87 个文件内容哈希与大小核对一致。
- 重新检查归档旧完整运行与主要完整运行的 checkpoint 和五份 CSV，六项一致。
- 文件导航为本机 `artifacts/waymax/README.md`；
  详情见[保留与迁移说明](../../waymax-artifact-retention.md)。

目录整理不是新的车辆实验，不改变学习理解验收状态。

## 本次学习检查点提交

本次提交汇总概念图解、终端输出解读、自动文件导航、基线演示、归档规则，
并准备 Day 02 的只读课单。提交前助手检查：

- Waymax 专项测试：37 tests PASS。
- 包含上述测试的既有学习/NAVSIM/Scenario 相关回归：49 tests PASS。
- 主要完整实验的 46 个留存文件校验 PASS。

这些不是学习者新增完成的课程任务，不改变概念题的 NOT_VERIFIED 状态。
实验目录仍被 git 忽略，没有把原始数据或 checkpoint 纳入提交；
学习文档、生成图解和脚本正常提交，本次不推送远端。

## 下一步学习验收

先查看本次 `environment.json`、`records.json`、`status.json`。
能找到 CPU、空 GPU 列表和两个 ID，并用自己的话说明“今天还没训练”，
再更新当天的理解验收；目前保持 `IN_PROGRESS`。
前面的概念题仍为 `NOT_VERIFIED`，不因命令通过就自动标为掌握。
继续读基线的 `expert-0/summary.json`、前三行 CSV 和 `baseline.png`，
用自己的话解释一行记录。先不跳到完整训练。

准备阶段虽已有工程证据，也不能代替学习者当天阅读、操作和解释的验收。
