#!/usr/bin/env bash
# macOS / Linux 环境准备：需要 python3 + ffmpeg
set -e
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg
  elif command -v apt-get >/dev/null; then sudo apt-get install -y ffmpeg fonts-noto-cjk
  else echo "请先安装 ffmpeg"; exit 1; fi
fi
python3 -m pip install -q pillow numpy faster-whisper edge-tts
python3 -c "import PIL, numpy, faster_whisper; print('python OK')"
ffmpeg -version | head -n 1
