# 架构

`audio-transcribe` 把一个本地音频文件转换为经过验证、可复用的公共结果。本文档面向维护者说明稳定边界；实现细节以模块和测试为准。

## 全局视图

```text
local audio
  -> identity and runtime resolution
  -> Provider candidates and alignment acceptance
  -> merged and normalized workspace result
  -> segmentation and manifest-last publication
  -> validated manifest.json
```

只有完整公共结果验证成功后，命令才输出 manifest 路径。结果仅能通过该 manifest 和 `audio-transcribe-contract` loader 复用。

## 代码地图

| 区域 | 职责 |
| --- | --- |
| `scripts/transcribe.py` | CLI 编排、audio identity、provider 选择、variant identity、锁、cache 和报告 |
| `scripts/asr/alignment.py` | 唯一的 alignment 核心：`AlignmentItem`、`AlignedTranscript`、`CleanupReport`、固定的 `ALIGNMENT_POLICY`，以及接受、投影、偏移和验证 |
| `scripts/asr/segmentation.py` | 对已经验证的 global alignment 进行句子分段 |
| 其他 `scripts/asr/` 模块 | 音频准备、VAD、chunk 规划、Provider 执行、cache、合并和 workspace 输出 |
| `scripts/artifacts.py` | manifest-last 发布、公共 artifact 恢复、锁和自验证 |
| `scripts/model_artifacts.py` | 保守的本地模型 ready 检查，包括 indexed safetensors |
| `scripts/setup/` | Windows 环境和固定 revision 的模型安装 |
| `packages/audio-transcribe-contract/` | 面向 consumer，对公共 manifest 和 artifact 进行严格的只读验证 |
| `tests/` | 行为与 contract 的回归覆盖 |

## 系统边界

- 输入是本地音频文件；此 Skill 不下载媒体。
- `workspace/` 是私有恢复状态。`manifest.json` 和 `transcript.json` 构成可独立移动的公共 bundle；日志和 workspace 均非公共依赖。
- consumer 通过 `audio-transcribe-contract` 读取公共结果；不得 import 此 Skill 的源码或检查 `workspace/`。
- 模型 setup 和 readiness 属于本地职责。model index 格式错误时，应报告模型不可用，不得让异常逃逸到 setup 或依赖检查中。

## 稳定不变量

- `audio_id` 是音频字节的 SHA-256，与其路径无关。
- `config_digest` 是排除该字段后的 canonical request JSON SHA-256，标识所有可能改变 transcript 字节或 timestamp 的 resolved behavior；不包含音频身份，也不是单次调用编号。canonical request 包含模型 revision、执行策略、VAD、规划、分句、文本规范化、固定 `alignment_policy` 和 `public_schema_version: 2`。`audio_id + config_digest` 共同定位结果。
- `ALIGNMENT_POLICY` 使用 schema v1、1 ms timestamp resolution、`drop_item_and_owned_text` zero-duration 处理和严格排序。policy 变化会生成新的 `config_digest` 和 ASR plan identity。
- 所有语言都使用 NFKC，仅 `zh` 额外使用 OpenCC `t2s`，因此 normalization policy 变化也会生成新的 `config_digest`。
- 完整 manifest 最后发布，并且是唯一的成功标记。
- 公共 artifact 路径必须位于结果目录内；indexed model shard 路径必须位于模型目录内。
- 完整有效且身份匹配的 bundle 直接复用。损坏 bundle 可在同一音频与配置身份下重新发布；重建 digest 相同时保留原 manifest 字节，不同时仅更新其正文 digest。重建失败不覆盖已有公共文件。

## 横切关注点

- 原子写入和 variant lock 防止发布与恢复受到部分更新或并发更新影响。
- contract 验证检查 schema、identity、路径包含关系、digest、timing 和文件类型，不修复文件。
- 日志包含运行诊断，但不包含 transcript 文本、Cookie 或模型对象；面向 job 的错误保持简洁。
- Provider 选择在推理前完成解析，失败后不得静默更改。

## Alignment 与验证流程

Provider adapter 仅把第三方字段映射为 `AlignedTranscript` candidate。随后，每个 candidate 都必须经过同一个 `accept_provider_transcript` 边界：

1. 拒绝负数、非有限、反向、重叠、超出范围或 probability 无效的值；
2. 把 timestamp 量化到小数点后三位（1 ms）；
3. 按 source 顺序把源字符映射给 alignment-item owner，允许没有 owner 的标点和空白，不使用全局文本替换；
4. 仅移除量化后 `start == end` 的 item，以及恰好归这些 item 所有的源字符；
5. 严格重新验证字符 alignment、`0 <= start < end <= duration`、不重叠和 probability。

如果只剩可移除 item 和没有 owner 的标点或空白，一个 accepted chunk 可能变为空。合并时忽略空 chunk，但合并结果完全为空的转写必须失败。

合并流程只生成一个 global `AlignedTranscript`：应用每个 chunk offset，组合非空的 accepted chunk，并验证全局结果。写入 `workspace/result.json` 前，通过 item ownership 反向投影文本规范化结果，并再次进行严格验证。发布流程严格读取并重新验证 workspace shape 和 alignment；既不强制转换字段类型，也不把 timestamp 裁剪到音频 duration。句子分段仅在公共转换期间运行一次，并使用已经验证的 global alignment。

对于 Qwen3-ASR，alignment-item probability 保持 `null`。其他被接受的 probability 必须是有限值，并位于 `[0, 1]` 内。

## 私有 cache

私有 cache 只支持当前代码能够严格解析的格式，不承担 schema 兼容或迁移责任。`asr_plan.json` 通过 canonical plan payload 的 SHA-256 `plan_id` 绑定 source、Provider request、execution policy、VAD、规划参数和 chunk layouts；读取时重新计算身份并验证精确字段、类型和 layout 不变量。旧 schema、未知字段、错误 policy 或损坏数据自然成为 cache miss。

`vad_result.json` 只是 plan miss 时的可选输入。合法 plan 是 chunk 恢复的唯一权威，命中时不读取 VAD，也不从 VAD 重新推导 layout。chunk payload 仅保存 `plan_id`、`chunk_index`、accepted text 和 items；边界由 plan 恢复。Provider metadata、elapsed timing 和 `CleanupReport` 不落盘，因此 cleanup warning 只为本次新接受的 candidate 输出，不为 cached chunk 重放。

## 公共结果结构

```text
results/<audio_id>/<provider>-<language>-<config_digest>/
├─ manifest.json
├─ transcript.json
├─ transcribe.log
└─ workspace/
   ├─ asr_plan.json
   ├─ vad_result.json
   ├─ chunk_results/
   └─ result.json
```

manifest 记录 audio identity、resolved request identity、受限于目录内的 `transcript.json` 相对路径及其 SHA-256；不记录日志或 workspace。`transcript.json` 只保留一份 `schema_version`、`audio_id`、`config_digest`、Provider、language 和 duration，同时包含句子级 `segments`（id/start/end/text）及细粒度 `items`（text/start/end/probability）。句子文本来自完整规范化文本，不能假定简单拼接 item 文本可恢复全部标点和空白。

`audio_id + config_digest` 表示音频与已解析配置组合；`artifact_sha256.transcript` 标识某次发布的整个正文文件字节，包括元数据、segments、items 和 JSON 格式。损坏修复可改变后者，不改变配置摘要算法、结果定位或转写生成算法。需要固定历史结果的消费者应保存独立 bundle；生产端不维护发布历史或备份归档。

两份公共 JSON 保持字节及相对路径不变即可复制或移动到任意目录，consumer 无需访问原音频、模型、日志或 workspace。仅携带 bundle 的环境不具备本地恢复资料。生产端通过输入音频重新计算 audio identity 和 resolved request，定位本地 `results/<audio_id>/<provider>-<language>-<config_digest>/workspace/`，不根据公共 manifest 搜索或读取私有路径。自动解析后的配置变化会选择不同结果目录，不跨配置猜测或复用 workspace。

`workspace/result.json` 是 pipeline 唯一的合并结果，也是发布所使用的唯一私有 recovery snapshot。它仅包含 `audio_id`、`config_digest`、`text`、`items`、`duration`、`provider` 和 `language`，并在读取、发布和恢复时与当前请求逐项验证。

plan 和各 chunk 的 Provider 结果仍为独立的 workspace cache。当所有 chunk cache 都有效时，pipeline 会重新执行合并、timestamp offset、alignment 验证、文本与item 规范化以及 alignment 复验，然后原子替换`result.json`；此过程不加载 Provider。workspace 不存储 segment。包含 `plan`、`words` 或 `segments` 的旧版合并结果不会被读取或迁移。

私有 `chunk_results/` 保留 Provider 文本。`workspace/result.json` 和公共正文 JSON 仅包含规范化文本。规范化失败或规范化后的 alignment 失败会停止发布；恢复流程从已规范化的 workspace snapshot 确定性重建结果。

pipeline 不写入 `progress.json` 或 `metrics.json`，也不会删除历史遗留的这两个文件。进度由 plan、合法 chunk 文件和本次日志推导；diagnostics 通过进程内 `PipelineOutcome` 返回给 benchmark，不属于公开结果合同。

## 公共 contract 与发布

`audio-transcribe-contract` 0.2.0 只接受公共 schema v2，独立于内部 alignment 模块。固定 alignment policy 的内部版本仍为 1；contract 拥有独立副本。manifest 与正文之间的 identity、canonical request digest、正文 digest、Provider、language、duration 和路径包含关系必须一致。`artifacts` 和 `artifact_sha256` 只允许 `transcript` 键；alignment item 使用精确键集合和严格 timing/probability 验证。其他节点沿用已知字段验证，不在此次格式升级中统一收紧未知字段规则。

`load_result(path)` 完整验证后返回 `TranscriptionResult(manifest_path, transcript_path, manifest, transcript)`，路径均为绝对路径。正文 snapshot 同时提供 `segments` 和 `items`。外层 dataclass 冻结，内层 TypedDict/list 是普通可变内存对象，修改不写回文件。不再导出 `RawTimestamps` 类型或返回独立 timestamp 路径/snapshot。

`load_manifest(path)` 只验证 manifest 元数据、配置摘要、正文摘要格式及路径安全性，返回 manifest snapshot；不要求正文存在，不读取正文。它用于生产端决定恢复约束，不能代替 `load_result()` 认证完整结果。

旧公共 schema v1、旧入口 `result_manifest.json` 和 `variant_id` 字段不兼容，不自动迁移或删除。生产 request 的 `public_schema_version` 参与配置摘要，保证新格式选择新结果目录；旧私有 snapshot 也因字段不匹配而失效。

发布和恢复在同一个 result lock 内进行。先在结果目录下的临时 staging 目录生成完整两文件 candidate，保持最终相对路径，由 `load_result()` 验证后才替换正式正文，最后原子替换 `manifest.json`。candidate 失败不改变已有公共文件；最终 manifest 安装失败时尝试回滚正文。多文件替换不是整体原子事务：进程被强制终止时可能留下需要下次运行验证和恢复的状态，consumer 必须每次完整验证。

生产端复用与恢复按以下顺序执行：

1. 验证 manifest 元数据。有效但与当前 audio/request 不匹配时停止，不覆盖其他身份结果。
2. 若完整 bundle 验证通过，直接复用，不检查私有文件，也不改写日志。
3. 否则严格验证固定本地路径的 `workspace/result.json`；无效或缺失时进入 pipeline，从合法 plan/chunks 重建，缓存不足时执行正常推理。此路径同样适用于已有 manifest 的结果。
4. manifest 元数据有效且重建 digest 相同时，精确恢复并保留原 manifest 字节；digest 不同时允许重新发布，以原 manifest 为基础仅更新 `artifact_sha256.transcript`，保留 artifact 相对路径及其他元数据，不原地修改原文件或 loader snapshot。
5. manifest 缺失或损坏时，根据当前音频信息、resolved request 和合法 workspace 生成正文及 manifest，使用默认 `transcript.json` 路径，不承诺与历史结果逐字节相同。

恢复仍从规范化 snapshot 确定性生成句子和 items。重建或 candidate 验证失败，保留原有公共文件供后续检查；只有完整候选验证通过后才安装正文与更新后的 manifest。`publish_result()` 默认拒绝覆盖已有 manifest；生产命令通过现有 `replace_existing=True` 进入受控修复，不提供新的强制覆盖接口。成功发布后的诊断见 [错误处理](ERROR-HANDLING.md#日志)。
