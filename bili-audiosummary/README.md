# bili-audiosummary

`bili-audiosummary` 根据 Bilibili 视频的字幕或音频生成总结、笔记、要点和时间戳，适合讲座、访谈、教程、播客、评论和解说等以语言内容为主的视频。

## 使用边界

它不负责分析画面、图表、屏幕文字、动作、评论区或封面，也不适合作为 PV、音乐、舞蹈等主要依赖视觉或声音表现而非讲述内容的视频分析工具。

## 隐私与安全

访问 Bilibili 需要网络连接，部分场景可能需要登录 Cookie。脚本只把 Cookie 文件路径传给 yt-dlp；yt-dlp 仅在请求 Bilibili 视频元数据、字幕和音频时使用 Cookie。脚本不会把 Cookie 内容写入 `summary_job.json`、下载产物、总结、日志或错误报告。

Cookie 文件包含登录凭据，不应提交到版本控制或分享给无关人员。下载的音频、字幕和生成的总结都是本地用户数据，分享前请检查是否包含个人信息、版权内容或其他不宜公开的材料。

依赖源按官方 PyPI、清华、阿里的顺序配置，并使用 uv 的 `first-index` 策略；国内源作为可信的后续候选源。

## Cookies 导出

Bilibili 返回 `HTTP 412` 或请求需要登录态时，可从已登录 Bilibili 的浏览器导出 Netscape 格式的 Cookie 文件：

- Chrome：安装 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)，打开已登录的 Bilibili 页面后导出，文件会保存到下载目录。
- Edge：安装 [Cookie-Editor](https://microsoftedge.microsoft.com/addons/detail/cookieeditor/neaplmfkghagebokkhpjpoebhdledlfi)，打开已登录的 Bilibili 页面后选择 `Netscape` 格式导出，再将剪贴板内容保存为 `cookies.txt`。

将文件放到 Skill 根目录并命名为 `cookies.txt`、`www.bilibili.com_cookies.txt` 或 `bilibili_cookies.txt`，pipeline 会自动检测。使用其他文件名或位置时，通过 `--cookies` 显式指定：

```powershell
uv run --no-sync python -m scripts.run_pipeline `
  "<bilibili-url>" `
  --language zh `
  --cookies .\cookies.txt
```

Cookie 失效或被拒绝时，重新从已登录的 Bilibili session 导出 Netscape 格式文件。

## 进一步阅读

- [SKILL.md](SKILL.md)：完整使用规则、准备、继续、完成流程和停止条件。
- [references/ARCHITECTURE.md](references/ARCHITECTURE.md)：任务状态、模块边界和产物设计。
- [references/ERROR-HANDLING.md](references/ERROR-HANDLING.md)：下载、Cookie、字幕、转写接入和总结校验错误处理。
- [AGENTS.md](AGENTS.md)：开发维护、测试和代码规范。
