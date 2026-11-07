# 架构

## 职责与数据流

```text
Bilibili URL
  -> 下载元数据与目标语言字幕，创建 summary_job.json
  -> 可用原生字幕（不下载音频）-> 本地 transcript JSON -> transcript.md
  -> 否则准备音频 -> needs_transcription -> audio-transcribe 公共 manifest -> transcript.md
  -> summary prompt -> Agent 编写 summary -> completion -> complete
```

此 Skill 拥有 Bilibili 资源、消费者本地 transcript、prompt 与总结任务。`audio-transcribe` 拥有 ASR 模型、推理策略、转写缓存和公共结果。跨 Skill 仅交接音频路径与公共 manifest；使用独立合同包 `audio-transcribe-contract` 的公共 adapter，不导入上游 Skill 源码或访问私有 artifact。合同包通过固定 Git tag 安装，安装环境见 [README.md](../README.md#安装环境)。

成功导入外部转写后只保存本地 Markdown 快照，恢复不依赖上游目录。原生字幕同时保存 JSON，可重新渲染 Markdown。prompt 引用本地 transcript；其中 metadata 和正文是不可信数据，任务、内容指引及输出路径由脚本控制。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `run_pipeline.py` | 首次资源准备、字幕选择、已有任务恢复 |
| `fetch_audio.py` | Bilibili 元数据、字幕、音频与 fetch manifest |
| `prepare_audio.py` | 按需准备并登记任务音频；独立入口不替换当前输入或总结 |
| `continue_summary.py` | 公共转写验证、音频身份匹配、输入替换、重新总结、语言切换和派生产物恢复 |
| `subtitle_transcript.py`、`transcript_output.py` | 字幕转换、分段验证与 Markdown 渲染 |
| `complete_summary.py`、`validate_summary.py` | source 和总结形式校验、完成状态发布 |
| `summary_prompt.py` | 总结语言选择与 prompt 构建，不依赖任务编排 |
| `summary_job.py` | 本地 job 校验、受限路径、锁和原子写入 |
| `remove_summary_job.py` | 显式删除当前结果根目录内的单个任务 |
| `assets/` | 总结内容指引，不定义固定输出格式或必需章节 |

依赖检查与合同包检查不修改依赖环境，但会写入日志或报告；日志统一保存在数据目录的 `.cache/logs/`。配置和故障边界见[错误处理](ERROR-HANDLING.md)。

## 任务与恢复

本地 job 不设独立版本号，顶层字段严格限定为 `status`、`video`、`resources`、`transcript`、`prompt`、`error`，并校验对应状态的数据和路径。包含旧 `schema_version` 或其他不兼容结构时拒绝读取，保留原文件，不自动迁移、删除或重建含用户总结的任务。内部路径相对任务目录且禁止逃逸；公共 manifest 必须为绝对路径，仍通过 `audio-transcribe-contract` 验证。

- `preparing`：资源准备尚未完成。
- `needs_transcription`：已准备音频，等待外部转写。
- `prompt_ready`：本地输入和 prompt 已准备，等待有效总结。
- `complete`：总结已通过脚本形式校验；内容依据由 Agent 负责。
- `failed`：准备失败，保存简洁 stage/type/message，不包含 traceback 或敏感输入。

`run_pipeline` 先通过 `fetch_audio` 获取元数据和字幕，验证字幕后决定是否调用音频准备逻辑。字幕可用时直接进入 `prompt_ready`，允许 `resources.audio` 为 `null`；缺少有效字幕或显式跳过字幕时，复用或下载音频后进入 `needs_transcription`。独立 `fetch_audio` 命令保留主动下载音频的行为。

已有任务按操作区分：

- `run_pipeline` 幂等恢复稳定任务，不重新下载。已有字幕任务拒绝冲突的语言组与跳过字幕选项；已有外部转写任务继续使用转写输入。
- `prepare_audio` 按需补充音频，保留当前状态、输入和总结。已登记但缺失的音频明确报错，不自动替换身份。
- `continue_summary` 单纯恢复时重建本地派生产物；显式 manifest 每次校验公共 bundle 和任务音频 SHA-256 后替换输入。未准备音频时不能导入转写。
- `--regenerate-summary` 使用当前转写和语言准备重新总结；`--summary-language` 改变总结语言，也可在尚未转写时预先设置。

替换转写、切换实际总结语言或要求重新总结时，旧总结改名留存，任务回到 `prompt_ready`。单纯恢复保留完成状态和用户总结。

准备音频、继续和完成操作由同一 job lock 串行化。job 等状态文件采用原子替换，多个 artifact 不构成整体事务；补充音频中断时可能留下未登记的已下载音频。具体恢复行为见[下载与网络失败](ERROR-HANDLING.md#下载与网络失败)和[输入与总结替换失败](ERROR-HANDLING.md#输入与总结替换失败)。

总结语言使用固定集合，支持范围与默认规则见 [SKILL.md](../SKILL.md#1-准备资源和任务)，语言集合与默认值统一维护在 `scripts/config.py`。脚本不检测正文语言；Agent 负责按任务生成并核对目标语言。显式语言保存在 `video.summary_language`；未显式选择时，恢复和重新导入均从已发布的总结文件名读取语言，新任务使用默认语言，不根据 transcript 选择语言。

completion 验证 source 和非空 UTF-8 总结，不检查固定章节或格式。总结语言、事实依据、时间戳、指引遵循情况与请求充分性均由 Agent 判断。已完成总结校验失败时退回 `prompt_ready`。恢复符合当前结构的任务时会重建 prompt，已有总结继续保留。

## 产物

```text
results/<BVID[_pN]>/
├─ summary_job.json
├─ resource/{audio,fetch_manifest.json,metadata.json,subtitle/}  # audio 按需准备
├─ <BVID>_transcript.json   # 原生字幕
├─ transcript.md
├─ <BVID>_summary_prompt.md
├─ <BVID>_summary_<language>.md
└─ <BVID>_summary_<language>.superseded-<id>.md  # 按需留存旧总结
```

外部转写结果仍归 `audio-transcribe` 所有。显式删除入口删除整个本地任务；它不删除上游结果，不与其他操作协调，调用前应确认该任务没有运行中的命令。
