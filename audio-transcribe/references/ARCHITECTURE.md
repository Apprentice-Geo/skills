# 架构

`audio-transcribe` 把一个本地音频文件转换为经过验证、可复用的公共结果。本文档面向维护者说明稳定边界；实现细节以模块和测试为准。

## 全局视图

```text
local audio
  -> identity and runtime resolution
  -> Provider candidates and alignment acceptance
  -> merged and normalized workspace result
  -> segmentation and manifest-last publication
  -> validated result_manifest.json
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
- `workspace/` 是私有恢复状态。`result_manifest.json`、`transcript.json` 和 `raw_timestamps.json` 是公共 artifact。
- consumer 通过 `audio-transcribe-contract` 读取公共结果；不得 import 此 Skill 的源码或检查 `workspace/`。
- 模型 setup 和 readiness 属于本地职责。model index 格式错误时，应报告模型不可用，不得让异常逃逸到 setup 或依赖检查中。

## 稳定不变量

- `audio_id` 是音频字节的 SHA-256，与其路径无关。
- `variant_id` 标识所有可能改变 transcript 字节或 timestamp 的 resolved behavior。canonical request 同时包含文本规范化 policy 和精确固定的 `alignment_policy`。
- `ALIGNMENT_POLICY` 使用 schema v1、1 ms timestamp resolution、`drop_item_and_owned_text` zero-duration 处理和严格排序。policy 变化会生成新的 `variant_id` 和 ASR plan identity。
- 所有语言都使用 NFKC，仅 `zh` 额外使用 OpenCC `t2s`，因此 normalization policy 变化也会生成新的 `variant_id`。
- 完整 manifest 最后发布，并且是唯一的成功标记。
- 公共 artifact 路径必须位于结果目录内；indexed model shard 路径必须位于模型目录内。
- 仅当重建的公共 artifact 能复现 manifest 记录的 digest 时，恢复流程才逐字节还原原始完整 manifest。恢复失败时，保留原始完整 manifest，以便稍后重试。

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
results/<audio_id>/<provider>-<language>-<variant_id>/
├─ result_manifest.json
├─ transcript.json
├─ raw_timestamps.json
├─ transcribe.log
└─ workspace/
   ├─ asr_plan.json
   ├─ vad_result.json
   ├─ chunk_results/
   └─ result.json
```

manifest 记录 audio identity、resolved request identity、受限于目录内的 artifact 路径，以及公共 artifact digest。成功的 consumer 从 `load_result` 获取经过验证的 manifest、transcript 和 timestamp snapshot；不返回部分结果。

`workspace/result.json` 是 pipeline 唯一的合并结果，也是发布所使用的唯一私有 recovery snapshot。它仅包含 `audio_id`、`variant_id`、`text`、`items`、`duration`、`provider` 和 `language`，并在读取、发布和恢复时与当前请求逐项验证。

plan 和各 chunk 的 Provider 结果仍为独立的 workspace cache。当所有 chunk cache 都有效时，pipeline 会重新执行合并、timestamp offset、alignment 验证、文本与item 规范化以及 alignment 复验，然后原子替换`result.json`；此过程不加载 Provider。workspace 不存储 segment。包含 `plan`、`words` 或 `segments` 的旧版合并结果不会被读取或迁移。

私有 `chunk_results/` 保留 Provider 文本。`workspace/result.json` 和两个公共 JSON artifact 仅包含规范化文本。规范化失败或规范化后的 alignment 失败会停止发布；恢复流程从已规范化的 workspace snapshot 确定性重建结果。

pipeline 不写入 `progress.json` 或 `metrics.json`，也不会删除历史遗留的这两个文件。进度由 plan、合法 chunk 文件和本次日志推导；diagnostics 通过进程内 `PipelineOutcome` 返回给 benchmark，不属于公开结果合同。

## 公共 contract 与发布

`audio-transcribe-contract` 0.1.2 被有意设计为独立于内部 alignment 模块。其 `load_result()` API 保持不变，但它包含一份固定 alignment policy 的独立副本，并会在接受 identity 前拒绝缺失或遭修改的 policy。Raw item 必须具有精确的 item shape、非空文本、有效的 Provider probability，以及满足 `0 <= start < end <= duration` 和 `start >= previous_end` 的 timing。manifest 与 artifact 之间的 identity、canonical request digest、artifact digest、Provider、language、duration 和路径包含关系必须一致。

发布采用 manifest-last。流程先写入 `transcript.json` 和 `raw_timestamps.json`，再写入 `.result_manifest.json.incomplete`，并使用 `load_result()` 验证该 candidate。仅当验证成功时，才允许通过 `os.replace()` 原子创建 `result_manifest.json`；candidate 验证失败时删除 incomplete 文件，并且不创建正式成功标记。首次发布拒绝覆盖已有正式 manifest。

恢复流程临时使用 `.result_manifest.json.recovery`，与发布 candidate 区分。它从经过严格验证的规范化 workspace 重建公共 artifact，要求其 digest 与记录的完整结果匹配，并恢复原始正式 manifest 字节。此命名隔离不改变现有的多文件恢复协议。
