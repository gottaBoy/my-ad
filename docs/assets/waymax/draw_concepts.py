"""Draw static teaching diagrams with Pillow; no simulator, JAX or GPU."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
INK = "#202932"
MUTED = "#55616c"
BLUE = "#2365ae"
GREEN = "#187d58"
RED = "#b33c38"


def font(size):
    return ImageFont.truetype(FONT, size)


def text(draw, xy, content, size=26, color=INK):
    draw.text(xy, content, font=font(size), fill=color)


def arrow(draw, start, end, color=INK, width=4):
    draw.line((start, end), fill=color, width=width)
    x, y = end
    draw.polygon([(x, y), (x - 14, y - 8), (x - 14, y + 8)], fill=color)


def road(draw, top):
    draw.rectangle((55, top, 1445, top + 104), fill="#edf0f2")
    draw.line((55, top + 8, 1445, top + 8), fill="#9ca7b0", width=2)
    draw.line((55, top + 96, 1445, top + 96), fill="#9ca7b0", width=2)
    for x in range(70, 1420, 75):
        draw.line((x, top + 52, x + 35, top + 52), fill="#ffffff", width=4)


def car(draw, x, y, color, label=None, outline=False):
    box = (x, y, x + 100, y + 40)
    draw.rectangle(box, fill="#ffffff" if outline else color, outline=color, width=3)
    if label:
        text(draw, (x + 10, y + 2), label, 20, color if outline else "#ffffff")


def roles_diagram():
    image = Image.new("RGB", (1500, 1080), "#ffffff")
    draw = ImageDraw.Draw(image)
    text(draw, (55, 26), "不是三个软件一起开车，而是三种不同角色", 38)
    text(draw, (55, 88), "教学示意：位置、道路和数字均为讲解构造，不是本地场景截图。", 23, MUTED)
    columns = [
        (55, BLUE, "1  Waymo 数据", "像一本带时间的交通记录表", [
            "记录：车在哪里、速度多少、路在哪",
            "回答：过去发生过什么？",
            "它自己不会选择刹车或转向。",
        ]),
        (550, GREEN, "2  Waymax", "像可计算的俯视交通沙盘", [
            "输入：当前状态 + 动作",
            "输出：下一时刻的新状态",
            "它计算运动，不替你选择动作。",
        ]),
        (1045, RED, "3  策略模型", "像沙盘里的练习司机", [
            "看当前观测，输出控制动作",
            "例如：加速度、转向曲率",
            "模型可以做错，也可以跑偏。",
        ]),
    ]
    for x, color, title, subtitle, lines in columns:
        draw.line((x, 160, x + 395, 160), fill=color, width=5)
        text(draw, (x, 180), title, 32, color)
        text(draw, (x, 235), subtitle, 25)
        for index, line in enumerate(lines):
            text(draw, (x, 292 + 42 * index), line, 22, MUTED)
    text(draw, (55, 430), "只看一次 step：把“下一步”拆开看", 32)
    road(draw, 500)
    car(draw, 170, 514, BLUE, "自车")
    car(draw, 880, 514, RED, "前车")
    arrow(draw, (285, 534), (445, 534), BLUE)
    text(draw, (170, 615), "当前自车速度 8 m/s；前方有另一辆车。", 25)
    text(draw, (55, 688), "观测 Observation", 26, BLUE)
    text(draw, (400, 688), "策略 Policy", 26, RED)
    text(draw, (740, 688), "动作 Action", 26, RED)
    text(draw, (1110, 688), "Waymax.step()", 26, GREEN)
    arrow(draw, (300, 710), (370, 710))
    arrow(draw, (620, 710), (710, 710))
    arrow(draw, (970, 710), (1080, 710))
    text(draw, (55, 748), "当前车速、附近对象、地图", 21, MUTED)
    text(draw, (400, 748), "决定这一步怎样控制", 21, MUTED)
    text(draw, (740, 748), "例如：加速度 -2 m/s²", 21, MUTED)
    text(draw, (1110, 748), "计算 0.1 秒后的状态", 21, MUTED)
    draw.line((55, 825, 1445, 825), fill="#d6dce0", width=2)
    text(draw, (55, 852), "示意结果：8 + (-2) × 0.1 = 7.8 m/s", 30, GREEN)
    text(draw, (55, 905), "车辆再观察新状态、选择新动作，重复进行，才是一段 rollout。", 26)
    text(draw, (55, 960), "本地实验：0.1 秒/步 × 80 步 = 8 秒模拟；不等于电脑只运行 8 秒。", 24, MUTED)
    image.save(ROOT / "roles-and-step.png")


def experiments_diagram():
    image = Image.new("RGB", (1500, 1130), "#ffffff")
    draw = ImageDraw.Draw(image)
    text(draw, (55, 26), "同一份小数据，我们问了三类不同问题", 38)
    text(draw, (55, 88), "场景 0 = 训练练习；场景 1 = 另一段验证记录。不是第 0 步和第 1 步。", 24, MUTED)
    sections = [
        (160, BLUE, "问题 A：动作有没有真的影响车辆？", [
            (55, "同一场景 0\n同一起点"),
            (440, "expert 参考动作\n固定负加速度动作"),
            (980, "各跑一条轨迹\n比较是否不同"),
        ], "这是控制对照，不是训练。固定负加速度也不是安全控制器。"),
        (425, GREEN, "问题 B：小模型能模仿吗，换场景还行吗？", [
            (55, "场景 0：当前观测\n+ expert 参考答案"),
            (540, "80 组教学样本\n训练一个小模型"),
            (1035, "场景 1：模型自己控制\n检查偏离和越界"),
        ], "expert 可用下一帧日志制作标签；学习模型输入不能直接包含该答案。"),
        (725, RED, "问题 C：保存的模型能准确还原吗？", [
            (55, "已经训练好的模型\n参数 + 归一化信息"),
            (565, "checkpoint.npz\n保存到本地"),
            (1035, "新 Python 进程加载\n比较预测与轨迹"),
        ], "能还原 ≠ 开得好。一个会跑偏的模型，也可以保存和重载得完全一致。"),
    ]
    for top, color, heading, blocks, caption in sections:
        draw.line((55, top, 1445, top), fill=color, width=4)
        text(draw, (55, top + 18), heading, 30, color)
        for x, label in blocks:
            draw.multiline_text((x, top + 90), label, font=font(24), fill=INK, spacing=10)
        arrow(draw, (300, top + 120), (405 if top == 160 else 505, top + 120), color)
        arrow(draw, (820 if top == 160 else 865, top + 120),
              (945 if top == 160 else 1000, top + 120), color)
        text(draw, (55, top + 185), caption, 22, MUTED)
    text(draw, (55, 1020), "当前实测：工程链路跑通；学习模型在验证场景仍有明显越界。", 28)
    image.save(ROOT / "three-experiments.png")


def record_hierarchy_diagram():
    image = Image.new("RGB", (1500, 1090), "#ffffff")
    draw = ImageDraw.Draw(image)
    text(draw, (55, 25), "Day 03：一段场景，包含对象、地图和一段时间", 37)
    text(draw, (55, 90), "结构为教学示意；数字来自本地文件前两条记录的只读核对。", 23, MUTED)
    text(draw, (55, 155), "1 个 TFRecord 文件", 30, BLUE)
    arrow(draw, (400, 178), (570, 178), BLUE)
    text(draw, (605, 155), "本课只取前 2 条 record，不能推断全文件只有 2 条。", 25)
    draw.line((55, 235, 1445, 235), fill="#d6dce0", width=2)
    text(draw, (55, 258), "看什么", 27)
    text(draw, (660, 258), "场景 0", 30, BLUE)
    text(draw, (1100, 258), "场景 1", 30, GREEN)
    rows = [
        ("原始字段名数量（不是车辆数）", "125", "125"),
        ("预留对象槽位（不是都有效）", "128", "128"),
        ("current 帧有效对象", "6", "14"),
        ("本实验选取的对象槽位上限", "前 32 槽", "前 32 槽"),
        ("时间帧：过去 + 当前 + 未来", "10 + 1 + 80", "10 + 1 + 80"),
        ("地图点槽位（不是道路条数）", "30000", "30000"),
        ("其中有效地图点", "12680", "10154"),
    ]
    for index, (label, first, second) in enumerate(rows):
        y = 335 + index * 72
        text(draw, (55, y), label, 25)
        text(draw, (660, y), first, 27, BLUE)
        text(draw, (1100, y), second, 27, GREEN)
        draw.line((55, y + 56, 1445, y + 56), fill="#edf0f2", width=1)
    text(draw, (55, 885), "一个场景 ≠ 一辆车 ≠ 一个时刻", 32)
    text(draw, (55, 945), "槽位像登记表的空行；valid 告诉我们哪一行在这一帧能用。", 26, MUTED)
    text(draw, (55, 997), "90000 个 xyz 数值 ÷ 每点 3 个坐标 = 30000 个地图点槽位。", 26, MUTED)
    image.save(ROOT / "record-hierarchy.png")


def reset_step_diagram():
    image = Image.new("RGB", (1500, 1100), "#ffffff")
    draw = ImageDraw.Draw(image)
    text(draw, (55, 25), "Day 04：reset 摆好起点，step 再向前推进", 37)
    text(draw, (55, 88), "教学时间轴：复用已有 baseline 的索引，不是新仿真截图。", 23, MUTED)

    text(draw, (55, 155), "原始记录：91 帧，从索引 0 数到 90", 30)
    segments = [
        (55, 535, BLUE, "过去：0 ... 9", "10 帧"),
        (555, 865, GREEN, "当前：10", "1 帧"),
        (885, 1445, RED, "未来参考：11 ... 90", "80 帧"),
    ]
    for left, right, color, label, count in segments:
        draw.line((left, 230, right, 230), fill=color, width=7)
        text(draw, (left + 15, 250), label, 27, color)
        text(draw, (left + 15, 300), count, 24, MUTED)
    text(draw, (55, 365), "reset：把前 11 帧填入仿真记录，当前指针停在 10；未来仿真尚未完成。", 26)
    draw.line((55, 430, 1445, 430), fill="#d6dce0", width=2)

    text(draw, (55, 455), "同一次 rollout：本轮步数与场景索引分开数", 30)
    columns = [
        (55, BLUE, "reset 完成", "step 次数：0", "timestep：10", "相对起点：0 秒"),
        (430, GREEN, "第 1 次推进后", "step 次数：1", "timestep：11", "相对起点：0.1 秒"),
        (805, GREEN, "第 2 次推进后", "step 次数：2", "timestep：12", "相对起点：0.2 秒"),
        (1180, RED, "第 80 次后", "step 次数：80", "timestep：90", "相对起点：8 秒"),
    ]
    for x, color, heading, steps, timestep, elapsed in columns:
        text(draw, (x, 525), heading, 27, color)
        text(draw, (x, 583), steps, 24)
        text(draw, (x, 630), timestep, 24)
        text(draw, (x, 677), elapsed, 24, MUTED)
    for left, right in ((300, 395), (675, 770), (1050, 1145)):
        arrow(draw, (left, 550), (right, 550))
    text(draw, (1030, 505), "省略中间步骤", 19, MUTED)
    draw.line((55, 750, 1445, 750), fill="#d6dce0", width=2)
    text(draw, (55, 785), "一次推进：当前状态 + 动作 → 动力学 → 下一状态 → 保存 CSV 一行", 29, GREEN)
    text(draw, (55, 845), "CSV 动作列 = 这次用了什么动作；位置与指标 = 该动作执行后的结果。", 25)
    text(draw, (55, 912), "80 次 × 0.1 秒 = 8 秒模拟时间，不等于电脑实际耗时。", 28)
    text(draw, (55, 980), "is_done=true：到达记录末尾。不是“没有越界”，更不是驾驶安全验收。", 25, RED)
    image.save(ROOT / "reset-step-timeline.png")


def action_units_diagram():
    image = Image.new("RGB", (1500, 1100), "#ffffff")
    draw = ImageDraw.Draw(image)
    text(draw, (55, 25), "Day 05：动作是两个有单位的数", 37)
    text(draw, (55, 88), "本地图示使用 bicycle model；不是方向盘、摄像头或真实车辆控制器。", 23, MUTED)

    text(draw, (55, 160), "策略输出 Action", 30, BLUE)
    draw.rounded_rectangle((55, 220, 610, 400), radius=12, fill="#eef5fb", outline=BLUE, width=3)
    text(draw, (90, 245), "[ acceleration, steering curvature ]", 27, BLUE)
    text(draw, (90, 305), "[ 加速度,       转向曲率       ]", 27)
    text(draw, (90, 355), "两个连续数，不是左/右/停三个按钮。", 22, MUTED)

    arrow(draw, (650, 310), (820, 310), BLUE)
    text(draw, (680, 260), "dt = 0.1 秒", 22, MUTED)

    text(draw, (850, 160), "物理单位与边界", 30, GREEN)
    draw.rounded_rectangle((850, 220, 1445, 400), radius=12, fill="#eef8f3", outline=GREEN, width=3)
    text(draw, (885, 245), "accel：[-6, 6] m/s^2", 27, GREEN)
    text(draw, (885, 305), "curvature：[-0.3, 0.3] m^-1", 27, GREEN)
    text(draw, (885, 355), "超出边界会先 clip（裁剪）。", 22, MUTED)

    draw.line((55, 455, 1445, 455), fill="#d6dce0", width=2)
    text(draw, (55, 490), "两条计算方向", 30)
    text(draw, (55, 560), "Forward dynamics", 28, GREEN)
    text(draw, (55, 610), "当前状态 + 动作", 25)
    arrow(draw, (295, 625), (415, 625), GREEN)
    text(draw, (450, 610), "计算下一状态", 25)
    arrow(draw, (670, 625), (790, 625), GREEN)
    text(draw, (825, 610), "x/y/yaw/velocity", 25, GREEN)

    text(draw, (55, 720), "Inverse dynamics", 28, RED)
    text(draw, (55, 770), "当前状态 + 日志下一帧", 25)
    arrow(draw, (335, 785), (455, 785), RED)
    text(draw, (490, 770), "反推 accel/curvature", 25)
    arrow(draw, (735, 785), (855, 785), RED)
    text(draw, (890, 770), "expert 参考动作", 25, RED)

    draw.line((55, 855, 1445, 855), fill="#d6dce0", width=2)
    text(draw, (55, 890), "训练时：物理动作 ÷ [6.0, 0.3] → 归一化标签 [-1, 1]", 27, BLUE)
    text(draw, (55, 945), "执行前：模型输出 × [6.0, 0.3] → 恢复物理动作", 27, BLUE)
    text(draw, (55, 1000), "固定 brake：[-2.0 m/s^2, 0.0 m^-1]，只用于动作因果对照，不是安全控制器。", 24, RED)
    image.save(ROOT / "action-units.png")


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    roles_diagram()
    experiments_diagram()
    record_hierarchy_diagram()
    reset_step_diagram()
    action_units_diagram()
    print("Wrote five static learning diagrams; no simulator or GPU was initialized.")
