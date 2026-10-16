# vibe知识大赏-像素风格版

用代码生成“Vibe知识大赏”像素风中文知识解说视频的 Skill。

它可以把一个选题、文案、配音和参考素材，转换成包含以下元素的视频：

- 8-bit 像素场景
- 像素人物和角色卡
- 游戏化 UI
- 关键词字幕
- 成就、等级、技能树等动画
- 芯片音效和背景音乐
- 配音与画面时间轴同步

适合制作：

- 书籍解读
- 人物 / IP 拆解
- 品牌故事
- 知识科普
- 方法论总结
- “Vibe 知识大赏”风格视频

## 示例视频

| 工具 | 示例 |
|---|---|
| Claude | [打开 Claude 示例](examples/claude.mp4) |
| Codex | [打开 Codex 示例](examples/codex.mp4) |
| WorkBuddy | [打开 WorkBuddy 示例](examples/workbuddy.mp4) |
| 豆包 | [打开豆包示例](examples/doubao.mp4) |

这些示例都是同一套像素风视频工作流在不同工具中的输出效果。

## 工作流程

1. 输入选题、文案或参考素材。
2. Skill 分析内容并拆分章节、场景和视觉隐喻。
3. 设计像素角色、背景、道具和游戏化 UI。
4. 生成或导入配音。
5. 将字幕和动画绑定到配音时间轴。
6. 先渲染 20–30 秒样片。
7. 确认风格后，再渲染完整视频。

## 使用工具

- Claude Code
- Codex
- WorkBuddy
- 豆包（工作任务模式）

## Agent 安装提示词

```text
帮我安装这个 Skill：

https://github.com/YT-smart/pixel-explainer-video

Skill 名称：vibe知识大赏-像素风格版

安装完成后，告诉我如何使用它制作像素风知识解说视频。
```

## 仓库结构

```text
.
├── README.md
├── SKILL.md
├── references/
│   ├── production.md
│   ├── storyboard.md
│   └── style_guide.md
├── scripts/
│   ├── pixkit.py
│   ├── example_video.py
│   ├── transcribe.py
│   ├── tts.py
│   ├── setup_env.ps1
│   └── setup_env.sh
└── examples/
    ├── claude.mp4
    ├── codex.mp4
    ├── workbuddy.mp4
    └── doubao.mp4
```
