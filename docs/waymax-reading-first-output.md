# 第一次运行后，终端输出怎么看

记录日期：2026-11-07。对应学习者实际运行：

`artifacts/waymax/learning-20261107T152431Z-ym466a_w/`

本页根据学习者粘贴的终端输出和本地结果 JSON 编写。没有重新运行车辆仿真、
训练或 GPU；这里是在阅读已经产生的结果。

## 1. 先用“开课前检查”理解

可以把今天这两条命令理解为：

```text
make waymax-check
    检查：计算环境能用吗？选对代码了吗？本地资料能打开吗？

make test-waymax
    检查：实验脚本的保护规则和文件校验规则还正确吗？
```

今天还没有让小车在仿真中走，也没有训练“练习司机”。

| 今天已经检查 | 今天这次没有检查 |
| --- | --- |
| 现有 Python/JAX 环境可用 | 车辆能否完成 80 步仿真 |
| 实际后端是 CPU，没有可见 GPU | 模型训练 loss 是否下降 |
| 实际加载指定的 Waymax 源码 | 模型是否能可靠驾驶 |
| 本地文件中两条场景记录的身份 | 摄像头、LiDAR、实车或 GPU 性能 |
| 本仓库的 26 项专项自动测试 | 26 个交通场景或 26 种驾驶能力 |

**今天通过的是开课前检查，不是驾驶考试。**

## 2. 第一条命令逐行翻译

```text
make waymax-check
```

这是你输入的命令。`make` 按仓库的 `Makefile` 查找名为 `waymax-check`
的任务，再执行里面的命令。不需要先学完整 Makefile，先把它当作“运行指定任务”。

```text
bash scripts/waymax/run-cpu.sh --stage inventory
```

这是 make 打印的实际命令，不是错误：

| 片段 | 中文理解 |
| --- | --- |
| `bash` | 用 Bash 执行脚本 |
| `scripts/waymax/run-cpu.sh` | 运行本仓库的纯 CPU 入口 |
| `--stage inventory` | 本次只做盘点检查；inventory 是“清单/盘点”，不是训练 |

入口会设置进程级 CPU/GPU 可见性和线程参数，再运行 Python 检查程序。
不会因为屏幕出现 `bash` 就多运行了一次你没要求的训练。

```text
P1 PASS: backend=cpu, source=/home/my/workspace/autodrive/waymax-my, fixture=40073764 bytes
```

把这一行拆成四件事：

| 字段 | 中英文含义 | 这次说明什么 |
| --- | --- | --- |
| `P1 PASS` | 阶段 1 检查通过 | 当前检查项通过，不是“课程第 1 天全部学会” |
| `backend=cpu` | Backend，计算后端 | JAX 使用 CPU，不是 GPU |
| `source=.../waymax-my` | Source，实际代码来源 | 程序真正加载这个目录下的 Waymax |
| `fixture=40073764 bytes` | Fixture，测试样本；bytes，字节 | 文件大小 40,073,764 字节，约 38.22 MiB |

为什么要显示 source？本机有多个 Waymax 目录，存在不等于实际加载了它。
检查实际路径，是避免“以为运行 A，实际运行 B”。

```text
P1 PASS: 2 distinct local records
```

含义是：从本地文件读取了两条不同的记录，record 内容哈希不同、
scenario ID 也不同。这里的 `distinct` 是“不同的”。

它**不是两辆车，不是两个模型，不是训练了两次**。
此阶段解析 TFRecord 中的字段与身份，不进行 `load_scenarios` 后面的完整
SimulatorState 构造、动力学推进和训练。这些在后续 baseline/all 阶段验证。

本次的两个实际 scenario ID：

```text
记录 0：d27157fded0ddd5e
记录 1：543908aa309f5a0b
```

把 ID 想成资料的编号即可，现在不需要背下来。

```text
RUN_DIR=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-20261107T152431Z-ym466a_w
```

这是 **Run Directory，本次运行的结果目录**，像这次检查的档案袋。
后面查看 JSON 要找这个确切目录，不要随便选一个以前的成功目录。

名称里的 `20261107T152431Z` 是 UTC 时间戳；`Z` 表示 UTC，不是失败标记。
后面的 `ym466a_w` 用来避免目录重名，不是评分。

## 3. 第二条命令只先看三处

```text
python3 -m unittest discover -s tests -p 'test_waymax_*.py' -v
```

含义是：Python 在 `tests` 目录找名字匹配 `test_waymax_*.py` 的测试，
然后逐项显示检查结果。

### 第一处：每一行最后的 ok

```text
test_cpu_guard_rejects_gpu_and_preloaded_driver ... ok
```

这项检查的是“如果配置试图启用 GPU 或预加载驱动，保护规则会不会拒绝”。
测试符合预期，所以显示 `ok`。

**不是说它真的在 GPU 上训练了，也不是说已经把 NVIDIA 驱动修好了。**
这类测试使用受控环境/替身检查拒绝规则。

再例如：

```text
test_same_size_checkpoint_corruption_is_rejected ... ok
```

它在临时测试文件上模拟“文件长度相同但内容被改”，检查哈希校验能否发现。
显示 `ok` 表示拒绝机制正确，不是原始模型文件已经损坏。

### 第二处：总数和最终结果

```text
Ran 26 tests in 0.072s

OK
```

含义是：这次执行了 26 项自动测试，用了约 0.072 秒，全部符合各自预期。

这 26 项包括配置边界、CPU 保护、坏文件拒绝、课程日程和文档图片等检查。
**不是 26 次驾驶试验，不能推导为模型会开车或学习者已掌握。**

### 第三处：最后的 bash -n

```text
bash -n scripts/waymax/run-cpu.sh
```

`-n` 表示只检查 Shell 脚本语法，不执行脚本里面的实验。
没有打印语法错误而返回终端，是正常表现；不需要等待另一个 PASS。

终端提示符中的 `git:(main) ✗` 也不是实验 FAIL：结合当前 `git status`，
它表示 main 分支有未提交修改，例如正在补充的教学文档。
判断实验结果要看对应命令和结果文件，而不是提示符图案。

## 4. 第一次先打开三个文件

这三条命令只是读文件，不启动训练：

```bash
cd /home/my/workspace/autodrive/my-ad
LAB_RUN=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-20261107T152431Z-ym466a_w
cat "$LAB_RUN/environment.json"
cat "$LAB_RUN/records.json"
cat "$LAB_RUN/status.json"
```

JSON 就是按“字段名：字段值”保存的文本。第一次不用读懂全部字段，
只找下列几项：

| 文件 | 找什么 | 本次实际值 | 怎么读 |
| --- | --- | --- | --- |
| `environment.json` | `jax_backend` | `"cpu"` | 使用 CPU 计算 |
| `environment.json` | `jax_devices` | `["cpu:0"]` | 一个 JAX CPU 设备；不是 GPU 0 |
| `environment.json` | `tensorflow_visible_gpus` | `[]` | 空列表：TensorFlow 没有可见 GPU |
| `environment.json` | `cpu_affinity` | `[0, 1]` | 进程可调度在这两个 CPU 逻辑处理器上 |
| `environment.json` | `data_file` | 本地 `tfrecord_with_routes` 路径 | 这次使用的资料文件 |
| `records.json` | `scenario_id` | 两个不同 ID | 两条资料身份不同 |
| `status.json` | `stage` | `"inventory"` | 本次只做盘点检查 |
| `status.json` | `status` / `phases.P1` | `"PASS"` | 本次 P1 检查通过 |
| `status.json` | `seconds` | `2.859` | 检查耗时，不是模拟驾驶时长 |
| `status.json` | `peak_rss_mib` | `674.43` | 父进程峰值常驻内存测量，非硬内存上限 |

`cpu:0` 和 `[0, 1]` 不矛盾：前者是 JAX 的逻辑设备名称，
后者是操作系统允许进程使用的 CPU 处理器编号。

`data_sha256` 是文件内容指纹，后续用于发现输入是否被改过；现在不需要背长串字符。
`feature_count=125` 是记录中的字段数量，**不是车辆数量**。
90000 个 roadgraph xyz 数值也不是 90000 辆车或道路条数。

## 5. 现在能说什么

可以说：

> 我在指定目录运行了环境盘点。程序选中了 waymax-my，用 CPU，
> 找到了两条不同的本地场景记录，26 项专项自动测试全部符合预期。

暂时不能说：

> 我刚完成了车辆仿真、训练了驾驶模型，模型能安全开车了。

这就是今天最重要的区别。下一步先自己找到 `jax_backend`、
`tensorflow_visible_gpus` 和两个 `scenario_id`，用一句话解释；
不必立刻继续运行 `make waymax-learning`。

后续补充：学习者已找到 `"jax_backend": "cpu"`、
`"tensorflow_visible_gpus": []`，并要求继续。文件生成过程与用途见
[实验文件说明](waymax-files-explained.md)；后续基线另建目录、另记阶段，
本页保留对这次 inventory 输出的原始解读。
