"""用户没有配音时，用微软 Edge 在线语音合成生成旁白（需要联网，pip install edge-tts）。
用法: python tts.py 文案.txt 输出.mp3 [--voice zh-CN-YunxiNeural] [--rate +10%]
常用中文音色：zh-CN-YunxiNeural（男·沉稳解说）、zh-CN-YunjianNeural（男·激昂）、
              zh-CN-XiaoxiaoNeural（女·亲和）、zh-CN-YunyangNeural（男·新闻播报）
"""
import argparse, asyncio


async def run(text, out, voice, rate):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("out")
    ap.add_argument("--voice", default="zh-CN-YunxiNeural")
    ap.add_argument("--rate", default="+10%")
    a = ap.parse_args()
    text = open(a.script, encoding="utf-8").read().replace("**", "")
    asyncio.run(run(text, a.out, a.voice, a.rate))
    print("saved", a.out)


if __name__ == "__main__":
    main()
