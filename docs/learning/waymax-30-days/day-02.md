# Day 02：这次到底运行了哪份代码

## 状态

- 计划日期：2026-11-09；这是建议日期，可按实际学习进度提前或顺延。
- 实际学习日期：未填写。
- 学习状态：`NOT_STARTED`。
- 学习者命令执行：`NOT_RUN`。
- 本页是准备好的课单，不是学习者已完成的记录。
- 本课不训练、不仿真、不申请 GPU，不安装或更新依赖。

Day 01 已有环境检查和操作记录，但理解验收仍为进行中。
先确认自己能解释“CPU 后端、空的 GPU 列表、本次只做盘点”，再开始本课；
不因翻到第二页就把前一天自动改成 VERIFIED。

## 1. 今天只解决一个问题

本机同时有：

```text
waymax/
waymax-my/
waymax-rl_planner/
.venv-waymax/
```

**目录存在，不等于程序用的是它。** 今天要弄清：

```text
谁执行程序？      Python 解释器 Interpreter
安装时登记在哪？  包元数据 / editable registration
这次真正加载哪份？实际导入的源代码 Imported Source
```

类比：一个人可以有几本同名教材；有一本登记在书目里，但这次上课实际拿来
阅读的可能是另一本。要看“实际打开的书”，不能只看书柜有哪些书。
比喻只帮助理解，下面用真实文件核对。

## 2. 从已有结果读三个字段

不必再运行 `make waymax-check`，直接复用自己的那次盘点：

```bash
cd /home/my/workspace/autodrive/my-ad
P1_RUN=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-20261107T152431Z-ym466a_w
cat "$P1_RUN/environment.json"
```

今天先找这三项，不用背版本号：

| 字段 | 这次记录 | 它回答的问题 |
| --- | --- | --- |
| `python` | `/home/my/workspace/autodrive/.venv-waymax/bin/python` | 谁在执行我们的 Python 程序？ |
| `source.root` | `/home/my/workspace/autodrive/waymax-my` | 本实验选定并核对的源码目录？ |
| `waymax_file` | `/home/my/workspace/autodrive/waymax-my/waymax/__init__.py` | `import waymax` 实际加载到了哪里？ |

JSON 里 `source.root` 不是一个带点的字段名，而是：

```json
{
  "source": {
    "root": "/home/my/workspace/autodrive/waymax-my"
  }
}
```

读法是“source 这一组里面的 root”。
`__init__.py` 是这里的 Python 包入口文件，不是训练 checkpoint。

## 3. 看看配置是怎么选的

```bash
cat config/waymax/learning.json
```

只找：

```json
"source_root": "../waymax-my",
"data_file": "../waymax/waymax/dataloader/testdata/tfrecord_with_routes"
```

- `source_root` 选择的是程序代码。
- `data_file` 选择的是资料文件。
- 本实验把这两个相对路径按 `my-ad` 仓库根目录解析。
- 所以代码可以来自 `waymax-my`，数据来自另一个 `waymax` 目录；两者不矛盾。

**代码负责怎么计算，数据提供计算所需的交通记录。**

## 4. 为什么安装登记处却写着另一个目录

下面这条命令只读 Python 包元数据，不导入 Waymax/JAX/TF，不运行实验：

```bash
../.venv-waymax/bin/python -I -c 'from importlib.metadata import distribution; print(distribution("waymo-waymax").read_text("direct_url.json"))'
```

助手准备课单时只读核对到的登记为：

```json
{
  "dir_info": {"editable": true},
  "url": "file:///home/my/workspace/autodrive/waymax-rl_planner"
}
```

`editable` 表示这种安装关联本地源码目录。这个登记能说明安装来源，
**不能单独证明本次实验实际导入了哪里**。不要为了让两者同名而重新安装包。

本实验入口先明确选择 `waymax-my`：

```python
source = local_path(config["source_root"])
sys.path.insert(0, str(source))
import waymax
```

随后核对 `waymax.__file__` 所在位置，如果不是选定目录就报错。
因此，本次环境记录中 `waymax_file` 是更直接的实际导入证据。

选择 `waymax-my` 的背景：准备阶段旧 fork 在当前 JAX 下调用已移除的
`jax.tree_map` 失败；旁边已有使用 `jax.tree.map` 的兼容源码，所以明确选择它，
而不是修改 JAX、升级环境或重写原仓库。

这一原因不是要求现在阅读 JAX 内部，只要知道“同名不同版本不能静默混用”。

## 5. 今天应画出的关系

```text
run-cpu.sh
    |
    v
.venv-waymax/bin/python        执行教学脚本
    |
    v
learning_lab.py
    |
    +-- 按 source_root 选择 waymax-my，核对实际 import 路径
    |
    +-- 按 data_file 读取 waymax/ 下的本地测试 TFRecord
    |
    +-- environment.json 记录 Python / 源码 / 数据身份

包安装登记：waymax-rl_planner  仅说明 editable 安装来源，不覆盖上面的实际导入证据
```

## 6. 当天验收与日志

下面三项请用自己的话填写，暂不预设已答对：

| 问题 | 自己的回答 | 状态 |
| --- | --- | --- |
| `.venv-waymax` 和 `waymax-my` 有什么区别？ | 待填写 | NOT_VERIFIED |
| 为什么源码目录与数据目录可以不同？ | 待填写 | NOT_VERIFIED |
| 哪个字段证明本次实际加载了 `waymax-my`？ | 待填写 | NOT_VERIFIED |

操作后在本页补实际日期、命令输出、配置/输入身份和自己的解释，
再更新[进度表](README.md)的 Day 02。未操作前保持 `NOT_STARTED` / `NOT_RUN`。

本课完成的判断：能说明“解释器、安装来源、实际代码、输入数据”四者的关系，
不需要训练 loss 或新的结果目录。下一课再看 TFRecord 里的两段记录是什么。
