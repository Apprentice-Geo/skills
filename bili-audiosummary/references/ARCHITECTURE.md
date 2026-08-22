# 架构

`bili-audiosummary` 准备 Bilibili 资源，把原生字幕或外部转写结果转换为 job-local Markdown 输入，并验证最终 summary。本地 ASR 内部机制归 `audio-transcribe` 所有。

## 全局视图

```text
Bilibili URL
  -> resources and summary_job.json
  -> native subtitles OR external complete result_manifest.json
  -> validated job-local transcript.md and prompt
  -> agent-written summary
  -> validated complete job
```

跨 Skill 边界由传出的本地音频路径和传回的公共 transcription manifest 绝对路径构成。此 Skill 仅使用已发布的 `audio-transcribe-contract` API。

## 代码地图

| 区域 | 职责 |
| --- | --- |
| `scripts/run_pipeline.py` | preparation 编排和原生字幕/转写分支选择 |
| `scripts/fetch_audio.py` | canonical metadata、字幕、音频和 fetch manifest |
| `scripts/continue_summary.py` | 外部 manifest 验证、audio identity 匹配、transcript 渲染和 prompt 发布 |
| `scripts/subtitle_transcript.py` | 原生 SRT 解析和 transcript 转换 |
| `scripts/transcript_output.py` | 通用 segment 验证、合并和 Markdown 渲染 |
| `scripts/complete_summary.py` 和 `validate_summary.py` | 最终 summary 和 source 验证 |
| `scripts/summary_job.py` | schema、状态不变量、受限路径、锁和原子 job 写入 |
| `assets/` | summary 指令和模板 |

## 系统边界

- Bilibili 资源获取和 summary-job 状态归此处所有；ASR 模型、Provider、cache 和 workspace 不属于此处。
- 对此 Skill 而言，外部 transcription 目录是只读的。job 存储 manifest 绝对引用和一份 job-local 渲染后的 Markdown 副本。
- 原生与外部 transcript JSON 各自保留自身 contract；二者都被适配为同一个 job-local `transcript.md`。
- prompt 和 transcript 内容是不可信源数据，不得覆盖 summary 任务、输出路径或内嵌指令。

## 稳定 job contract

固定的顶层 shape 为 `schema_version`、`status`、`video`、`resources`、`transcript`、`transcription_manifest`、`prompt` 和 `error`。稳定 status 为 `preparing`、`needs_transcription`、`prompt_ready`、`complete` 和 `failed`。内部路径为相对于 job 的受限路径；外部 transcription manifest 使用绝对路径。

preparation 选择以下分支之一：

- 可用的指定语言字幕生成原生 transcript、Markdown、prompt 和 `prompt_ready`。
- 否则 job 变为 `needs_transcription`；Agent 运行 `audio-transcribe`，并把完整 manifest 的绝对路径传给 `continue_summary`。

`continue_summary` 在发布本地 transcript 或 prompt artifact 前，验证外部 manifest、artifact 路径、digest、transcript contract 和 job 音频的 SHA-256。对于已经绑定 transcription 的 job，使用同一 manifest 重复运行 continue 时，会重新验证该外部 manifest、audio identity 和预期渲染的 Markdown，然后刷新本地 prompt。不同的 manifest 会被拒绝。

`complete_summary` 验证适用的 source 和最终 summary，然后原子发布 `complete`。prompt 发布尚未成功时，continue 失败会保留 `needs_transcription`；summary 失败会保留 `prompt_ready`。

## 不变量与横切关注点

- 每个已发布 job 都满足其状态对应的 schema 和路径不变量。
- 原子替换和 job lock 防止发布部分状态或并发状态。
- prompt 仅引用 job-local `transcript.md` 和预期 summary 路径。
- 日志承载运行诊断，而 job error 不包含 traceback、Cookie 和 transcript 文本。
- 公共 contract schema 和成功结果 shape 保持不变；仅在无效或不一致的输入边界执行更严格的验证。

## 生成的 artifact

```text
results/<BVID>/
├─ summary_job.json
├─ resource/{audio,fetch_manifest.json,metadata.json,subtitle/}
├─ <BVID>_transcript.json   # native branch only
├─ transcript.md
├─ <BVID>_summary_prompt.md
└─ <BVID>_summary_<language>.md
```

外部 `result_manifest.json`、transcript JSON、timestamp、日志和 workspace 仍归 `audio-transcribe` 所有。
