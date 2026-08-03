# audio-transcribe

`audio-transcribe` 用于将已经存在于本地的音频转换为可复用的文字稿和时间戳，也可供总结、字幕制作等后续 Skill 使用。

## 使用边界

- 输入音频必须已经存在于本地。
- Skill 不负责下载媒体，也不修改其他 Skill 的结果。

## 隐私与安全

运行环境配置需要访问网络，转写完全在本地环境中执行，但音频本身可能包含个人信息、机密对话或受版权保护的内容，分享转写文本、时间戳或结果目录前，请确认其中没有不应公开的原始内容、日志或路径信息。

## 进一步阅读

- [SKILL.md](SKILL.md)：完整使用规则、环境要求、命令和停止条件。
- [references/ARCHITECTURE.md](references/ARCHITECTURE.md)：转写流程、缓存、身份和公开产物设计。
- [references/ERROR-HANDLING.md](references/ERROR-HANDLING.md)：安装、模型、Provider、缓存和产物错误处理。
- [AGENTS.md](AGENTS.md)：开发维护、测试和代码规范。
