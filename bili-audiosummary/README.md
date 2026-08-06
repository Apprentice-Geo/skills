# bili-audiosummary

`bili-audiosummary` 根据 Bilibili 视频的字幕或音频生成总结、笔记、要点和时间戳，适合讲座、访谈、教程、播客、评论和解说等以语言内容为主的视频。

## 使用边界

它不负责分析画面、图表、屏幕文字、动作、评论区或封面，也不适合作为 PV、音乐、舞蹈等主要依赖视觉或声音表现而非讲述内容的视频分析工具。

## 隐私与安全

访问 Bilibili 需要网络连接，部分场景可能需要登录 Cookie，项目不会记录、保存或发送 Cookie，仅将其提供给 yt-dlp 完成视频资源下载。下载的音频、字幕和生成的总结都是本地用户数据，分享前请检查是否包含个人信息、版权内容或其他不宜公开的材料。

依赖源按官方 PyPI、清华、阿里的顺序配置，并使用 uv 的 `first-index` 策略；国内源作为可信的后续候选源。

## 进一步阅读

- [SKILL.md](SKILL.md)：完整使用规则、准备、继续、完成流程和停止条件。
- [references/ARCHITECTURE.md](references/ARCHITECTURE.md)：任务状态、模块边界和产物设计。
- [references/ERROR-HANDLING.md](references/ERROR-HANDLING.md)：下载、Cookie、字幕、转写接入和总结校验错误处理。
- [AGENTS.md](AGENTS.md)：开发维护、测试和代码规范。
