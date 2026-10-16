"""pixkit —— 像素游戏风解说视频引擎

画面：场景在 480x270 低分辨率上逐像素绘制（色带渐变、阶梯光晕、像素小人），最近邻放大 4 倍到 1920x1080；
      文字 / UI 面板在高分辨率层绘制，保证清晰。
声音：配音 + numpy 合成的 8-bit 音效（UI 弹出时自动登记）+ 按章节换和弦的芯片 BGM。
时间：用配音的逐字时间轴（transcribe.py 生成 words.json）驱动动画，`tl.at("关键词", 秒)` 查词出现时间。

典型用法见 example_video.py。所有函数都可直接 import 使用：
    import pixkit as px
"""
import difflib, glob, json, math, os, random, re, shutil, subprocess, sys, wave
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# ================================================================ 基本参数
LW, LH, S = 480, 270, 4                 # 低分辨率画布与放大倍数
W, H, FPS = LW * S, LH * S, 30

GOLD = (255, 190, 60)
ORANGE = (255, 150, 40)
WHITE = (248, 244, 236)
GREY = (150, 140, 130)
INK = (16, 12, 20)
RED = (235, 70, 60)
GREEN = (110, 210, 120)
CHALK = (236, 236, 226)


def find_font():
    for p in [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf",
              "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"]:
        if os.path.exists(p):
            return p
    hits = glob.glob("/usr/share/fonts/**/*CJK*", recursive=True)
    if hits:
        return hits[0]
    raise RuntimeError("找不到中文字体，请设置环境变量 PIXKIT_FONT")


def find_ffmpeg():
    cand = [os.environ.get("PIXKIT_FFMPEG"), shutil.which("ffmpeg"),
            os.path.join(os.path.expanduser("~"), "tools", "ffbin", "ffmpeg.exe")]
    for c in cand:
        if c and os.path.exists(c):
            return c
    raise RuntimeError("找不到 ffmpeg：先运行 setup_env，或设置 PIXKIT_FFMPEG")


FONT = os.environ.get("PIXKIT_FONT") or find_font()


# ================================================================ 数学 / 缓动
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, start, dur):
    return clamp((t - start) / dur) if dur > 0 else float(t >= start)


def ease_out(p):
    return 1 - (1 - p) ** 3


def ease_io(p):
    return 3 * p * p - 2 * p * p * p


def back_out(p, s=1.8):
    p -= 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def lerp(a, b, k):
    return a + (b - a) * k


def mix(c1, c2, k):
    return tuple(int(lerp(c1[i], c2[i], k)) for i in range(3))


# ================================================================ 低分辨率绘制
def new_lo(color=(0, 0, 0)):
    return Image.new("RGBA", (LW, LH), tuple(color) + (255,))


def bands(d, y0, y1, c_top, c_bot, steps, x0=0, x1=LW):
    """分段色带渐变 —— 像素画的天空/墙面都用它，不要用平滑渐变。"""
    for i in range(steps):
        ya = y0 + (y1 - y0) * i // steps
        yb = y0 + (y1 - y0) * (i + 1) // steps
        d.rectangle([x0, ya, x1, yb], fill=mix(c_top, c_bot, i / max(1, steps - 1)) + (255,))


def glow(img, cx, cy, r, color, max_a=90, steps=4, ry=None):
    """阶梯式光晕（4 级同心椭圆叠加）。"""
    ry = ry or r
    if r <= 0:
        return
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for i in range(steps):
        k = 1 - i / steps
        d.ellipse([cx - r * k, cy - ry * k, cx + r * k, cy + ry * k], fill=tuple(color) + (int(max_a / steps),))
    img.alpha_composite(lay)


def cone(img, top, bottom_l, bottom_r, color, a=40, steps=3):
    """台灯/聚光灯的光锥。"""
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    tx, ty = top
    for i in range(steps):
        k = 1 - i * 0.28
        cxm = (bottom_l[0] + bottom_r[0]) / 2
        hw = (bottom_r[0] - bottom_l[0]) / 2 * k
        d.polygon([(tx - 3 * k, ty), (tx + 3 * k, ty), (cxm + hw, bottom_r[1]), (cxm - hw, bottom_l[1])],
                  fill=tuple(color) + (a,))
    img.alpha_composite(lay)


def floor(d, y, c1, c2):
    d.rectangle([0, y, LW, LH], fill=tuple(c1) + (255,))
    d.rectangle([0, y, LW, y + 2], fill=tuple(c2) + (255,))


_vig = None


def vignette():
    """四角压暗（量化成 4 级）。"""
    global _vig
    if _vig is None:
        lay = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
        px = lay.load()
        for y in range(LH):
            for x in range(LW):
                dx, dy = (x - LW / 2) / (LW / 2), (y - LH / 2) / (LH / 2)
                v = clamp((math.sqrt(dx * dx * 0.8 + dy * dy) - 0.7) / 0.6)
                px[x, y] = (0, 0, 0, int(round(v * 4) / 4 * 170))
        _vig = lay
    return _vig


def draw_coin(d, x, y, spin):
    w = max(1, int(3 * abs(math.cos(spin))))
    d.rectangle([x - w, y - 3, x + w, y + 3], fill=(255, 200, 60, 255))
    d.rectangle([x - w, y - 3, x - w, y + 3], fill=(255, 240, 160, 255))
    d.rectangle([x + w, y - 2, x + w, y + 3], fill=(190, 120, 30, 255))


# ================================================================ 像素角色
PAL = {
    "O": (18, 14, 22), "K": (28, 24, 30), "k": (62, 56, 66), "S": (244, 204, 172), "s": (214, 168, 138),
    "G": (22, 18, 18), "E": (34, 28, 34), "M": (176, 92, 80), "W": (242, 242, 246), "w": (200, 202, 216),
    "U": (88, 92, 104), "u": (62, 64, 76), "L": (118, 122, 136), "B": (32, 26, 26),
    "T": (240, 232, 212), "t": (206, 196, 172), "N": (40, 60, 110), "Y": (246, 204, 64), "y": (206, 160, 40),
    "P": (60, 60, 70),
}
HAIR = {   # 第 0–7 行：发型 + 额头
    "slick": ["......OOOOOOOO......", "....OOKKKKKKKKOO....", "...OKKKKKKKKKKkKO...", "..OKKKKKKKKKKKKKKO..",
              "..OKKKKKKKKKKKKKKO..", "..OKKKKSSSSSSKKKKO..", "..OKKSSSSSSSSSSSKO..", ".OSKSSSSSSSSSSSSKSO."],
    "spiky": [".....O.OO.OO.O......", "....OKOKKOKKOKO.....", "...OKKKKKKKKKKKKO...", "..OKKKKKKKKKKKKKKO..",
              "..OKKKKKKKKKKKKKKO..", "..OKKSSKSSSKSSSKKO..", "..OKSSSSSSSSSSSSKO..", ".OSSSSSSSSSSSSSSSSO."],
    "bob":   ["......OOOOOOOO......", "....OOKKKKKKKKOO....", "...OKKKKKKKKKKKKO...", "..OKKKKKKKKKKKKKKO..",
              "..OKKKKKKKKKKKKKKO..", "..OKKKKKKKKKKKKKKO..", "..OKKSSSSSSSSSSKKO..", ".OKKSSSSSSSSSSSSKKO."],
    "bald":  ["....................", "......OOOOOOOO......", "....OOSSSSSSSSOO....", "...OSSSSSSSSSSSSO...",
              "..OSSSSSSSSSSSSSSO..", "..OSSSSSSSSSSSSSSO..", "..OSSSSSSSSSSSSSSO..", ".OSSSSSSSSSSSSSSSSO."],
}
EYES = {   # 第 8–11 行：(行, 眨眼行号)
    "round":  ([".OSSSGGSSSSSSGGSSSO.", ".OSSGSSGGGGGGSSGSSO.", ".OSSGEsGSSSSGEsGSSO.", ".OSSSGGSSSSSSGGSSSO."], 10, False),
    "square": ([".OSSGGGGGSSGGGGGSSO.", ".OSSGSESGGGGSESGSSO.", ".OSSGGGGGSSGGGGGSSO.", ".OSSSSSSSssSSSSSSSO."], 9, True),
    "none":   ([".OSSSSSSSSSSSSSSSSO.", ".OSSSSSSSSSSSSSSSSO.", ".OSSSSESSSSSSESSSSO.", ".OSSSSSSSssSSSSSSSO."], 10, True),
}
MOUTH = {  # 第 12–15 行
    "normal": ["..OSSSSSSMMSSSSSSO..", "...OSSSSSSSSSSSSO...", "....OOsSSSSSSsOO...."],
    "smile":  [".OSSSSMMMMMMMMSSSSO.", "..OSSSSMWWWWMSSSSO..", "...OSSSSMMMMSSSSO...", "....OOsSSSSSSsOO...."],
}
BODY = {
    "suit": ["...OUUUWWWWWWUUUO...", "..OUUULWWwwWWLUUUO..", ".OUUUUULWwWWLUUUUUO.", ".OUUUUUULWWLUUUUUUO.",
             ".OSUUUUUUUWUUUUUUSO.", ".OSuUUUUUUuUUUUUuSO.", "..OuUUUUUUUUUUUUuO..", "...OuuuuuuuuuuuuO..."],
    "tee":  ["...OTTTTSSSSTTTTO...", "..OTTTTTTTTTTTTTTO..", ".OTTTTTTTTTTNNTTTTO.", ".OtTTTTTTTTTNTTTTtO.",
             ".OSTTTTTTTTTTTTTTSO.", ".OSTTTTTTTTTTTTTTSO.", "..OYYYYYYYYYYYYYYO..", "...OYYYYYYyYYYYYO..."],
}
LEGS_A = ["....OPPPO..OPPPO....", "....OPPPO..OPPPO....", "....OPPPO..OPPPO....", "....OPPPO..OPPPO....",
          "...OBBBBO..OBBBBO...", "...OOOOOO..OOOOOO..."]
LEGS_B = ["....OPPPO..OPPPO....", "....OPPPO..OPPPO....", "...OPPPO....OPPPO...", "...OPPPO....OPPPO...",
          "..OBBBBO....OBBBBO..", "..OOOOOO....OOOOOO.."]


def build_sprite(rows, pal):
    im = Image.new("RGBA", (len(rows[0]), len(rows)), (0, 0, 0, 0))
    px = im.load()
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                px[x, y] = tuple(pal[ch]) + (255,)
    return im


def make_character(hair="slick", glasses=None, top="suit", bottom="pants", mouth="normal",
                   hair_color=(28, 24, 30), skin=(244, 204, 172), top_color=None, inner=(242, 242, 246),
                   bottom_color=None, logo=(40, 60, 110), shoes=(32, 26, 26)):
    """按真人特征拼一个 20x30 的大头像素小人，返回 {idle, blink, walk} 三帧。
    hair: slick/spiky/bob/bald；glasses: None/round/square；top: suit/tee；bottom: pants/shorts；mouth: normal/smile
    """
    eyes, blink_row, nose_done = EYES[glasses or "none"]
    lower = MOUTH[mouth]
    if mouth == "normal":
        lower = ([".OSSSSSSSSSSSSSSSSO."] if nose_done else [".OSSSSSSSssSSSSSSSO."]) + lower
    head = HAIR[hair] + eyes + lower
    top_color = top_color or ((88, 92, 104) if top == "suit" else (240, 232, 212))
    bottom_color = bottom_color or (top_color if top == "suit" else (60, 70, 100))
    pal = dict(PAL)
    pal.update({"K": hair_color, "k": mix(hair_color, (255, 255, 255), .25), "S": skin, "s": mix(skin, (0, 0, 0), .14),
                "U": top_color, "u": mix(top_color, (0, 0, 0), .3), "L": mix(top_color, (255, 255, 255), .2),
                "T": top_color, "t": mix(top_color, (0, 0, 0), .15), "W": inner, "w": mix(inner, (0, 0, 0), .18),
                "N": logo, "Y": bottom_color, "y": mix(bottom_color, (0, 0, 0), .2), "B": shoes,
                "P": skin if bottom == "shorts" else bottom_color})
    body = BODY[top]
    blink = [r.replace("E", "S") if i == 8 + (blink_row - 8) else r for i, r in enumerate(head)]
    return {"idle": build_sprite(head + body + LEGS_A, pal), "blink": build_sprite(blink + body + LEGS_A, pal),
            "walk": build_sprite(head + body + LEGS_B, pal)}


def random_npcs(n, seed=21):
    """路人 / 观众 / 学员：随机发色、衣服。"""
    r = random.Random(seed)
    out = []
    for _ in range(n):
        out.append(make_character(hair=r.choice(["slick", "bob", "slick", "spiky"]),
                                  hair_color=r.choice([(30, 26, 30), (90, 60, 40), (160, 120, 60)]),
                                  top=r.choice(["suit", "tee"]),
                                  top_color=r.choice([(200, 80, 80), (80, 120, 200), (90, 160, 90), (200, 160, 60), (150, 90, 170)]),
                                  bottom_color=r.choice([(50, 50, 60), (70, 60, 50), (40, 60, 100)])))
    return out


def draw_char(lo, spr, x, feet, t, scale=2, walking=False, flip=False, ph=0.0):
    """x=中心，feet=脚底 y。待机：呼吸起伏 + 偶尔眨眼；行走：两帧交替。"""
    if walking:
        k = int((t + ph) / 0.16) % 2
        im, bob = spr["walk" if k else "idle"], k
    else:
        im = spr["blink"] if ((t + ph) % 3.4) < 0.12 else spr["idle"]
        bob = 1 if ((t + ph) % 1.2) < 0.6 else 0
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    if flip:
        im = ImageOps.mirror(im)
    lo.alpha_composite(im, (int(x - im.width / 2), int(feet - im.height - bob * max(1, scale // 2))))


# ================================================================ 照片 → 像素头像
def cutout_white(im, thr=236):
    """从四边洪水填充去掉纯白背景（棚拍白底照适用）。"""
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    seen = bytearray(w * h)
    q = deque([(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] +
              [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)])
    while q:
        x, y = q.popleft()
        i = y * w + x
        if seen[i]:
            continue
        seen[i] = 1
        r, g, b, a = px[x, y]
        if min(r, g, b) < thr:
            continue
        px[x, y] = (r, g, b, 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                q.append((nx, ny))
    return im


def pixelate(im, width=60, colors=22):
    im = im.convert("RGBA")
    bbox = im.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
    if bbox:
        im = im.crop(bbox)
    h = round(im.height * width / im.width)
    small = im.resize((width, h), Image.LANCZOS)
    alpha = small.getchannel("A").point(lambda v: 255 if v > 110 else 0)
    rgb = small.convert("RGB").filter(ImageFilter.UnsharpMask(1, 80, 2))
    rgb = rgb.quantize(colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def photo_portrait(path, crop=None, width=60, white_bg=False, rel=True):
    """crop 为 (左, 上, 右, 下)；rel=True 时按比例(0–1)。返回低分辨率像素头像（放进相框时 1 像素 = 屏幕 4 像素）。"""
    src = Image.open(path).convert("RGB")
    if crop:
        w, h = src.size
        box = tuple(int(v * (w if i % 2 == 0 else h)) for i, v in enumerate(crop)) if rel else crop
        src = src.crop(box)
    im = cutout_white(src) if white_bg else src.convert("RGBA")
    return pixelate(im, width)


def framed(lo, img, cx, cy, p=1.0):
    """金色相框 + 红色角钉，像对标视频里的“传记封面”。"""
    d = ImageDraw.Draw(lo)
    fw, fh = img.width + 16, img.height + 16
    x0, y0 = int(cx - fw // 2), int(cy - fh // 2 + (1 - p) * 30)
    glow(lo, cx, cy, 120 * p, (255, 190, 80), 50)
    d.rectangle([x0 - 4, y0 - 4, x0 + fw + 3, y0 + fh + 3], fill=(60, 36, 10, 255))
    d.rectangle([x0 - 2, y0 - 2, x0 + fw + 1, y0 + fh + 1], fill=GOLD + (255,))
    d.rectangle([x0 + 2, y0 + 2, x0 + fw - 3, y0 + fh - 3], fill=(150, 100, 30, 255))
    d.rectangle([x0 + 4, y0 + 4, x0 + fw - 5, y0 + fh - 5], fill=(238, 230, 214, 255))
    lo.alpha_composite(img, (x0 + 8, y0 + fh - 8 - img.height))
    for qx, qy in [(x0 - 3, y0 - 3), (x0 + fw - 2, y0 - 3), (x0 - 3, y0 + fh - 2), (x0 + fw - 2, y0 + fh - 2)]:
        d.rectangle([qx - 2, qy - 2, qx + 3, qy + 3], fill=RED + (255,))


def big(im, k=S):
    """把低分辨率小图放大到高分辨率层用（保持像素感）。"""
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


# ================================================================ 像素图标（高分辨率层用）
IPAL = {"o": (60, 36, 8), "y": (255, 200, 60), "Y": (255, 236, 150), "d": (200, 130, 30), "w": (255, 255, 255),
        "r": (220, 70, 60), "b": (90, 150, 255), "g": (110, 210, 120), "G": (40, 120, 60), "R": (235, 70, 60),
        "f": (255, 120, 40), "k": (30, 30, 40), "s": (200, 210, 230), "n": (60, 90, 160)}


def icon(rows, pal=IPAL, scale=8):
    return build_sprite(rows, pal).resize((len(rows[0]) * scale, len(rows) * scale), Image.NEAREST)


ICONS = {
    "coin": icon([".oooo.", "oyYYyo", "oYyydo", "oYyydo", "oyddyo", ".oooo."], scale=4),
    "trophy": icon(["ooooooooo", "oyYYYYYdo", ".oyYYYdo.", ".oyYyydo.", "..oyydo..", "...odo...", "...odo...",
                    "..ooooo..", ".oyyyydo."], scale=4),
    "star": icon(["...o...", "..oYo..", "ooyYyoo", "oyyyydo", ".oyyyo.", ".oyodo.", "oo...oo"], scale=4),
    "pin": icon([".ooo.", "orrro", "orwro", "orrro", ".oro.", "..o.."], scale=4),
    "book": icon(["oooooooooo", "orrrrrrryo", "oryyyyyryo", "orrrrrrryo", "oryyyyyryo", "orrrrrrryo",
                  "orrrrrrryo", "orrrrrrryo", "oooooooooo"], scale=12),
    "check": icon(["......gg", ".....ggG", "g...ggG.", "gg.ggG..", ".gggG...", "..gG...."]),
    "cross": icon(["RR....RR", ".RR..RR.", "..RRRR..", "..RRRR..", ".RR..RR.", "RR....RR"]),
    "fire": icon(["...f....", "..ff..f.", ".fyff.f.", ".fyYff..", "ffyYyff.", "fyYYYyf.", ".fyYyf..", "..fff..."]),
    "flag": icon(["kRRRR...", "kRRRRRR.", "kRRRR...", "k.......", "k.......", "k.......", "kk......"]),
    "key": icon([".yyy......", "y...y.....", "y...yyyyyy", "y...y..y.y", ".yyy......"]),
    "eye": icon(["..kkkk..", ".kwwwwk.", "kwwnnwwk", "kwnkknwk", ".kwwwwk.", "..kkkk.."], scale=7),
    "phone": icon(["kkkkkk", "kssssk", "ksnnsk", "ksnwsk", "ksnnsk", "kssssk", "kkwwkk"], scale=10),
    "table": icon(["kkkkkkkkk", "kgggggggk", "kkkkkkkkk", "kwkwkwkwk", "kkkkkkkkk", "kwkwkwkwk", "kkkkkkkkk",
                   "kwkwkwkwk", "kkkkkkkkk"], scale=12),
    "gift": icon(["..R...R..", "...R.R...", "yyyyRyyyy", "yYYyRyYYy", "yyyyRyyyy", ".yYyRyYy.", ".yyyRyyy.",
                  ".yYyRyYy.", ".yyyyyyy."], scale=12),
    "house": icon(["....RR....", "...RRRR...", "..RRRRRR..", ".RRRRRRRR.", "RRRRRRRRRR", ".wwwwwwww.",
                   ".wnnwwnnw.", ".wnnwwnnw.", ".wwwkkwww.", ".wwwkkwww."], scale=14),
    "scroll": icon(["dyyyyyyd", ".YYYYYY.", ".YkkkkY.", ".YYYYYY.", ".YkkkYY.", ".YYYYYY.", ".YkkkkY.", "dyyyyyyd"],
                   scale=12),
}


# ================================================================ 高分辨率文字 / UI
_fonts, _cache = {}, {}


def font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(FONT, size)
    return _fonts[size]


def _parts(markup, base, hl):
    parts, cur, on = [], "", False
    for ch in markup:
        if ch in "{}":
            if cur:
                parts.append((cur, hl if on else base))
            cur, on = "", ch == "{"
        else:
            cur += ch
    if cur:
        parts.append((cur, hl if on else base))
    return parts


def rich(markup, size, stroke=0, base=WHITE, hl=ORANGE):
    """文字渲染成 RGBA 小图；`{关键词}` 显示为橙色。按字形宽度逐段绘制，段间不会多出空隙。"""
    key = ("rich", markup, size, stroke, base, hl)
    if key not in _cache:
        f = font(size)
        parts = _parts(markup, base, hl)
        full = "".join(s for s, _ in parts) or " "
        l, tp, r, b = f.getbbox(full, stroke_width=stroke)
        out = Image.new("RGBA", (r - l + 8, b - tp + 8), (0, 0, 0, 0))
        d = ImageDraw.Draw(out)
        x = 4 - l
        for s, c in parts:
            d.text((x, 4 - tp), s, font=f, fill=tuple(c) + (255,), stroke_width=stroke, stroke_fill=INK + (255,))
            x += f.getlength(s)
        _cache[key] = out
    return _cache[key]


def txt(text, size, color=WHITE, stroke=0):
    return rich(text.replace("{", "").replace("}", ""), size, stroke, base=color)


def put(canvas, im, cx, cy, alpha=1.0, scale=1.0, anchor="c"):
    """把小图贴到画布：anchor='c' 以 (cx,cy) 为中心，'l' 以左边中点。"""
    if alpha <= 0.01 or scale <= 0.01:
        return
    if abs(scale - 1) > 0.005:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.NEAREST)
    if alpha < 0.999:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
    x = int(cx - im.width / 2) if anchor == "c" else int(cx)
    canvas.alpha_composite(im, (x, int(cy - im.height / 2)))


def panel(w, h, border=GOLD, fill=(20, 15, 26)):
    """像素 UI 面板：4px 粗边 + 切角 + 内描边。"""
    key = ("panel", w, h, border, fill)
    if key not in _cache:
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        p = S
        d.rectangle([p, 0, w - p - 1, h - 1], fill=tuple(border) + (255,))
        d.rectangle([0, p, w - 1, h - p - 1], fill=tuple(border) + (255,))
        d.rectangle([p, p, w - p - 1, h - p - 1], fill=mix(border, (0, 0, 0), 0.55) + (255,))
        d.rectangle([p * 2, p * 2, w - p * 2 - 1, h - p * 2 - 1], fill=tuple(fill) + (240,))
        _cache[key] = im
    return _cache[key]


SFX_EVENTS = set()


def sfx_at(t0, kind="pop"):
    """登记一个音效（同一时间点重复登记无妨）。kind 见 SFX_KINDS。"""
    if kind:
        SFX_EVENTS.add((round(t0, 2), kind))


def pop_k(t, t0, out=None, d=0.35, s=1.9):
    p = back_out(prog(t, t0, d), s)
    a = clamp(p * 1.5)
    if out is not None:
        a *= 1 - prog(t, out, 0.25)
    return a, 0.6 + 0.4 * p


def card(hi, t, t0, x, y, text, size=40, w=None, h=None, border=GOLD, fill=(20, 15, 26), color=WHITE,
         out=None, sfx="pop", img=None):
    """t0 时刻弹出的像素面板（中心 x,y），out 时刻淡出。img 可放图标。弹出时自动登记音效。"""
    if t < t0:
        return 0
    sfx_at(t0, sfx)
    a, sc = pop_k(t, t0, out)
    if a <= 0.01:
        return 0
    key = ("card", text, size, w, h, border, fill, color, id(img))
    if key not in _cache:
        lab = rich(text, size, base=color) if text else None
        iw = img.width + 24 if img is not None else 0
        tw = (lab.width if lab else 0) + iw
        ww = w or tw + 84
        hh = h or max(size * 2 + 20, (img.height + 48) if img is not None else 0)
        c = panel(ww, hh, border, fill).copy()
        x0 = (ww - tw) // 2
        if img is not None:
            c.alpha_composite(img, (x0, (hh - img.height) // 2))
        if lab:
            c.alpha_composite(lab, (x0 + iw, (hh - lab.height) // 2))
        _cache[key] = c
    put(hi, _cache[key], x, y, a, sc)
    return a


def bubble(hi, t, t0, x, y, text, size=38, out=None, sfx="pop"):
    """白底对话气泡。"""
    return card(hi, t, t0, x, y, text, size, border=WHITE, fill=(255, 255, 255), color=INK, out=out, sfx=sfx)


def label(hi, t, t0, x, y, text, size=40, color=WHITE, out=None, stroke=0, sfx=None, anchor="c"):
    """淡入上浮的文字。"""
    if t < t0:
        return
    sfx_at(t0, sfx)
    a = ease_out(prog(t, t0, 0.3))
    if out is not None:
        a *= 1 - prog(t, out, 0.25)
    put(hi, rich(text, size, stroke, base=color), x, y - (1 - a) * 20, a, anchor=anchor)


def icon_pop(hi, t, t0, im, x, y, out=None, sfx="pop"):
    if t < t0:
        return
    sfx_at(t0, sfx)
    a, sc = pop_k(t, t0, out)
    put(hi, im, x, y, a, sc)


def arrow(hi, t, t0, x0, y0, x1, y1, color=GOLD, width=10, d=0.35, out=None):
    """从起点生长出来的箭头。"""
    p = ease_out(prog(t, t0, d))
    if p <= 0 or (out is not None and t > out):
        return
    xe, ye = lerp(x0, x1, p), lerp(y0, y1, p)
    dr = ImageDraw.Draw(hi)
    dr.line([(x0, y0), (xe, ye)], fill=tuple(color) + (255,), width=width)
    ang = math.atan2(ye - y0, xe - x0)
    hs = width * 2.4
    dr.polygon([(xe + math.cos(ang) * hs, ye + math.sin(ang) * hs),
                (xe + math.cos(ang + 2.3) * hs, ye + math.sin(ang + 2.3) * hs),
                (xe + math.cos(ang - 2.3) * hs, ye + math.sin(ang - 2.3) * hs)], fill=tuple(color) + (255,))


def ring(hi, t, t0, cx, cy, r, color=CHALK, width=8, d=0.4):
    p = ease_out(prog(t, t0, d))
    if p > 0:
        rr = r * p
        ImageDraw.Draw(hi).ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=tuple(color) + (255,), width=width)


def counter(t, t0, dur, target, fmt="{:,}", done=None):
    """数字滚动文本：返回当前应显示的字符串。done 为滚完后的显示（如 '10万+'）。"""
    p = prog(t, t0, dur)
    if p >= 1 and done:
        return done
    return fmt.format(int(target * ease_io(p)))


def progress_bar(w, h, p, fg=GREEN, bg=(40, 30, 30)):
    bar = Image.new("RGBA", (w, h), tuple(bg) + (255,))
    ImageDraw.Draw(bar).rectangle([0, 0, int(w * clamp(p)), h - 1], fill=tuple(fg) + (255,))
    return bar


def badge(hi, t, t0, title, head_img=None, sub=None, color=ORANGE):
    """左上角栏目徽章，如「XX拆解」+ 主讲人像素头像。"""
    a = ease_out(prog(t, t0, 0.35))
    if a <= 0:
        return
    x = 40 - (1 - a) * 80
    put(hi, panel(420, 120, color, (60, 24, 10)), x, 130, a, anchor="l")
    tx = x + 30
    if head_img is not None:
        put(hi, head_img, x + 70, 130, a)
        tx = x + 135
    put(hi, txt(title, 40, GOLD), tx, 112 if sub else 130, a, anchor="l")
    if sub:
        put(hi, txt(sub, 24, GREY), tx + 2, 152, a, anchor="l")


def player_hud(hi, t, t0, head_img, name, lv, job):
    """游戏玩家状态条：头像 + 名字 + 等级 + 职业。"""
    a = ease_out(prog(t, t0, 0.4))
    if a <= 0:
        return
    dx = -(1 - a) * 80
    put(hi, panel(520, 130), 40 + dx, 135, a, anchor="l")          # y 留出顶部 44px 章节条
    put(hi, head_img, 110 + dx, 135, a)
    put(hi, rich(f"{name}  {{Lv.{lv}}}", 34), 175 + dx, 107, a, anchor="l")
    put(hi, txt(f"职业：{job}", 28, GREY), 175 + dx, 155, a, anchor="l")


def chapter_bar(hi, t, chapters, start=None, end=None):
    """顶部章节导航条。chapters=[(开始秒, 名称), ...]"""
    start = chapters[0][0] if start is None else start
    if t < start or not chapters:
        return
    a = ease_out(prog(t, start, 0.4)) * (1 - prog(t, end, 0.4) if end else 1)
    bar = Image.new("RGBA", (W, 44), (8, 6, 12, 200))
    cur = max([i for i, (ct, _) in enumerate(chapters) if t >= ct] or [0])
    seg = W // len(chapters)
    d = ImageDraw.Draw(bar)
    for i, (_, name) in enumerate(chapters):
        on = i == cur
        if on:
            d.rectangle([i * seg + 6, 38, (i + 1) * seg - 6, 43], fill=GOLD + (255,))
        im = txt(name, 24, GOLD if on else (120, 112, 104))
        bar.alpha_composite(im, (i * seg + (seg - im.width) // 2, (40 - im.height) // 2))
    put(hi, bar, W / 2, 22, a)


def draw_caption(hi, t, captions, size=50, y=None):
    """底部字幕：白字黑描边，{关键词} 橙色。"""
    for s0, e0, text in captions:
        if s0 <= t < e0:
            a = clamp(min((t - s0) / 0.08, (e0 - t) / 0.08))
            put(hi, rich(text, size, stroke=5), W / 2, y or H - 70, a)
            return


# ================================================================ 背景库（都是低分辨率 480x270，构建一次后 copy() 复用）
def bg_city_night(seed=5):
    lo = new_lo()
    d = ImageDraw.Draw(lo)
    bands(d, 0, 216, (8, 10, 30), (40, 34, 72), 7)
    glow(lo, 405, 42, 40, (255, 240, 200), 70)
    d.ellipse([395, 32, 415, 52], fill=(250, 240, 210, 255))
    r = random.Random(seed)
    for li, (col, base_h, win_p) in enumerate([((24, 24, 50), 70, 0.25), ((14, 14, 32), 45, 0.4)]):
        x = -10
        while x < LW:
            bw, bh = r.randint(18, 40), r.randint(base_h // 2, base_h + 40)
            d.rectangle([x, 216 - bh, x + bw, 216], fill=col + (255,))
            for wy in range(216 - bh + 4, 214, 6):
                for wx in range(x + 3, x + bw - 2, 5):
                    if r.random() < win_p:
                        d.rectangle([wx, wy, wx + 1, wy + 2], fill=(255, 200, 90, 255) if li else (180, 150, 90, 255))
            x += bw + r.randint(0, 6)
    floor(d, 216, (18, 15, 26), (52, 46, 74))
    return lo


_sr = random.Random(11)
_STARS = [(_sr.randrange(LW), _sr.randrange(150), _sr.random() * 6.28) for _ in range(90)]


def stars(lo, t):
    d = ImageDraw.Draw(lo)
    for x, y, ph in _STARS:
        v = math.sin(t * 2.2 + ph)
        if v > -0.3:
            d.point((x, y), fill=(255, 250, 220, 255) if v > 0.6 else (150, 150, 190, 255))


def bg_study():
    """书房：窗户+月亮、书架、台灯光锥、书桌（对标视频的主场景）。"""
    lo = new_lo((34, 24, 20))
    d = ImageDraw.Draw(lo)
    for x in range(0, LW, 24):
        d.rectangle([x, 0, x, 200], fill=(42, 30, 25, 255))
    d.rectangle([28, 26, 96, 100], fill=(70, 48, 32, 255))
    bands(d, 30, 96, (12, 18, 46), (34, 40, 84), 4, 32, 92)
    d.ellipse([70, 40, 82, 52], fill=(245, 236, 200, 255))
    d.ellipse([67, 38, 77, 48], fill=(26, 32, 70, 255))
    d.rectangle([61, 30, 63, 96], fill=(70, 48, 32, 255))
    d.rectangle([32, 62, 92, 64], fill=(70, 48, 32, 255))
    r = random.Random(9)
    d.rectangle([330, 24, 474, 200], fill=(56, 38, 26, 255))
    for row in range(4):
        y1 = 64 + row * 44
        d.rectangle([336, y1 - 38, 468, y1], fill=(24, 16, 14, 255))
        x = 338
        while x < 462:
            bw, bh = r.randint(3, 6), r.randint(24, 36)
            col = r.choice([(120, 40, 36), (40, 70, 90), (150, 110, 50), (60, 90, 60), (90, 60, 100), (170, 150, 120)])
            d.rectangle([x, y1 - bh, x + bw - 1, y1], fill=col + (255,))
            d.rectangle([x, y1 - bh + 4, x + bw - 1, y1 - bh + 5], fill=mix(col, (255, 220, 150), .4) + (255,))
            x += bw + (1 if r.random() < .3 else 0)
        d.rectangle([332, y1, 472, y1 + 4], fill=(86, 60, 40, 255))
    d.rectangle([84, 196, 108, 200], fill=(200, 150, 60, 255))
    d.rectangle([94, 120, 97, 196], fill=(210, 160, 70, 255))
    d.polygon([(72, 128), (120, 128), (110, 108), (82, 108)], fill=(40, 120, 90, 255))
    d.polygon([(82, 108), (110, 108), (106, 104), (86, 104)], fill=(70, 160, 120, 255))
    cone(lo, (96, 128), (40, 202), (170, 202), (255, 220, 140), 30)
    desk(lo)
    return lo


def desk(lo):
    """书桌前沿（画完桌后人物后再调用一次，人物就像站在桌子后面）。"""
    d = ImageDraw.Draw(lo)
    d.rectangle([0, 200, LW, LH], fill=(60, 40, 28, 255))
    d.rectangle([0, 200, LW, 205], fill=(104, 72, 46, 255))
    d.rectangle([0, 206, LW, 207], fill=(40, 26, 20, 255))
    glow(lo, 105, 204, 90, (255, 210, 130), 60, ry=14)


def bg_stage():
    lo = new_lo((10, 8, 14))
    d = ImageDraw.Draw(lo)
    for x in range(0, LW, 20):
        d.rectangle([x, 0, x + 9, 230], fill=(14, 11, 19, 255))
    d.rectangle([0, 230, LW, LH], fill=(20, 15, 22, 255))
    cone(lo, (240, 0), (150, 236), (330, 236), (255, 220, 150), 22)
    glow(lo, 240, 236, 110, (255, 210, 130), 70, ry=16)
    return lo


def bg_chalk_room():
    """主讲人点评用的黑板房：黑板占左侧 3/4，右侧留给主讲人小人（scale=3，x≈440）。"""
    lo = new_lo((22, 30, 34))
    d = ImageDraw.Draw(lo)
    for x in range(0, LW, 20):
        d.rectangle([x, 0, x, 210], fill=(26, 36, 40, 255))
    d.rectangle([24, 16, 398, 198], fill=(92, 62, 38, 255))
    d.rectangle([28, 20, 394, 194], fill=(34, 62, 50, 255))
    r = random.Random(3)
    for _ in range(140):
        d.point((r.randint(30, 392), r.randint(22, 192)), fill=(56, 86, 72, 255))
    d.rectangle([24, 196, 398, 201], fill=(120, 84, 52, 255))
    d.rectangle([60, 194, 74, 196], fill=(240, 240, 230, 255))
    d.rectangle([300, 194, 308, 196], fill=(240, 220, 120, 255))
    floor(d, 210, (40, 30, 26), (70, 52, 40))
    cone(lo, (440, 0), (380, 214), (480, 214), (255, 220, 150), 22)
    return lo


def bg_classroom():
    lo = new_lo((44, 38, 50))
    d = ImageDraw.Draw(lo)
    d.rectangle([100, 26, 380, 142], fill=(92, 62, 38, 255))
    d.rectangle([104, 30, 376, 138], fill=(34, 62, 50, 255))
    floor(d, 206, (60, 44, 34), (96, 70, 50))
    d.rectangle([210, 168, 270, 206], fill=(110, 76, 48, 255))
    d.rectangle([206, 164, 274, 170], fill=(140, 100, 62, 255))
    d.rectangle([232, 178, 248, 188], fill=GOLD + (255,))
    for x in (20, 440):
        d.rectangle([x, 40, x + 20, 206], fill=(60, 30, 40, 255))
        d.rectangle([x + 2, 40, x + 6, 206], fill=(90, 44, 56, 255))
    cone(lo, (240, 0), (150, 208), (330, 208), (255, 220, 150), 20)
    return lo


def bg_calendar():
    """挂着 100 格日历墙的房间；calendar_cell(i) 给出第 i 格坐标。"""
    lo = new_lo((30, 22, 18))
    d = ImageDraw.Draw(lo)
    for x in range(0, LW, 24):
        d.rectangle([x, 0, x, 210], fill=(38, 28, 22, 255))
    d.rectangle([156, 14, 466, 200], fill=GOLD + (255,))
    d.rectangle([159, 17, 463, 197], fill=(60, 36, 14, 255))
    d.rectangle([162, 20, 460, 194], fill=(18, 14, 18, 255))
    d.rectangle([162, 20, 460, 36], fill=(90, 40, 20, 255))
    for i in range(100):
        cx, cy = calendar_cell(i)
        d.rectangle([cx, cy, cx + 11, cy + 24], fill=(34, 28, 34, 255))
    floor(d, 210, (52, 36, 26), (90, 64, 44))
    glow(lo, 70, 120, 70, (255, 200, 120), 40)
    return lo


def calendar_cell(i):
    return 167 + (i % 20) * 14.6, 42 + (i // 20) * 30


def fill_calendar(lo, n):
    d = ImageDraw.Draw(lo)
    for i in range(n):
        cx, cy = calendar_cell(i)
        d.rectangle([cx, cy, cx + 11, cy + 24], fill=(255, 196, 70, 255))
        d.rectangle([cx, cy, cx + 11, cy + 2], fill=(255, 236, 160, 255))
    if 0 < n < 100:
        cx, cy = calendar_cell(n - 1)
        glow(lo, cx + 6, cy + 12, 16, (255, 220, 120), 90)


SHOP_COLS = [(200, 70, 60), (60, 120, 200), (220, 170, 60), (80, 160, 100), (170, 90, 170), (220, 120, 60)]


def bg_street(width=LW * 2, seed=6):
    """白天商业街（宽图，可横向平移镜头）。返回 (图, shops=[(x, 宽, 颜色)])；人物脚底 y≈236。"""
    lo = Image.new("RGBA", (width, LH), (0, 0, 0, 255))
    d = ImageDraw.Draw(lo)
    for i in range(6):
        d.rectangle([0, 216 * i // 6, width, 216 * (i + 1) // 6], fill=mix((120, 170, 230), (250, 220, 170), i / 5) + (255,))
    r = random.Random(seed)
    shops, x = [], 6
    while x < width - 60:
        w = r.randint(70, 96)
        col = SHOP_COLS[len(shops) % len(SHOP_COLS)]
        d.rectangle([x, 96, x + w, 208], fill=(214, 200, 176, 255))
        d.rectangle([x, 92, x + w, 96], fill=(150, 130, 110, 255))
        d.rectangle([x + 6, 104, x + w - 6, 122], fill=(40, 30, 30, 255))
        for k in range(0, w, 10):
            d.rectangle([x + k, 126, x + k + 4, 136], fill=col + (255,))
            d.rectangle([x + k + 5, 126, x + k + 9, 136], fill=(245, 240, 230, 255))
        d.rectangle([x + 8, 142, x + w // 2 - 4, 196], fill=(140, 190, 220, 255))
        d.rectangle([x + w // 2 + 6, 150, x + w - 10, 208], fill=(110, 76, 50, 255))
        shops.append((x, w, col))
        x += w + r.randint(8, 16)
    d.rectangle([0, 208, width, LH], fill=(150, 146, 140, 255))
    d.rectangle([0, 208, width, 211], fill=(190, 186, 178, 255))
    for k in range(0, width, 24):
        d.rectangle([k, 230, k + 12, 231], fill=(120, 116, 112, 255))
    return lo, shops


def bg_live_room():
    """直播间：大屏（96..384 x 26..180）+ 两侧环形灯。"""
    lo = new_lo((16, 14, 22))
    d = ImageDraw.Draw(lo)
    d.rectangle([90, 20, 390, 186], fill=(30, 30, 40, 255))
    d.rectangle([96, 26, 384, 180], fill=(24, 40, 70, 255))
    d.rectangle([220, 186, 260, 200], fill=(40, 40, 50, 255))
    d.rectangle([190, 200, 290, 204], fill=(50, 50, 60, 255))
    floor(d, 206, (26, 22, 30), (44, 38, 50))
    for x in (40, 440):
        d.ellipse([x - 16, 60, x + 16, 92], outline=(250, 250, 240, 255), width=3)
        d.rectangle([x - 1, 92, x + 1, 206], fill=(80, 80, 90, 255))
    d.rectangle([100, 30, 130, 40], fill=(220, 50, 50, 255))
    return lo


def bg_chasm():
    """两座悬崖中间一道鸿沟（左崖顶 x<150，右崖 x>330，崖面 y=150）——表达“距离/门槛”。"""
    lo = new_lo()
    d = ImageDraw.Draw(lo)
    bands(d, 0, 200, (30, 30, 70), (200, 120, 110), 7)
    glow(lo, 240, 180, 70, (255, 210, 160), 60)
    d.polygon([(0, 150), (150, 150), (160, 162), (166, LH), (0, LH)], fill=(70, 56, 64, 255))
    d.polygon([(330, 150), (LW, 150), (LW, LH), (316, LH), (322, 162)], fill=(70, 56, 64, 255))
    d.rectangle([0, 148, 150, 152], fill=(110, 160, 90, 255))
    d.rectangle([330, 148, LW, 152], fill=(110, 160, 90, 255))
    bands(d, 236, LH, (30, 24, 40), (10, 8, 16), 3)
    return lo


def draw_bridge(lo, p):
    """在鸿沟上铺桥，p=0..1。"""
    d = ImageDraw.Draw(lo)
    for k in range(int(12 * p)):
        x = 152 + k * 15
        y = 150 + int(math.sin(k / 11 * math.pi) * 8)
        d.rectangle([x, y, x + 12, y + 6], fill=(200, 150, 70, 255))
        d.rectangle([x, y, x + 12, y + 1], fill=(250, 210, 120, 255))


def bridge_y(x):
    return 150 + int(math.sin(clamp((x - 152) / 165) * math.pi) * 8) if 152 < x < 317 else 150


def bg_hall():
    """暗色展厅（放展品、柱状图、八宫格）。"""
    lo = new_lo((12, 10, 16))
    d = ImageDraw.Draw(lo)
    for x in range(0, LW, 40):
        d.rectangle([x, 0, x + 6, 210], fill=(20, 16, 24, 255))
    floor(d, 210, (24, 18, 26), (46, 36, 50))
    for i in range(0, LW, 16):
        d.rectangle([i, 222, i + 7, 223], fill=(34, 26, 38, 255))
    return lo


def pedestal(lo, x, lit=0.0):
    d = ImageDraw.Draw(lo)
    cone(lo, (x, 0), (x - 40, 210), (x + 40, 210), (255, 220, 150), 22)
    d.rectangle([x - 26, 170, x + 26, 210], fill=(70, 56, 40, 255))
    d.rectangle([x - 30, 166, x + 30, 172], fill=(110, 90, 60, 255))
    if lit > 0:
        glow(lo, x, 140, 40 * lit, (255, 200, 90), 80)


def gold_bar(lo, x, height, w=40):
    """金色柱（成绩/收入柱状图）。"""
    d = ImageDraw.Draw(lo)
    cone(lo, (x + w // 2, 0), (x - 10, 210), (x + w + 10, 210), (255, 220, 140), 18)
    for y in range(210 - int(height), 210, 6):
        d.rectangle([x, y, x + w, y + 4], fill=(255, 196, 60, 255))
        d.rectangle([x, y, x + w, y], fill=(255, 236, 160, 255))
        d.rectangle([x + w - 2, y, x + w, y + 4], fill=(190, 120, 30, 255))


def bg_garage():
    lo = new_lo((70, 74, 82))
    d = ImageDraw.Draw(lo)
    d.rectangle([0, 0, LW, 20], fill=(50, 52, 60, 255))
    d.rectangle([20, 40, 150, 120], fill=(130, 110, 80, 255))
    for x in range(26, 146, 8):
        for y in range(46, 116, 8):
            d.point((x, y), fill=(90, 74, 54, 255))
    for k, (x, y) in enumerate([(40, 60), (70, 58), (100, 64), (126, 60)]):
        d.rectangle([x, y, x + 3, y + 30], fill=(190, 190, 200, 255))
        d.rectangle([x - 3, y, x + 6, y + 6], fill=(200, 60, 50, 255) if k % 2 else (190, 190, 200, 255))
    for k in range(0, LW, 30):
        d.rectangle([k, 20, k + 2, 210], fill=(60, 64, 72, 255))
    floor(d, 210, (90, 92, 98), (120, 122, 128))
    d.rectangle([0, 230, LW, 233], fill=(240, 200, 60, 255))
    return lo


def draw_car(d, cx, g, body=(60, 110, 200)):
    d.rectangle([cx - 56, g - 24, cx + 56, g - 8], fill=tuple(body) + (255,))
    d.polygon([(cx - 30, g - 24), (cx - 18, g - 40), (cx + 22, g - 40), (cx + 34, g - 24)], fill=mix(body, (0, 0, 0), .15) + (255,))
    d.rectangle([cx - 16, g - 37, cx + 20, g - 26], fill=(170, 210, 240, 255))
    for wx in (cx - 34, cx + 32):
        d.ellipse([wx - 9, g - 18, wx + 9, g], fill=(30, 30, 30, 255))
        d.rectangle([wx - 2, g - 11, wx + 2, g - 7], fill=(180, 180, 180, 255))


# ---------------------------------------------------------------- 横版闯关（人物经历最好用）
_rm = random.Random(8)
MOUNT = [(i * 40 + _rm.randint(-10, 10), _rm.randint(40, 80)) for i in range(60)]
CITY_STRIP = []
_x = 0
while _x < 4000:
    _bw = _rm.randint(16, 34)
    CITY_STRIP.append((_x, _bw, _rm.randint(20, 60)))
    _x += _bw + _rm.randint(2, 12)


def bg_dusk_sky():
    lo = new_lo()
    d = ImageDraw.Draw(lo)
    bands(d, 0, 216, (44, 34, 80), (236, 150, 96), 8)
    glow(lo, 360, 160, 60, (255, 220, 150), 80)
    d.ellipse([340, 140, 380, 180], fill=(255, 226, 150, 255))
    return lo


def draw_landmark(d, kind, x, t, g=216):
    """横版关卡里的地标：school 学校 / hoop 篮球架 / car 汽车展厅 / board 白板+学员 / tower 写字楼 / house 房子 / shop 小店"""
    if kind == "school":
        d.rectangle([x - 36, g - 52, x + 36, g], fill=(214, 196, 158, 255))
        d.polygon([(x - 42, g - 52), (x + 42, g - 52), (x, g - 74)], fill=(170, 60, 50, 255))
        for wx in (-26, -12, 6, 20):
            d.rectangle([x + wx, g - 40, x + wx + 7, g - 30], fill=(90, 130, 170, 255))
        d.rectangle([x - 6, g - 20, x + 6, g], fill=(100, 70, 50, 255))
        d.rectangle([x + 44, g - 90, x + 45, g], fill=(200, 200, 210, 255))
        d.rectangle([x + 46, g - 90, x + 60, g - 81 + int(t * 6) % 2], fill=(220, 50, 50, 255))
    elif kind == "hoop":
        d.rectangle([x - 2, g - 80, x + 1, g], fill=(140, 140, 150, 255))
        d.rectangle([x - 2, g - 80, x + 14, g - 78], fill=(140, 140, 150, 255))
        d.rectangle([x + 12, g - 96, x + 30, g - 76], fill=(245, 245, 250, 255))
        d.rectangle([x + 17, g - 88, x + 25, g - 80], outline=(220, 60, 50, 255))
        d.rectangle([x + 14, g - 76, x + 28, g - 75], fill=(240, 120, 40, 255))
        by = g - 6 - abs(math.sin(t * 5)) * 40
        d.ellipse([x - 22, by - 5, x - 12, by + 5], fill=(240, 120, 40, 255))
    elif kind == "car":
        d.rectangle([x - 56, g - 70, x + 56, g], fill=(70, 80, 100, 255))
        d.rectangle([x - 52, g - 60, x + 52, g - 4], fill=(120, 170, 210, 255))
        d.rectangle([x - 60, g - 80, x + 60, g - 68], fill=(30, 30, 40, 255))
        cx = x - 6
        d.rectangle([cx - 26, g - 16, cx + 26, g - 6], fill=(220, 50, 50, 255))
        d.polygon([(cx - 16, g - 16), (cx - 10, g - 26), (cx + 12, g - 26), (cx + 18, g - 16)], fill=(200, 40, 40, 255))
        for wx in (cx - 16, cx + 14):
            d.ellipse([wx - 5, g - 10, wx + 5, g], fill=(30, 30, 30, 255))
    elif kind == "board":
        d.rectangle([x - 30, g - 70, x + 30, g - 30], fill=(240, 240, 236, 255))
        d.line([(x - 22, g - 38), (x - 8, g - 50), (x + 4, g - 44), (x + 22, g - 64)], fill=(220, 60, 50, 255), width=2)
        d.rectangle([x - 22, g - 30, x - 20, g], fill=(90, 90, 100, 255))
        d.rectangle([x + 20, g - 30, x + 22, g], fill=(90, 90, 100, 255))
        for k, sx in enumerate((x + 44, x + 60, x + 76)):
            bob = int(t * 3 + k) % 2
            d.rectangle([sx - 4, g - 18 - bob, sx + 4, g - 10 - bob], fill=(30, 26, 30, 255))
            d.rectangle([sx - 5, g - 10, sx + 5, g], fill=[(60, 90, 160), (160, 70, 70), (70, 130, 80)][k] + (255,))
    elif kind == "tower":
        d.rectangle([x - 40, g - 140, x + 40, g], fill=(64, 78, 110, 255))
        for wy in range(g - 128, g - 10, 9):
            for wx in range(x - 30, x + 34, 9):
                d.rectangle([wx, wy, wx + 5, wy + 5], fill=(255, 210, 120, 255) if (wx + wy) % 3 else (120, 150, 190, 255))
        d.rectangle([x - 46, g - 158, x + 46, g - 140], fill=(30, 30, 40, 255))
        d.rectangle([x - 8, g - 18, x + 8, g], fill=(30, 26, 30, 255))
    elif kind == "house":
        d.rectangle([x - 34, g - 46, x + 34, g], fill=(230, 220, 200, 255))
        d.polygon([(x - 40, g - 46), (x + 40, g - 46), (x, g - 76)], fill=(200, 70, 60, 255))
        d.rectangle([x - 8, g - 24, x + 8, g], fill=(110, 76, 50, 255))
        d.rectangle([x - 26, g - 36, x - 14, g - 26], fill=(120, 170, 210, 255))
    elif kind == "shop":
        d.rectangle([x - 40, g - 70, x + 40, g], fill=(214, 200, 176, 255))
        d.rectangle([x - 34, g - 64, x + 34, g - 50], fill=(40, 30, 30, 255))
        for k in range(-40, 40, 10):
            d.rectangle([x + k, g - 46, x + k + 4, g - 38], fill=(200, 70, 60, 255))
        d.rectangle([x - 8, g - 30, x + 8, g], fill=(110, 76, 50, 255))


LANDMARK_TOP = {"school": 74, "hoop": 96, "car": 80, "board": 72, "tower": 158, "house": 76, "shop": 70}


class SideScroll:
    """横版闯关场景：主角原地走、世界向左滚，地标在指定时刻正好经过主角。
    landmarks=[(到达秒, 类型, 标牌文字)]"""

    def __init__(self, t0, landmarks, hero, v=100, hero_x=120, ground=216):
        self.t0, self.lm, self.hero, self.v, self.hx, self.g = t0, landmarks, hero, v, hero_x, ground
        self.sky = bg_dusk_sky()

    def wx(self, ev):
        return self.hx + self.v * (ev - self.t0)

    def lo(self, t, walking=True):
        lo = self.sky.copy()
        d = ImageDraw.Draw(lo)
        cam = self.v * (t - self.t0)
        d.polygon([(0, self.g)] + [(mx - cam * 0.15, self.g - mh) for mx, mh in MOUNT] + [(LW, self.g)], fill=(110, 70, 100, 255))
        for bx, bw, bh in CITY_STRIP:
            x = bx - cam * 0.4
            if -40 < x < LW:
                d.rectangle([x, self.g - bh, x + bw, self.g], fill=(82, 54, 86, 255))
        for ev, kind, _ in self.lm:
            x = self.wx(ev) - cam
            if -120 < x < LW + 120:
                draw_landmark(d, kind, int(x), t, self.g)
        d.rectangle([0, self.g, LW, LH], fill=(104, 70, 48, 255))
        d.rectangle([0, self.g, LW, self.g + 4], fill=(96, 150, 70, 255))
        off = int(cam) % 16
        for x in range(-off, LW, 16):
            for y in range(self.g + 12, LH, 12):
                d.rectangle([x + (y // 12 % 2) * 8, y, x + (y // 12 % 2) * 8 + 2, y + 1], fill=(80, 52, 36, 255))
        draw_char(lo, self.hero, self.hx, self.g, t, 2, walking=walking)
        return lo

    def labels(self, hi, t):
        cam = self.v * (t - self.t0)
        for ev, kind, text in self.lm:
            x = (self.wx(ev) - cam) * S
            if not text or not -200 < x < W + 200:
                continue
            y = (self.g - LANDMARK_TOP[kind] - 22) * S
            card(hi, t, ev - 0.25, x, y, text, 40, sfx="ding")


def bg_land():
    lo = bg_dusk_sky()
    d = ImageDraw.Draw(lo)
    d.polygon([(0, 216)] + [(mx, 216 - mh) for mx, mh in MOUNT[:14]] + [(LW, 216)], fill=(110, 70, 100, 255))
    d.rectangle([0, 216, LW, LH], fill=(104, 70, 48, 255))
    d.rectangle([0, 216, LW, 220], fill=(96, 150, 70, 255))
    return lo


def level_up_beam(lo, x, t, t0, ground=216):
    lp = prog(t, t0, 0.7)
    if 0 < lp < 1:
        lay = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
        ImageDraw.Draw(lay).rectangle([x - 26, 0, x + 26, ground], fill=(255, 220, 120, int(120 * (1 - lp))))
        lo.alpha_composite(lay)


def sparks(lo, t, t0, cx, cy, n=26, seed=2):
    sp = prog(t, t0, 0.8)
    if 0 < sp < 1:
        d = ImageDraw.Draw(lo)
        r = random.Random(seed)
        for _ in range(n):
            ang = r.random() * 6.28
            dist = 30 + 110 * ease_out(sp) * r.uniform(.6, 1)
            x, y = cx + math.cos(ang) * dist, cy + math.sin(ang) * dist * 0.4 - 20 * sp
            d.rectangle([x, y, x + 1, y + 1], fill=(255, 220, 120, int(255 * (1 - sp))))


# ================================================================ 时间轴
class Timeline:
    """读取 transcribe.py 生成的 words.json，提供查词和文稿对齐。"""

    PUNCT = set("，。！？；：、“”‘’（）《》,.!?;:\"'()[]【】…— \n\t")

    def __init__(self, words_json):
        words = json.load(open(words_json, encoding="utf-8"))
        chars = []
        for s0, e0, w0 in words:
            w0 = w0.strip()
            for i, c in enumerate(w0):
                chars.append((c, s0 + (e0 - s0) * i / max(1, len(w0)), s0 + (e0 - s0) * (i + 1) / max(1, len(w0))))
        self.chars = chars
        self.text = "".join(c for c, _, _ in chars)
        self.times = [t for _, t, _ in chars]
        self.end = words[-1][1] if words else 0

    def at(self, sub, after=0.0, default=None):
        """after 秒之后第一次说出 sub 的时刻。sub 要按识别稿的写法（同音错字也照抄）。找不到时返回 default 或报错。"""
        i0 = next((i for i, tt in enumerate(self.times) if tt >= after - 0.05), len(self.times))
        idx = self.text.find(sub, i0)
        if idx < 0:
            if default is None:
                raise ValueError(f"识别稿里 {after}s 之后找不到「{sub}」，请查 segments.txt 的写法或给 default")
            return default
        return round(self.times[idx], 2)

    def cue(self, sub, after=0.0):
        """按【正确文稿】的写法查词出现时刻（需先调用 align_script）。比 at() 省心：不用管识别错字。"""
        if not hasattr(self, "script_str"):
            raise RuntimeError("先调用 tl.align_script(文稿)")
        key = "".join(c for c in sub if c not in self.PUNCT)
        i0 = next((i for i, tt in enumerate(self.script_times) if tt >= after - 0.05), len(self.script_times))
        idx = self.script_str.find(key, i0)
        if idx < 0:
            raise ValueError(f"文稿里 {after}s 之后找不到「{sub}」")
        return round(self.script_times[idx], 2)

    def words_between(self, a, b):
        """a–b 秒之间说了什么（规划分镜时核对用）。"""
        return "".join(c for c, t, _ in self.chars if a <= t < b)

    def align_script(self, script, max_len=18):
        """把用户的正确文稿切成字幕句，并对齐到配音时间。返回 [(开始, 结束, 文本)]。
        识别错字不影响：按字符序列做模糊匹配，没对上的字按前后插值。"""
        clauses = []
        for sent in re.split(r"(?<=[。！？；\n])", script):
            sent = sent.strip()
            if not sent:
                continue
            pieces = [p for p in re.split(r"(?<=[，、：,])", sent) if p.strip()]
            cur = ""
            for p in pieces:
                if cur and len(cur) + len(p) > max_len:
                    clauses.append(cur)
                    cur = p
                else:
                    cur += p
            if cur:
                clauses.append(cur)
        s_chars, owner = [], []
        for ci, c in enumerate(clauses):
            for ch in c:
                if ch not in self.PUNCT:
                    s_chars.append(ch)
                    owner.append(ci)
        a_idx = [i for i, (c, _, _) in enumerate(self.chars) if c not in self.PUNCT]
        a_str = "".join(self.chars[i][0] for i in a_idx)
        sm = difflib.SequenceMatcher(None, "".join(s_chars), a_str, autojunk=False)
        mapped = [None] * len(s_chars)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                mapped[blk.a + k] = a_idx[blk.b + k]
        start_t = [self.chars[m][1] if m is not None else None for m in mapped]
        end_t = [self.chars[m][2] if m is not None else None for m in mapped]
        known = [i for i, v in enumerate(start_t) if v is not None]
        for i in range(len(start_t)):
            if start_t[i] is None:
                lo_i = max([k for k in known if k < i], default=None)
                hi_i = min([k for k in known if k > i], default=None)
                if lo_i is None and hi_i is None:
                    start_t[i] = end_t[i] = 0.0
                elif lo_i is None:
                    start_t[i] = end_t[i] = start_t[hi_i]
                elif hi_i is None:
                    start_t[i] = end_t[i] = end_t[lo_i]
                else:
                    k = (i - lo_i) / (hi_i - lo_i)
                    start_t[i] = end_t[i] = lerp(end_t[lo_i], start_t[hi_i], k)
        self.script_str, self.script_times = "".join(s_chars), start_t
        caps = []
        for ci, c in enumerate(clauses):
            ids = [i for i, o in enumerate(owner) if o == ci]
            if not ids:
                continue
            caps.append([round(start_t[ids[0]], 2), round(end_t[ids[-1]] + 0.15, 2), c.strip().rstrip("，。、；,;")])
        for i in range(len(caps) - 1):
            caps[i][1] = min(caps[i][1], caps[i + 1][0])
        return [tuple(c) for c in caps]


def mark_keywords(captions, keywords):
    """给字幕里的关键词加 {}（橙色高亮）。长词优先，避免“培训”先吃掉“培训基地”。"""
    out = []
    for a, b, text in captions:
        for k in sorted(keywords, key=len, reverse=True):
            if k in text and "{" + k not in text:
                text = text.replace(k, "{" + k + "}", 1)
        out.append((a, b, text))
    return out


# ================================================================ 音效 / BGM
SR = 44100


def tone(f, dur, vol, kind="square", f2=None):
    n = int(dur * SR)
    fr = np.linspace(f, f2 or f, n)
    ph = 2 * np.pi * np.cumsum(fr) / SR
    if kind == "square":
        w = np.sign(np.sin(ph)) * 0.6
    elif kind == "tri":
        w = 2 / np.pi * np.arcsin(np.sin(ph))
    elif kind == "noise":
        w = np.random.default_rng(1).uniform(-1, 1, n)
    else:
        w = np.sin(ph)
    env = np.ones(n)
    a = max(1, int(0.004 * SR))
    env[:a] = np.linspace(0, 1, a)
    env *= np.linspace(1, 0, n) ** 1.5
    return w * env * vol


class Mixer:
    def __init__(self, duration):
        self.buf = np.zeros(int((duration + 1) * SR))
        self.duration = duration

    def add(self, t, sig):
        i = int(t * SR)
        if 0 <= i < len(self.buf):
            seg = sig[:len(self.buf) - i]
            self.buf[i:i + len(seg)] += seg

    def arp(self, t, notes, step=0.07, vol=0.09, last=0.25):
        for k, f in enumerate(notes):
            self.add(t + k * step, tone(f, last if k == len(notes) - 1 else step * 1.3, vol))

    def save(self, path, fade_tail=1.2):
        fade = np.clip((self.duration - np.arange(len(self.buf)) / SR) / fade_tail, 0, 1)
        buf = np.clip(self.buf * fade, -1, 1)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            wf.writeframes((buf * 32767).astype(np.int16).tobytes())


SFX_KINDS = {   # 名称 → 合成方法（UI 组件的 sfx= 参数用这些名字）
    "pop": lambda m, t: (m.add(t, tone(880, 0.05, 0.05)), m.add(t + 0.04, tone(1320, 0.07, 0.045))),
    "tick": lambda m, t: m.add(t, tone(1760, 0.035, 0.035)),
    "ding": lambda m, t: (m.add(t, tone(1568, 0.05, 0.06)), m.add(t + 0.05, tone(2093, 0.18, 0.05))),
    "item": lambda m, t: m.arp(t, [523, 659, 784, 1047], 0.07, 0.08, 0.35),       # 获得道具
    "achieve": lambda m, t: m.arp(t, [784, 988, 1175, 1568], 0.07, 0.08),         # 成就解锁
    "levelup": lambda m, t: m.arp(t, [523, 659, 784, 1047, 1319, 1568], 0.055, 0.08, 0.35),
    "chord": lambda m, t: m.arp(t, [392, 523, 659, 784], 0.04, 0.07, 0.45),       # 标题/大字
    "stamp": lambda m, t: (m.add(t, tone(150, 0.16, 0.16, "sine", 60)), m.add(t, tone(0, 0.06, 0.03, "noise"))),
    "coin": lambda m, t: (m.add(t, tone(988, 0.06, 0.06)), m.add(t + 0.06, tone(1319, 0.16, 0.06))),
    "buzz": lambda m, t: m.add(t, tone(110, 0.22, 0.06, "square", 90)),          # 错误/失败
    "whoosh": lambda m, t: m.add(t, tone(0, 0.35, 0.04, "noise")),
    "rise": lambda m, t: m.add(t, tone(400, 0.95, 0.045, "tri", 1600)),           # 聚合/升起
    "rumble": lambda m, t: (m.add(t, tone(70, 0.9, 0.10, "tri", 50)), m.add(t, tone(0, 0.9, 0.025, "noise"))),
    "step": lambda m, t: m.add(t, tone(0, 0.025, 0.025, "noise")),
}

BGM_PROGS = [   # 每章轮换一组和弦，避免几分钟同一个循环
    [(220, [440, 523, 659]), (175, [349, 440, 523]), (131, [392, 523, 659]), (196, [392, 494, 587])],
    [(147, [294, 349, 440]), (196, [392, 494, 587]), (131, [392, 523, 659]), (220, [440, 523, 659])],
    [(175, [349, 440, 523]), (131, [392, 523, 659]), (196, [392, 494, 587]), (220, [440, 523, 659])],
]


def build_audio(path, duration, events, chapters=(), cuts=(), bgm=True, bgm_vol=1.0, extra=None):
    m = Mixer(duration)
    last = {}
    for t, kind in sorted(events):
        if t - last.get(kind, -9) < 0.06:
            continue
        last[kind] = t
        SFX_KINDS.get(kind, SFX_KINDS["pop"])(m, t)
    for c in cuts:
        m.add(c - 0.05, tone(0, 0.18, 0.02, "noise"))
    if extra:
        extra(m)
    if bgm:
        beat = 0.42
        for bar in range(int(duration / (beat * 4)) + 1):
            t0 = bar * beat * 4
            chap = sum(1 for ct, _ in chapters if t0 >= ct)
            root, ch = BGM_PROGS[chap % 3][bar % 4]
            for k in range(4):
                m.add(t0 + k * beat, tone(root / 2, beat * 0.9, 0.042 * bgm_vol, "tri"))
                for j, f in enumerate(ch):
                    m.add(t0 + k * beat + j * beat / 3, tone(f, beat / 3, 0.010 * bgm_vol))
    m.save(path)


# ================================================================ 渲染
class Video:
    """scenes=[(开始秒, 结束秒, lo函数(t)->低分辨率图, hi函数(hi, t))]"""

    def __init__(self, scenes, duration, captions, voice, out, chapters=(), chapter_start=None, cuts=None,
                 extra_sfx=None, bgm=True, bgm_vol=1.0, caption_until=None):
        self.scenes, self.duration, self.captions, self.voice, self.out = scenes, duration, captions, voice, out
        self.chapters, self.chapter_start = list(chapters), chapter_start
        self.cuts = cuts if cuts is not None else [s[0] for s in scenes[1:]]
        self.extra_sfx, self.bgm, self.bgm_vol = extra_sfx, bgm, bgm_vol
        self.caption_until = caption_until or duration
        self.black = Image.new("RGBA", (W, H), (0, 0, 0, 255))

    def frame(self, t):
        hi = None
        for a, b, flo, fhi in self.scenes:
            if a <= t < b:
                lo = flo(t)
                lo.alpha_composite(vignette())
                hi = lo.resize((W, H), Image.NEAREST)
                fhi(hi, t)
                break
        if hi is None:
            hi = self.black.copy()
        if self.chapters:
            chapter_bar(hi, t, self.chapters, self.chapter_start, self.caption_until)
        if t < self.caption_until:
            draw_caption(hi, t, self.captions)
        dark = max([clamp(1 - abs(t - c) / 0.1) for c in self.cuts] +
                   [1 - prog(t, 0, 0.3), prog(t, self.duration - 0.5, 0.5)])
        if dark > 0.01:
            hi = Image.blend(hi, self.black, dark)
        return hi.convert("RGB")

    def preview(self, times, out_png, cols=4, thumb=480):
        """多个时间点拼成一张预览网格图（自检用）。"""
        ims = [self.frame(float(t)).resize((thumb, thumb * 9 // 16)) for t in times]
        rows = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * thumb, rows * thumb * 9 // 16), (0, 0, 0))
        dr = ImageDraw.Draw(sheet)
        for i, im in enumerate(ims):
            x, y = (i % cols) * thumb, (i // cols) * thumb * 9 // 16
            sheet.paste(im, (x, y))
            dr.text((x + 6, y + 4), f"{float(times[i]):.1f}s", fill=(255, 255, 0))
        sheet.save(out_png)
        return out_png

    def render(self, t_from=0.0, t_to=None, out=None, log_every=10):
        """渲染 [t_from, t_to) 片段（默认全片）：先出无声画面，再合成配音 + 音效 + BGM。"""
        t_to = t_to or self.duration
        out = out or self.out
        ff = find_ffmpeg()
        base = os.path.splitext(out)[0]
        silent, sfx = base + "_video_only.mp4", base + "_sfx.wav"
        SFX_EVENTS.clear()
        p = subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                              "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                              "-pix_fmt", "yuv420p", silent], stdin=subprocess.PIPE)
        n0, n1 = int(t_from * FPS), int(t_to * FPS)
        for f in range(n0, n1):
            p.stdin.write(self.frame(f / FPS).tobytes())
            if f % (FPS * log_every) == 0:
                print(f"{f / FPS:7.1f}s / {t_to:.1f}s", flush=True)
        p.stdin.close()
        p.wait()
        build_audio(sfx, self.duration, SFX_EVENTS, self.chapters, self.cuts, self.bgm, self.bgm_vol, self.extra_sfx)
        inputs = ["-i", silent]
        if self.voice:
            inputs += ["-ss", str(t_from), "-i", self.voice]
        inputs += ["-ss", str(t_from), "-i", sfx]
        if self.voice:
            fc = ("[1:a]apad,aformat=channel_layouts=stereo[v];[2:a]aformat=channel_layouts=stereo[s];"
                  "[v][s]amix=inputs=2:normalize=0:duration=first,volume=3dB,alimiter=limit=0.95[a]")
        else:
            fc = "[1:a]aformat=channel_layouts=stereo,volume=3dB,alimiter=limit=0.95[a]"
        subprocess.run([ff, "-y", "-v", "error"] + inputs + ["-filter_complex", fc, "-map", "0:v", "-map", "[a]",
                        "-t", str(t_to - t_from), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", out], check=True)
        for tmp in (silent, sfx):
            try:
                os.remove(tmp)
            except OSError:
                pass
        print("done ->", out)
        return out

    def cli(self, argv=None):
        """python 你的脚本.py                      渲染全片
           python 你的脚本.py sample 0 25          渲染 0–25 秒样片（输出 *_sample.mp4）
           python 你的脚本.py preview 3 10 20 ...  导出预览网格图（*_preview.png）"""
        argv = sys.argv[1:] if argv is None else argv
        base = os.path.splitext(self.out)[0]
        if argv and argv[0] == "preview":
            print(self.preview(argv[1:], base + "_preview.png"))
        elif argv and argv[0] == "sample":
            a, b = float(argv[1]), float(argv[2])
            self.render(a, b, base + "_sample.mp4")
        else:
            self.render()


def audio_duration(path):
    ff = find_ffmpeg()
    probe = ff.replace("ffmpeg", "ffprobe")
    out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out)
