# 技术实现与踩坑记录

## 1. 渲染管线（`px.Video`）

每一帧：
1. 场景的 `lo(t)` 在 480x270 RGBA 上画背景、人物、像素道具（背景提前构建，帧里 `copy()`）。
2. 叠 vignette → 最近邻放大到 1920x1080。
3. 场景的 `hi(hi, t)` 在高分辨率层画文字、UI 面板、图标、箭头。
4. 章节条、字幕、闪黑转场。
5. 原始 RGB 帧通过管道交给 ffmpeg（libx264, crf 17）。

画完全部帧后：根据渲染时登记的音效事件合成 `*_sfx.wav`（音效 + BGM），再和配音混音（配音 + 音效直接相加，整体 +3dB，限幅 0.95），封装成 mp4。

速度：约 2 倍实时（Pillow 单线程）。4 分钟成片约 10 分钟，记得放后台。

## 2. 动画方法

- 全部是“时间 → 状态”的纯函数：`prog(t, 开始, 时长)` 得到 0–1，再套缓动（`ease_out` 进场、`ease_io` 移动、`back_out` 回弹弹出）。所以任何一帧都能单独渲染，`preview` 才能随机抽帧。
- 弹出卡片：透明度 + 缩放 0.6→1 回弹。飞行：起止坐标插值，弧线加 `sin(pi*p)*高度`。计数：`counter()`。
- 世界滚动（横版）：地标世界坐标 = 主角 x + 速度 × (到达时间 − 场景开始)，这样“说到学校时正好走到学校”。
- 镜头平移：宽背景图 `crop` 一个窗口，窗口 x 随时间变化（商业街）。

## 3. 音效

UI 组件的 `sfx=` 参数在元素第一次出现时登记事件 `(时间, 类型)`，渲染结束后统一合成，所以画面和音效天然同步。类型见 `px.SFX_KINDS`：pop/tick/ding/item/achieve/levelup/chord/stamp/coin/buzz/whoosh/rise/rumble/step。场景里也可以直接 `px.sfx_at(t, "coin")`。同类音效 60ms 内只响一次，避免叠爆。

需要特殊音效（比如走路脚步、连续金币）用 `Video(extra_sfx=函数)`，函数收到 `Mixer`，可调用 `m.add(t, px.tone(...))` / `m.arp(...)`。

## 4. BGM

`build_audio` 里生成：每拍 0.42 秒，三角波低音（根音降八度）+ 方波三连音琶音，音量很低。每进入一个新章节换一组和弦（`BGM_PROGS` 三组轮换）。用户不要 BGM：`Video(bgm=False)`；嫌大：`bgm_vol=0.5`。用户有自己的 BGM 文件时，在 `render` 后用 ffmpeg 再混一轨（BGM 音量约 -28dB，`sidechaincompress` 可在人声出现时自动压低）。

## 5. 时间轴

- `transcribe.py` 用 faster-whisper small 模型，`word_timestamps=True`。识别稿会有同音错字（薛辉→学会、猎奇→烈旗），**不要用识别稿当字幕**。
- `tl.align_script(正确文稿)`：用 difflib 把文稿字符序列和识别字符序列对齐，没对上的字按前后插值，得到每句字幕的起止时间（长句按逗号再切，每句 ≤18 字）。
- `tl.cue(词, 大约秒数)` 用正确写法查时间；`tl.at(词, 秒)` 用识别稿写法查；`tl.words_between(a, b)` 看某段说了什么。
- 数字读法不同（“五百万” vs “500万”）对不上时，cue 会落在插值时间上，误差通常 <0.3 秒，肉眼可接受；要精确就用 `tl.at` 查识别稿写法。

## 6. 踩过的坑（都已在脚本里处理）

| 问题 | 处理 |
|---|---|
| Windows Server 上 Python 官方安装器失败（0x80070003） | 改用嵌入版 zip + get-pip（setup_env.ps1） |
| 嵌入版 Python 不会把脚本目录加进 sys.path，`import` 同目录模块失败 | 脚本开头 `sys.path.insert(0, 脚本目录)` |
| faster-whisper 读 mp3 报 `metadata_errors` 参数错误（PyAV 版本不兼容） | 先用 ffmpeg 解码成 16k PCM，把 numpy 数组交给模型 |
| 拼接多色文字时段间出现空格 | 用 `font.getlength` 逐段推进绘制（`px.rich`） |
| 配音和音效混音后整体变小 3dB | 混音后 `volume=3dB` + `alimiter` |
| 文字和章节条/字幕/人物重叠 | 预览网格图自检；章节条占 y<44，字幕 y≈960–1040 |
| PowerShell 里 `2>&1` 包裹原生命令会误报失败 | 不重定向 stderr，或写到文件 |
| 多张照片人物弄反 | 开始前向用户确认谁是谁 |

## 7. 竖屏

把 `LW, LH = 270, 480`（pixkit 顶部常量）即可得到 1080x1920，但背景库是按横屏构图写的，竖屏需要重新安排场景布局（人物放下半部分、UI 放上半部分、字幕上移到 y≈1500）。用户要竖屏时先告诉他需要额外调整构图。
