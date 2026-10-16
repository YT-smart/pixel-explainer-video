"""像素风解说视频 —— 项目模板（复制到项目目录，改成 video.py 后按需修改）

项目目录约定：
  voice.mp3                 配音（用户提供或 tts.py 生成）
  script.txt                正确文稿（用于字幕）
  timeline/words.json       transcribe.py 生成
  timeline/segments.txt     识别稿（查词的写法以它为准）
  photos/                   人物照片（可选）

用法:
  python video.py preview 2 8 15 30     预览网格图，自检构图/遮挡
  python video.py sample 0 25           先渲染 25 秒样片给用户看
  python video.py                       渲染完整视频

下面演示 5 种常用场景：数据钩子 / 栏目标题+人物卡 / 横版闯关讲经历 / 主讲人黑板点评 / 片尾。
真实项目里按文案逐段写场景，每个场景一对函数：xxx_lo(t) 画低分辨率画面，xxx_hi(hi, t) 画文字和 UI。
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("PIXKIT_DIR", HERE))      # pixkit.py 所在目录
import pixkit as px
from pixkit import S, W, H, GOLD, ORANGE, WHITE, GREY, INK, CHALK, RED, GREEN, prog, ease_out, ease_io, lerp, clamp

PROJECT = os.environ.get("PIXKIT_PROJECT", HERE)
VOICE = os.path.join(PROJECT, "voice.mp3")
OUT = os.path.join(PROJECT, "output.mp4")
tl = px.Timeline(os.path.join(PROJECT, "timeline", "words.json"))

# ---------------------------------------------------------------- 字幕：正确文稿 → 自动对齐，再手工给关键词加 {}
CAPTIONS = tl.align_script(open(os.path.join(PROJECT, "script.txt"), encoding="utf-8").read())
KEYWORDS = ["过亿", "500万", "2000平方米", "学科", "知识体系", "薛辉", "中专", "篮球", "汽车销售", "培训", "微博"]
CAPTIONS = px.mark_keywords(CAPTIONS, KEYWORDS)

# ---------------------------------------------------------------- 角色（按照片特征拼）
HERO = px.make_character(hair="spiky", glasses="square", top="tee", bottom="shorts", mouth="smile",
                         top_color=(240, 232, 212), bottom_color=(246, 204, 64))          # 主角
HOST = px.make_character(hair="slick", glasses="round", top="suit")                       # 主讲人
NPCS = px.random_npcs(8)
PHOTO = os.path.join(PROJECT, "photos", "hero.jpg")
HERO_PORTRAIT = px.photo_portrait(PHOTO, (0.1, 0.0, 0.92, 0.54), 60) if os.path.exists(PHOTO) else None
HERO_HEAD = px.big(px.photo_portrait(PHOTO, (0.28, 0.05, 0.66, 0.35), 18)) if os.path.exists(PHOTO) else None

# ---------------------------------------------------------------- 背景（构建一次，帧里 copy）
CITY, STAGE, CHALKROOM = px.bg_city_night(), px.bg_stage(), px.bg_chalk_room()

# ================================================================ 场景 1：数据钩子（夜景 + 成就解锁 + 计数器 + 大楼升起）
T_YI, T_500, T_BASE, T_2000 = tl.cue("过亿", 0), tl.cue("500", 2), tl.cue("沈阳", 3), tl.cue("2000", 4)


def s1_lo(t):
    lo = CITY.copy()
    px.stars(lo, t)
    if t > T_YI:
        px.glow(lo, 120, 180, 60 * ease_out(prog(t, T_YI, 0.5)), (255, 190, 60), 70)
    rp = ease_out(prog(t, T_BASE, 0.9))                       # 大楼升起
    if rp > 0:
        from PIL import ImageDraw
        d = ImageDraw.Draw(lo)
        h = int(120 * rp)
        d.rectangle([297, 216 - h, 393, 216], fill=(58, 52, 86, 255))
        for wy in range(216 - h + 10, 210, 10):
            for wx in range(305, 385, 10):
                d.rectangle([wx, wy, wx + 5, wy + 5], fill=(255, 206, 100, 255))
        px.sfx_at(T_BASE, "rumble")
    px.draw_char(lo, HERO, 120, 216, t, 2)
    return lo


def s1_hi(hi, t):
    val = px.counter(t, 0.8, T_YI - 0.8, 100_000_000, done="1亿+")
    px.card(hi, t, 0.5, W - 300, 100, f"年营收 {{{val}}}", 40, w=500, img=px.ICONS["coin"], sfx="tick")
    px.card(hi, t, T_YI, W / 2, 110, "{成就解锁}  年营收过亿", 44, img=px.ICONS["trophy"], sfx="achieve", out=T_500 + 0.8)
    px.label(hi, t, T_500, W - 300, 230, "-500万", 46, ORANGE, stroke=4, sfx="coin", out=T_500 + 1.2)
    px.card(hi, t, T_BASE + 0.1, 345 * S, 300, "沈阳", 40, border=RED, img=px.ICONS["pin"])
    px.card(hi, t, T_2000, 345 * S + 300, 520, "2000㎡", 60, border=ORANGE, color=GOLD, sfx="stamp")


# ================================================================ 场景 2：栏目标题 + 主讲人介绍人物
T_TITLE, T_NAME = tl.cue("拆解", 13), tl.cue("聊", 14)


def s2_lo(t):
    lo = STAGE.copy()
    if HERO_PORTRAIT is not None:
        px.framed(lo, HERO_PORTRAIT, 270, 110, ease_out(prog(t, T_TITLE, 0.45)))
    px.draw_char(lo, HOST, 110, 232, t, 3)
    px.sparks(lo, t, T_NAME + 0.3, 270, 200)
    return lo


def s2_hi(hi, t):
    px.badge(hi, t, T_TITLE, "百卓拆解")
    px.card(hi, t, T_TITLE, 1080, 110, "百大IP拆解", 52, border=ORANGE, fill=(90, 30, 14), color=GOLD, sfx="whoosh")
    px.bubble(hi, t, T_NAME, 640, 500, "今天聊", 38)
    px.card(hi, t, T_NAME + 0.3, 1080, 790, "薛 辉", 80, w=420, h=150, sfx="chord")


# ================================================================ 场景 3：横版闯关讲经历（地标在说到它时正好经过主角）
T3 = 16.45
LEVEL = px.SideScroll(T3, [(tl.cue("中专", 16), "school", "中专"), (tl.cue("篮球", 17), "hoop", "篮球"),
                           (tl.cue("汽车", 18), "car", "汽车销售"), (tl.cue("培训", 20), "board", "培训员工"),
                           (tl.cue("微博", 22), "tower", "微博")], HERO)
JOBS = [(T3, "学生", 1), (tl.cue("汽车", 18), "汽车销售", 2), (tl.cue("培训", 20), "培训师", 3), (tl.cue("微博", 22), "微博培训师", 4)]


def s3_lo(t):
    lo = LEVEL.lo(t)
    for jt, _, lv in JOBS[2:]:
        px.level_up_beam(lo, LEVEL.hx, t, jt)
    return lo


def s3_hi(hi, t):
    LEVEL.labels(hi, t)
    job, lv = [(j, l) for jt, j, l in JOBS if t >= jt][-1]
    px.player_hud(hi, t, T3 + 0.1, HERO_HEAD if HERO_HEAD is not None else px.big(HERO["idle"].crop((0, 0, 20, 16)), 4),
                  "薛辉", lv, job)
    for jt, job_name, lv in JOBS[2:]:
        px.card(hi, t, jt, LEVEL.hx * S + 80, 368, f"LEVEL UP!  {{{job_name}}}", 40, sfx="levelup", out=jt + 1.3)


# ================================================================ 场景 4：主讲人黑板点评（每个“拆解观点”段落用它）
T4 = 23.5


def s4_lo(t):
    lo = CHALKROOM.copy()
    px.draw_char(lo, HOST, 440, 214, t, 3)
    return lo


def s4_hi(hi, t):
    px.badge(hi, t, T4 + 0.1, "百卓拆解")
    px.label(hi, t, T4 + 0.3, 820, 200, "培训的基本要求", 46, CHALK, sfx="pop")
    for i, s in enumerate(["自己做得好", "讲清楚别人该怎么做"]):
        tt = T4 + 1.2 + i * 1.2
        px.label(hi, t, tt, 640, 330 + i * 120, s, 48, CHALK, anchor="l")
        px.icon_pop(hi, t, tt + 0.3, px.ICONS["check"], 570, 330 + i * 120, sfx="ding")
    px.arrow(hi, t, T4 + 4.0, 400, 640, 1200, 640, GOLD, 10)
    px.card(hi, t, T4 + 4.4, 820, 760, "{讲经验}", 52, border=ORANGE, fill=(70, 30, 10), sfx="chord")


# ================================================================ 片尾
def end_lo(t):
    return px.new_lo((8, 6, 12))


def end_hi(hi, t, t0=31.0):
    a = ease_out(prog(t, t0, 0.4))
    px.put(hi, px.txt("百大IP拆解", 64, GOLD), W / 2, 460, a)
    px.put(hi, px.txt("薛辉 · 把经验变成方法", 44, WHITE), W / 2, 570, a)


# ================================================================ 组装
DURATION = 32.5
SCENES = [(0, 13.88, s1_lo, s1_hi), (13.88, T3, s2_lo, s2_hi), (T3, T4, s3_lo, s3_hi),
          (T4, 31.0, s4_lo, s4_hi), (31.0, 99, end_lo, end_hi)]
CHAPTERS = [(T3, "起点"), (T4, "讲师之路")]

video = px.Video(SCENES, DURATION, CAPTIONS, VOICE, OUT, chapters=CHAPTERS, caption_until=31.0)

if __name__ == "__main__":
    video.cli()
