# bili-audiosummary

`bili-audiosummary` 根据 Bilibili 视频的字幕或音频生成总结、笔记、要点和时间戳，适合讲座、访谈、教程、播客、评论和解说等以语言内容为主的视频。

## 使用边界

它不负责分析画面、图表、屏幕文字、动作、评论区或封面，也不适合作为 PV、音乐、舞蹈等主要依赖视觉或声音表现而非讲述内容的视频分析工具。

优先使用可用字幕，只有需要转写时才准备音频，已有字幕任务也可按需补充音频。总结默认使用中文，也可选择其他受支持的语言；内容按用户请求组织，不要求固定章节或模板。默认关注要点见[内容指引](assets/summary_instructions.md)，支持语言和操作方式见 [SKILL.md](SKILL.md)。

## 安装环境

使用 Windows、Python 3.12、uv 和 Git。合同包通过 GitHub 固定 tag 安装，首次安装需要访问 GitHub；本 Skill 可独立安装，不要求与 `audio-transcribe` 保留相邻目录关系。

## 本地运行数据

运行日志、报告、缓存和结果可保存在指定的可写数据目录，默认仍使用 Skill 根目录。切换位置不会自动迁移历史任务；结果与日志可能含有私人内容和本地路径，分享前应检查。已有任务可复用资源恢复或按新要求重新总结；需要替换总结时，旧内容会改名留存。配置和操作见 [SKILL.md](SKILL.md)，写入错误与恢复影响见 [错误处理](references/ERROR-HANDLING.md#运行目录与写入失败)。

## 隐私与安全

访问 Bilibili 需要网络连接，部分场景可能需要登录 Cookie。脚本只把 Cookie 文件路径传给 yt-dlp；yt-dlp 仅在请求 Bilibili 视频元数据、字幕和音频时使用 Cookie。脚本不会把 Cookie 内容写入 `summary_job.json`、下载产物、总结、日志或错误报告。

Cookie 文件包含登录凭据，不应提交到版本控制或分享给无关人员。下载的音频、字幕和生成的总结都是本地用户数据，分享前请检查是否包含个人信息、版权内容或其他不宜公开的材料。

依赖源按官方 PyPI、清华、阿里的顺序配置，并使用 uv 的 `first-index` 策略；国内源作为可信的后续候选源。

## Cookies 导出

Bilibili 返回 `HTTP 412` 或请求需要登录态时，可从已登录 Bilibili 的浏览器导出 Netscape 格式的 Cookie 文件：

- Chrome：安装 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)，打开已登录的 Bilibili 页面后导出，文件会保存到下载目录。
- Edge：安装 [Cookie-Editor](https://microsoftedge.microsoft.com/addons/detail/cookieeditor/neaplmfkghagebokkhpjpoebhdledlfi)，打开已登录的 Bilibili 页面后选择 `Netscape` 格式导出，再将剪贴板内容保存为 `cookies.txt`。

导出文件的存放位置、自动检测文件名和 `--cookies` 配置统一见 [Cookie 故障处理](references/ERROR-HANDLING.md#http-412-与-cookie)。

Cookie 失效或被拒绝时，重新从已登录的 Bilibili session 导出 Netscape 格式文件。

## 进一步阅读

- [SKILL.md](SKILL.md)：完整使用规则、准备、继续、完成流程和停止条件。
- [references/ARCHITECTURE.md](references/ARCHITECTURE.md)：任务状态、模块边界和产物设计。
- [references/ERROR-HANDLING.md](references/ERROR-HANDLING.md)：下载、Cookie、字幕、转写接入和总结校验错误处理。
- [AGENTS.md](AGENTS.md)：开发维护、测试和代码规范。
