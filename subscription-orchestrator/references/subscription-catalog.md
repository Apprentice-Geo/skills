# ChatGPT Plus 与 Google AI Pro 订阅资源速查

当任务超出本地 CLI 编码/审查范围（例如视频、配乐、播客、解说视频、深度研究、GPU 计算或在线文档）时，查阅本文档选择订阅服务。具备浏览器能力的 Harness 可直接操控以下 Web 入口完成任务，并遵守文末的额度与安全约束。

## 能力与订阅服务对照

| 任务类型 | 推荐订阅服务 | 入口 | 归属订阅 |
| --- | --- | --- | --- |
| 复杂推理 / 联网问答 | ChatGPT（GPT-6.2）、Gemini（Gemini 4 Neon） | `https://chatgpt.com/`、`https://gemini.google.com/` | Plus / Pro |
| 多来源深度研究报告 | ChatGPT Deep Research、Gemini Deep Research | ChatGPT / Gemini 内置 | Plus / Pro |
| 网页端图片生成与编辑 | ChatGPT Images、Nano Banana（Gemini / AI Studio） | `https://chatgpt.com/images`、`https://gemini.google.com/`、`https://aistudio.google.com/` | Plus / Pro |
| 短视频生成、分镜、延长与超分 | Google Flow（主）、Gemini Video Generation | `https://flow.google/`、`https://gemini.google.com/` | Google AI Pro |
| 原创音乐、歌曲、BGM 配乐 | Google Flow Music（主）、Gemini Music、AI Studio（Lyria） | `https://www.flowmusic.app/`、`https://aistudio.google.com/` | Google AI Pro |
| 语音合成与音频生成 | Google AI Studio | `https://aistudio.google.com/` | Google AI Pro |
| 基于指定资料的播客、解说视频、PPT、信息图与报告 | Gemini Notebook（NotebookLM） | `https://notebooklm.google.com/` | Google AI Pro |
| 文档、电子表格、演示文稿与复杂在线办公 | ChatGPT Work、Gemini in Workspace | `https://chatgpt.com/`、Google Workspace | Plus / Pro |
| Python 数据分析与可视化 | ChatGPT Data Analysis、Google Colab | `https://chatgpt.com/`、`https://colab.research.google.com/` | Plus / Pro |
| GPU 计算与模型实验 | Google Colab（200 Compute Units，试用会员不含） | `https://colab.research.google.com/` | Google AI Pro |
| 视频剪辑制作 | Google Vids | `https://vids.google.com/` | Google AI Pro |
| 定时监控与周期性研究 | ChatGPT Scheduled Tasks、Gemini Tasks | ChatGPT / Gemini 内置 | Plus / Pro |
| 网页理解与浏览器自动化 | Antigravity、ChatGPT Work、Gemini in Chrome | Antigravity / ChatGPT / Chrome | Pro / Plus |

## 重点专项服务的额度与能力细则

### Google Flow（视频与专业视觉生成）

- 权益：每月 1,000 Google Flow Credits（按账单周期刷新，不结转）。
- 能力：Text to Video、Image to Video、Frames to Video、Ingredients to Video、Text to Image、Image to Image、视频延长与超分辨率。
- 参考消耗（以页面实时显示为准）：

| 模型 | 规格 | 参考消耗（Credits/次） |
| --- | --- | ---: |
| Veo 3.1 Lite | 4–8 秒，720p | 10 |
| Omni Flash 1.2 | 10 秒，360p | 10 |
| Omni Flash 1.2 | 10 秒，720p | 30 |
| Omni Flash 1.2 | 15 秒，720p | 60 |

### Google Flow Music（音乐与 BGM）

- 权益：包含 Flow Music Plus（需关联 Google One 订阅账户），使用独立于 Google AI Credits 的额度。
- 配额：每月 10,000 Credits（约 2,000 首歌曲）+ 每日额外补充，支持 12 路并发生成，含商业使用权。

### Gemini Notebook（资料驱动多模态创作）

- 单个 Notebook 最多支持 300 个来源。
- 可基于用户资料直接产出：Audio Overviews（播客/音频概述）、Video Overviews（解说视频）、Reports、Slides、Infographics、Flashcards 与 Quizzes。

### Google AI Studio 与开发者权益

- Google AI Pro 提升网页端 Gemini 4 Neon 与 Nano Banana Pro 使用限额，支持提示词实验、应用原型、Nano Banana 生图、Lyria 音乐与语音合成。
- Google Developer Program Premium：含每月 10 美元 Google Cloud Credits 与扩展开发工具权益。
- Google One：按订阅方案提供 5 TB 或 10 TB 云存储。

## 额度查询入口与浏览器操作规范

- **额度查询入口**：
  - Codex / ChatGPT Work 额度：`https://chatgpt.com/codex/settings/usage`
  - Gemini 使用限额：`https://gemini.google.com/` → Settings → Usage limits
  - Google AI Credits 管理：`https://one.google.com/ai/credits`
  - Antigravity 额度：模型选择器与 Settings 面板
- **浏览器自动化操作约束**：
  - 发起会消耗 Credits 的生成（尤其是 Google Flow 视频生成）前，先查看页面当前剩余订阅 Credits，评估单次与总消耗，避免耗尽后触发自动扣费的额外 Google AI Credits。
  - 若任务仅需少量音频/配乐或短片，优先选用成本更低的规格预览（例如 Veo 3.1 Lite 或 Omni Flash 1.2 360p 验证构图与节奏），确认效果后再生成高规格版本。
  - 下载或导出的产物统一保存到任务指定目录，并在汇报中交代来源服务、生成参数与本地文件路径。
  - 遇到未登录、二次验证、地区限制、人机验证（CAPTCHA）或付费页面时立即暂停并通知用户接管。
  - OpenAI API 与 Gemini API 均为独立计费，不包含在 ChatGPT Plus 或 Google AI Pro 订阅中。
