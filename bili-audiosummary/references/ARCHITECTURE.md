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
| `continue_summary.py` | 公共转写验证、音频身份匹配、输入替换、语言切换和派生产物恢复 |
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

`run_pipeline` 对稳定任务幂等恢复，不重新下载；已有原生字幕任务会拒绝冲突的语言组与跳过字幕选项，避免隐式改变或忽略输入选择。已有外部转写任务明确提示字幕选项不适用。`prepare_audio` 为稳定任务按需补充音频，保留现有状态、transcript、prompt 和总结；已登记但缺失的音频明确报错，不自动替换其身份。下载成功后依次发布 fetch manifest 和 job，发布中断可能留下已下载音频或更新的 fetch manifest；重试可复用音频完成登记，不影响原总结。该操作与继续、完成操作使用同一 job lock。

`continue_summary` 无 manifest 时恢复本地派生产物；显式 manifest 每次验证公共 bundle 和音频 SHA-256 后替换输入，不静默忽略。未准备任务音频时不能导入转写。`--summary-language` 只改变总结语言，未转写时也可预先设置。

替换转写或切换实际总结语言时，先成功撤销 `complete`，再将旧总结以及目标同名总结改名留存；多文件归档失败时回滚已经移动的文件。重新生成的 prompt 指向空缺的当前 summary 路径，避免误交付旧总结。单纯恢复保留完成状态和用户总结。任务操作由 job lock 串行化；各文件采用原子替换，多个 artifact 不构成整体事务。输入写入后若 prompt 或最终 job 发布失败，完成状态已经撤销，旧输入不保证原样保留；重新传入目标 manifest 恢复并校验，不能仅凭状态交付结果。

completion 验证 source 和非空 UTF-8 总结，不检查固定章节或格式。语言比例只是 warning；事实依据、时间戳、指引遵循情况与请求充分性仍由 Agent 判断。已完成总结校验失败时退回 `prompt_ready`。恢复符合当前结构的任务时会重建 prompt，已有总结继续保留。

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
