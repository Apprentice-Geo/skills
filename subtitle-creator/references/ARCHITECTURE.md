# 架构

## 职责和数据流

```text
本地音频 → open_subtitle_job → 按音频 SHA-256 定位 job
已完成 manifest → 公共 load_result() → bind_transcription
  → 本地不可变基准 + 可编辑工作副本
工作副本 → transcript show / edit / reset → generate_srt → SRT
```

- `open_subtitle_job.py` 按音频内容创建或恢复任务，返回当前可用产物；不生成字幕。
- `transcription_input.py` 通过公共合同只读接入上游，`bind_transcription.py` 验证音频身份并导入本地数据；每次显式绑定都替换基准、清空校正并使字幕失效。
- `subtitle_job.py` 验证任务身份、产物路径、文本校正边界、时间轴与 SRT 内容，发布本地工作副本。
- `transcript.py` 查看、批量修改文本，或从本地基准恢复文本；`generate_srt.py` 从合法工作副本生成 SRT 并记录校正 ID。
- `remove_subtitle_job.py` 只删除当前配置根目录下单个任务，拒绝链接与越界。
- `runtime_paths.py` 解析数据目录，`process_logging.py` 管理固定位置的会话日志。环境与依赖检查不参与字幕业务状态。

## 持久化和提交

任务只有 `transcription_unbound`、`transcription_bound` 两个状态。绑定后保存本地基准、工作副本路径、基准 digest、`changed_segment_ids` 和可空的字幕路径；后续编辑、reset、生成不读取上游或原音频。同一任务的命令必须串行执行。

只有绑定写入新基准；编辑和 reset 复用原基准路径及 digest。整批修改先校验，再在独立 `transcript-<random>/` 目录写工作副本，最后原子切换 job 引用，时间轴和 metadata 必须与基准一致。工作副本结构、数值类型和时间轴合法且文本无变化时，直接返回当前路径，不写 job、不使字幕失效；全部 reset 遇到损坏副本时从可信基准重建。文本改变或副本重建时重新计算校正 ID 并将字幕声明置空；旧 SRT 可以残留，不能作为有效字幕交付。

写入或校验失败时清理候选文件，旧状态保持可用；提交异常可能发生在原子替换后，工具重新读取磁盘声明确认当前引用。确认新声明时清理旧工作副本，确认旧声明时清理候选文件，无法确认则保留两份并警告。异常仍抛出，恢复依据是磁盘 job，不是命令是否报错。正常提交后也只清理已替换的受管文件，保留仍被引用的基准和用户附加内容；不维护编辑历史。

受管文件清理仅限本任务直接子目录 `transcript-<32位小写十六进制>/` 内的转写文件及空目录，拒绝 symlink、junction 和越界路径。清理失败只警告，不回滚已提交任务。残留目录不是恢复依据，随整个任务删除。

打开、查看、编辑和单段 reset 要求基准与工作副本结构、时间轴合法，允许校正记录或 SRT 陈旧；全部 reset 只要求任务声明及基准可信，能修复缺失或损坏的工作副本。重新绑定只要求任务声明可信及新转写有效。生成重新验证时间轴与文本并更新校正记录；只有 SRT 与当前工作副本预期字节一致时，打开任务才返回其路径。

## 持久化兼容

job schema 为 `3`，转写 JSON schema 为 `2`。旧 job schema `2` 的 `needs_transcription`、`editable` 在读取时分别映射为当前两个状态，保留基准、校正和绝对产物路径；读取本身不改写。下一次实际写入 job 保存 schema `3`；无变化编辑/reset 不触发迁移。旧工具不能读取新 job，不接受未知版本，不迁移可再生缓存。

已验证 transcript 使用递归 JSON 序列化，有限 `Decimal` 直接写成 JSON 数字，保留 duration 和时间戳精度，不经 `float`。新旧任务均允许基准与工作副本位于不同目录，无需迁移存量文件。
