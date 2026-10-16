"""配音 → 逐字时间轴。
输出：
  words.json     [[开始秒, 结束秒, "词"], ...]   —— pixkit.Timeline 读取
  segments.txt   每句一行 "开始-结束 文本"        —— 人工查看识别稿写法、规划分镜用
用法: python transcribe.py 配音.mp3 输出目录 [--model small] [--prompt "专有名词提示"]
首次运行会从 HuggingFace 下载模型（small 约 460MB）。
"""
import argparse, json, os, shutil, subprocess, sys

import numpy as np


def find_ffmpeg():
    for c in [os.environ.get("PIXKIT_FFMPEG"), shutil.which("ffmpeg"),
              os.path.join(os.path.expanduser("~"), "tools", "ffbin", "ffmpeg.exe")]:
        if c and os.path.exists(c):
            return c
    sys.exit("找不到 ffmpeg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("outdir")
    ap.add_argument("--model", default="small")
    ap.add_argument("--prompt", default="", help="专有名词/人名，提高识别准确率")
    a = ap.parse_args()
    from faster_whisper import WhisperModel
    os.makedirs(a.outdir, exist_ok=True)
    # 直接解码成 PCM 交给模型：绕开 faster-whisper 自带解码器与 PyAV 版本不兼容的问题
    raw = subprocess.run([find_ffmpeg(), "-v", "error", "-i", a.audio, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    model = WhisperModel(a.model, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(audio, language="zh", word_timestamps=True, initial_prompt=a.prompt or None)
    words, lines = [], []
    for s in segs:
        for w in s.words:
            words.append([round(w.start, 2), round(w.end, 2), w.word])
        lines.append(f"{s.start:7.2f}-{s.end:7.2f} {s.text.strip()}")
        print(lines[-1], flush=True)
    json.dump(words, open(os.path.join(a.outdir, "words.json"), "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(a.outdir, "segments.txt"), "w", encoding="utf-8").write("\n".join(lines))
    print(f"\n共 {len(words)} 个词，时长 {words[-1][1] if words else 0:.1f}s ->", a.outdir)


if __name__ == "__main__":
    main()
