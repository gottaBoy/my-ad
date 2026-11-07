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


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    roles_diagram()
    experiments_diagram()
    print("Wrote two static learning diagrams; no simulator or GPU was initialized.")
