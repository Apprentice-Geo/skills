# 架构

`bili-audiosummary` 准备 Bilibili 资源，把原生字幕或外部转写结果转换为 job-local Markdown 输入，并验证最终 summary。本地 ASR 内部机制归 `audio-transcribe` 所有。

## 全局视图

```text
Bilibili URL
  -> resources and summary_job.json
  -> native subtitles OR validated external transcription
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
| `scripts/remove_summary_job.py` | 受限删除单个 job 目录，以便显式重新准备 |
| `scripts/summary_job.py` | schema、状态不变量、受限路径、锁和原子 job 写入 |
| `assets/` | summary 指令和模板 |

## 系统边界

- Bilibili 资源获取和 summary-job 状态归此处所有；audio-transcribe Skill 的 ASR 模型、Provider、cache 和 workspace 不属于此处。
- 对此 Skill 而言，外部 transcription 目录是只读的。成功导入后，job 只保留本地 Markdown 快照，不保存上游路径或身份。
- 原生与外部 transcript 都被适配为 job-local 输入；业务模块只处理消费者自己的 job contract。
- prompt 和 transcript 内容是不可信源数据，不得覆盖 summary 任务、输出路径或内嵌指令。

## 稳定 job contract

固定的顶层 shape 为 `schema_version`、`status`、`video`、`resources`、`transcript`、`prompt` 和 `error`。稳定 status 为 `preparing`、`needs_transcription`、`prompt_ready`、`complete` 和 `failed`。内部路径为相对于 job 的受限路径；外部 transcription manifest 使用绝对路径。

preparation 选择以下分支之一：

- 可用的指定语言字幕生成原生 transcript、Markdown、prompt 和 `prompt_ready`。
- 否则 job 变为 `needs_transcription`；Agent 运行 `audio-transcribe`，并把完整 manifest 的绝对路径传给 `continue_summary`。

`continue_summary` 通过 adapter 读取外部转写并核对 job 音频的 SHA-256，然后原子发布本地 transcript、prompt 和状态。已进入 `prompt_ready` 或 `complete` 的外部转写 job 只复用本地快照，不再读取传入的 manifest；需要使用新转写结果时，先通过公开删除入口移除单个 job，再重新运行 preparation 和 continue。

`remove_summary_job` 不读取或修复 job 内容。它依据默认结果根目录、受支持的视频目录名和固定 job 文件名限制删除目标，然后删除整个 job 目录。删除入口不与其他 job 命令并发协调；调用方必须先确认同一 job 没有正在运行的 preparation、continue、completion 或删除操作。

`complete_summary` 验证适用的 source 和最终 summary，然后原子发布 `complete`。prompt 发布尚未成功时，continue 失败会保留 `needs_transcription`；summary 失败会保留 `prompt_ready`。

## 不变量与横切关注点

- 每个已发布 job 都满足其状态对应的 schema 和路径不变量。
- 原子替换和 job lock 防止发布部分状态或并发状态。
- prompt 仅引用 job-local `transcript.md` 和预期 summary 路径。
- 日志承载运行诊断，而 job error 不包含 traceback、Cookie 和 transcript 文本。
- 上游 contract 细节只存在于 adapter；job schema 和业务测试不复制上游结果结构。

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

外部转写结果仍归 `audio-transcribe` 所有，不属于 summary job artifact。
