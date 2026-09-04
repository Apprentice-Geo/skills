# 私有数据流与 Benchmark 重构计划

## 状态

- 状态：计划中
- 记录日期：2026-09-04
- 范围：私有 ASR workspace、pipeline diagnostics、benchmark 报告与代码布局

## 背景

当前实现通过严格的公共 manifest、artifact digest、请求身份和 alignment 验证，为其他 Skill 提供可验证的转写结果。公共合同承担跨 Skill 读取和长期复用责任，其版本和完整性设计有明确用途。

复杂度主要集中在公共边界之前：私有 workspace 同时保存 plan、VAD、progress、逐 chunk 结果、合并结果和 metrics；多个内部文件共享一个 ASR pipeline schema 版本；每个 chunk 又复制完整 plan。部分状态可以由其他文件推导，部分数据只为了在同一进程中的 benchmark 调用而落盘。这些设计扩大了需要理解、验证和维护的状态空间。

Benchmark 方面，`scripts/benchmark.py` 同时负责 CLI、矩阵构建、worker 子进程、资源采样、指标计算、报告验证、断点续跑和 Markdown 生成，文件已经接近一千行。Benchmark 报告和 reference 还包含独立的 schema 版本及旧格式拒绝逻辑，但项目不需要为历史 benchmark 实现提供恢复、迁移或重新评分能力。

本次重构以降低数据流和维护复杂度为首要目标，不以减少磁盘占用或清理既有文件为目标。

本轮审查发现但不全部纳入本次重构的问题，统一记录在 [`KNOWN-ISSUES.md`](KNOWN-ISSUES.md)。

## 重构目标

1. 移除不承担公共兼容责任的内部 `schema_version`，仅在最终公共产物及其他明确的外部合同中保留 schema。
2. 停止生成没有独立恢复或消费价值的落盘结果，减少私有 workspace 的状态数量。
3. 将 benchmark 报告视为当前实现的本地运行状态，不提供旧实现报告的恢复、迁移或重算保证，同时保留当前格式内的断点续跑能力。
4. 将 `scripts/benchmark.py` 迁移到 `benchmark/` 包，并按职责拆分，缩小单文件规模和跨职责耦合。
5. 保持公开 `result_manifest.json`、`transcript.json`、`raw_timestamps.json` 及 `audio-transcribe-contract` v1 行为不变。

## 非目标

- 不删除、迁移或批量改写已经存在的 workspace、benchmark report 或 benchmark 临时目录。
- 不保证旧私有 workspace 能被新实现复用；旧格式无法按当前结构读取时可以自然成为 cache miss。
- 不合并当前两个公共 JSON artifact，不升级公共 result schema。
- 不修改 Provider 参数、VAD 参数、chunk optimizer、alignment、文本规范化或句子分段算法。
- 不修改 benchmark 的 CER/WER、mode difference、实验矩阵、warmup 或资源采样口径；这些问题应单独评估，避免与数据流重构混合。
- 不在本次重构中解决模型 revision marker 的运行时验证问题；该问题需要独立的正确性改动和测试。

## 当前现状

### 私有 workspace

当前一次 pipeline 可能生成：

```text
workspace/
├─ asr_plan.json
├─ vad_result.json
├─ progress.json
├─ chunk_results/
│  ├─ chunk_000.json
│  └─ ...
├─ result.json
└─ metrics.json
```

这些文件存在以下问题：

- `ASR_PIPELINE_SCHEMA_VERSION` 同时控制 plan、VAD、chunk、progress 和 metrics。任何全局版本变化都会让职责不同的数据一起失效。
- `progress.json` 不参与恢复读取；它反复复制 plan，并把 plan 与现有 chunk 文件能够推导的状态再次落盘。
- 每个 chunk payload 包含完整 `plan.to_dict()`，chunk 越多，身份和布局信息重复越多。
- `provider_metadata` 当前没有恢复或发布消费者。
- chunk 中的 `elapsed_seconds` 主要服务落盘 metrics，而 benchmark 随后再从 `metrics.json` 读取。
- `cleanup_report` 只用于在部分恢复时重放历史 cleanup warning，不影响 chunk 的正确性或合并结果。
- `vad_result.json` 与 plan 相互验证：即使 plan 已经合法，pipeline 仍会读取 VAD、重新推导 layouts，并处理多类一致性失败。
- 正常 CLI 只根据 `workspace/result.json` 是否存在决定是否进入 pipeline；文件存在但损坏时，已有 chunk 重建路径无法被正常入口触发。

### Benchmark

当前 `scripts/benchmark.py` 包含以下职责：

- 用户 CLI 与隐藏 worker CLI；
- benchmark matrix 和 run identity；
- worker 子进程生命周期；
- CPU、RSS 和 NVIDIA 显存采样；
- production slicing 与 provider-native 两种执行路径；
- 文本规范化、编辑距离、Reference CER/WER 和 mode difference；
- report schema、结构验证、恢复和 attempt 管理；
- Markdown 汇总。

当前报告使用 schema 3，reference manifest 使用 schema 1。恢复逻辑明确拒绝旧 schema，并会重新验证和重新计算已有成功 run 的 reference comparison。报告允许在冻结样本集合内增加 Provider、mode 或 repetition，使一次报告既是实验结果，又承担可扩展 job 数据库的职责。

## 目标数据流

### Pipeline 主流程

重构后的私有数据流为：

```text
audio and resolved request
  -> load current plan
     -> valid: load matching chunks
     -> invalid: load optional VAD cache or run VAD, then create plan
  -> execute pending chunks
  -> merge accepted chunks
  -> write workspace/result.json
  -> publish public artifacts
```

新实现只生成以下私有恢复文件：

```text
workspace/
├─ asr_plan.json
├─ vad_result.json
├─ chunk_results/
│  ├─ chunk_000.json
│  └─ ...
└─ result.json
```

已经存在的 `progress.json` 和 `metrics.json` 不删除，但新实现不再读取或写入它们。

### Plan

`asr_plan.json` 不再包含 `schema_version`。它继续保存构建和验证 chunk layout 所需的数据，并新增基于 canonical plan payload 计算的 `plan_id`：

```json
{
  "plan_id": "<sha256>",
  "source": {},
  "provider_request": {},
  "execution_policy": {},
  "vad_parameters": {},
  "planning_parameters": {},
  "chunks": []
}
```

`plan_id` 是内容身份，不是格式版本。读取 plan 时按当前代码严格解析字段、类型和 layout 不变量，并重新计算 `plan_id`。解析失败、身份不匹配或 plan 非法时返回 cache miss，不迁移旧数据。

所有私有 JSON loader 使用同一条当前格式规则：对象字段集合必须精确匹配，未知字段和缺失字段都视为非法；不把字符串或布尔值强制转换为数字；拒绝非有限数字。Canonical JSON 使用排序 key、紧凑分隔符、UTF-8 和禁止 NaN 的固定序列化，`plan_id` 的输入不包含 `plan_id` 自身。旧文件中的 `schema_version` 因为属于未知字段，会使该私有文件自然失效。

### VAD cache

`vad_result.json` 保留，但降级为构建 plan 时使用的可选缓存：

```json
{
  "source": {},
  "parameters": {},
  "speech_intervals": []
}
```

VAD cache 遵循以下规则：

- 不包含 `schema_version`。
- 合法 plan 是 chunk 恢复的唯一权威；plan 合法时不读取 VAD，也不从 VAD 重新推导 layouts 验证 plan。
- 仅在 plan 缺失或非法时尝试读取 VAD。
- VAD 的 source、parameters 或 intervals 不符合当前严格验证时，重新计算 VAD 并覆盖该路径。
- 自动语言检测已经计算出 VAD 时，把同一份 intervals 传给 pipeline，不在一次调用中重复计算。
- 指定语言且已有合法 plan 时，不应为了进入 pipeline 而预先执行 VAD。
- plan miss 且需要构建 plan 时，新计算或由自动语言检测传入的 intervals 必须先按当前 source 和 VAD parameters 验证，再原子写入 `vad_result.json`，随后用于构建 plan。plan 构建失败时保留已经验证的 VAD cache，供下次重试使用。

该设计保留 VAD 在 plan 修复和诊断上的价值，同时移除 VAD 与合法 plan 之间的双向一致性状态。

这里需要区分两种行为：自动语言检测可能在 resolved request 和 variant workspace 确定之前就需要 VAD，这是上游语言检测输入；“只在 plan miss 时读取 VAD”特指 pipeline 对 `vad_result.json` 的读取。上游产生的 intervals 只有在 source identity 和 VAD parameters 与 pipeline 当前值完全一致时才能传入并持久化。如果自动语言检测已经计算 VAD，而最终 workspace 中已有合法 plan，pipeline 使用 plan 且不为了补齐 VAD cache 再次写文件。

### Chunk cache

chunk payload 不再保存完整 plan，也不包含内部 schema：

```json
{
  "plan_id": "<sha256>",
  "chunk_index": 0,
  "text": "...",
  "items": []
}
```

约束如下：

- 文件名和 `chunk_index` 必须一致。
- `plan_id` 必须与当前 plan 一致。
- start、end 和 duration 由 plan 中对应 layout 提供，不在 chunk 中重复保存。
- 读取时重新构建 alignment item，并执行当前严格 alignment 验证。
- `provider_metadata`、`elapsed_seconds` 和 `cleanup_report` 不再落盘。
- cleanup warning 只为本次新产生并被接受的 Provider candidate 记录；不再为 cached chunk 重放历史 warning。

### Progress

删除 `rebuild_progress()` 及所有 `progress.json` 写入。进度由以下状态推导：

- plan 中的完整 chunk 列表；
- `chunk_results/` 中通过当前 plan 验证的 chunk；
- 本次日志中的 pending、成功和失败摘要。

失败不会删除已经成功的 chunk；下次运行仍通过 plan 和 chunk 文件完成断点恢复。

### Workspace result

`workspace/result.json` 继续作为合并结果、发布输入和公共 artifact 恢复快照，但移除 `schema_version`。它显式绑定当前结果身份：

```json
{
  "audio_id": "...",
  "variant_id": "...",
  "provider": "...",
  "language": "...",
  "duration": 0.0,
  "text": "...",
  "items": []
}
```

`run_transcribe()` 是 canonical `audio_id` 和 `variant_id` 的唯一所有者。它必须把两个预期值显式传给 pipeline，以及测试注入的 engine 写入路径；pipeline 不从 plan 或 Provider 字段重新计算外层 result identity。`write_workspace_result()` 使用这两个调用方提供的值写入 snapshot，读取和发布时再与当前请求逐项比较。

发布和恢复仍严格验证字段 shape、身份、duration、Provider probability 规则和 alignment。文件存在不再等于文件合法：正常 CLI 应先尝试读取和验证 workspace result，失败时进入 pipeline，由合法 chunks 重建或重新执行缺失 chunk。

旧格式 workspace result 不迁移。已有合法公共 manifest 仍可按公共合同直接返回；只有需要读取旧私有 workspace 的未完成或损坏结果可能重新执行 pipeline。

### Pipeline diagnostics

删除 `metrics.json` 写入。pipeline 通过内部返回对象把 diagnostics 交给调用方，例如：

```python
@dataclass(frozen=True)
class PipelineOutcome:
    final_info: dict[str, Any]
    source: str
    metrics: PipelineMetrics
```

普通转写 CLI 只使用发布后的 manifest path。Benchmark 的 project-slicing worker 直接从本次调用结果读取 metrics，不再通过私有文件进行同进程数据传递。完整 chunk cache 恢复时，不承诺提供历史 provider stage timing；benchmark 使用全新结果目录，因此不依赖历史 diagnostics。

为避免把 diagnostics 重新藏入全局状态，内部 `run_transcribe()` 改为返回包含 `manifest_path` 和可选 `PipelineOutcome` 的 `TranscribeOutcome`。CLI 只打印其中的 manifest path；benchmark worker 读取本次 pipeline outcome。有效公共 manifest cache hit 没有新的 pipeline diagnostics，因此 outcome 中该字段为 `None`。跨 Skill 的公共读取仍只使用 manifest 和 contract，不依赖这个 Python 返回类型。

## Benchmark 重构

### 代码布局

删除 `scripts/benchmark.py`，不保留兼容 shim。新增或调整：

```text
benchmark/
├─ __init__.py
├─ __main__.py
├─ runner.py
├─ worker.py
├─ report.py
├─ metrics.py
├─ reference.py
└─ prepare_audio.py
```

职责划分：

| 模块 | 职责 |
| --- | --- |
| `benchmark.__main__` | 调用公开 benchmark CLI |
| `benchmark.runner` | 参数解析、矩阵构建、当前报告创建和 worker 调度 |
| `benchmark.worker` | 子进程入口、两种执行模式和资源采样 |
| `benchmark.report` | 当前报告读取、原子写入、attempt 状态和 Markdown 汇总 |
| `benchmark.metrics` | 文本规范化、编辑距离、reference comparison 和 mode comparison |
| `benchmark.reference` | 当前 reference manifest、文本片段和音频摘要验证 |
| `benchmark.prepare_audio` | benchmark 音频准备，职责不变 |

正式命令改为：

```powershell
uv run --no-sync python -m benchmark
```

runner 启动 worker 时使用：

```powershell
python -m benchmark.worker ...
```

`pyproject.toml` 的 Pyright include 应加入 `benchmark`，避免代码移出 `scripts/` 后失去现有类型检查覆盖。

### 当前格式报告

Benchmark report 不再包含 `schema_version`，reference manifest 和报告内的 `reference_set` 也不再包含 schema。建议当前报告结构为：

```json
{
  "config": {
    "providers": [],
    "languages": [],
    "minutes": [],
    "modes": [],
    "repetitions": 3
  },
  "comparison_policy": {},
  "reference_set": {
    "manifest_sha256": "...",
    "samples": []
  },
  "environment": {},
  "warmups": [],
  "runs": []
}
```

`comparison_policy`、reference digest、audio digest、Provider identity 和 execution identity 是实验记录，不是格式版本。顶层 `environment` 只记录报告创建时的环境快照；每个 run 继续记录自身 Provider identity 和 execution identity。它们帮助解释结果，但不足以自动识别或精确归因续跑期间的全部环境变化。重构不承诺根据这些字段为旧实现提供迁移或重新评分。

### 断点续跑

一份报告在创建时冻结完整 `config`。当前格式内的续跑规则为：

1. 已存在报告时，读取报告保存的 `config` 作为 job 定义。
2. Provider、language、minutes 和 mode 按模块中声明的固定顺序去重和排列；`repetitions` 必须是正整数。报告只保存这种 canonical config。
3. CLI 未提供矩阵参数时，直接继续报告 config。若显式提供部分矩阵参数，未提供的字段继承报告值；规范化后的完整 config 必须与报告完全一致。`--report` 等路径参数不属于 config。
4. 新报告在未提供某一矩阵参数时使用代码中的当前默认值；已有报告不重新套用可能已经变化的默认值。
5. 扩展或缩小矩阵需要新报告路径。
6. 当前 reference manifest、所选 reference 和音频摘要必须仍与报告记录一致。
7. 报告中的每个正式 run 都必须属于冻结 config：Provider、language、minutes 和 mode 必须是对应集合的成员，repetition 必须位于 `1..config.repetitions`。warmup 的 Provider 和样本也必须属于 config，并继续满足首个样本、`project-slicing`、repetition 0 的约束。config 外条目使报告整体无效，不能仅忽略。
8. 已有 `succeeded` run 按 `run_id` 跳过。
9. 已有 `failed` run 保留，并追加连续的 attempt。
10. 新完成的配对 run 可以补全本次 job 所需的 mode comparison。
11. 每次 warmup 和正式 run 后继续原子写入 JSON，以保留中断恢复点。

恢复时只验证完成上述流程所需的当前 shape、config、run identity、attempt 顺序和输入摘要。不重新计算已有成功 run 的 Reference CER/WER，不重新解释旧 comparison policy，也不尝试识别或迁移历史 schema。

报告不持久化 `running` 状态。worker 退出并产生完整结果后，runner 才把一次成功或失败 attempt 连同本次计算出的 comparison 一起原子写入报告：

- worker 运行中被中断，或 runner 在报告提交前退出：该 attempt 未发生持久化；恢复时使用同一个下一 attempt 编号和新的 worker 目录重新执行。
- attempt 已写入报告：它被视为完整提交，不会重复执行成功项；失败项的下一次运行使用连续编号。
- 一对 mode 的第二个成功结果及 `output_comparison` 在同一次报告写入中提交，不存在受支持的“成功 run 已提交但应有 comparison 尚未提交”状态；遇到这种 shape 时要求新报告路径，不进行补算。
- warmup 继续使用当前 Provider、首个样本、`project-slicing` 和 repetition 0 作为身份。每次失败和最终成功结果都追加到 `warmups`，保留历史；只要同一 Provider 存在任一成功 warmup，后续续跑就跳过该 Provider 的 warmup。warmup 不使用正式 run 的 attempt 序列。

新 runner 有意不根据 environment、commit、dependency、model revision、Provider identity 或 execution identity 自动拒绝续跑。恢复时输出一条固定提示，说明续跑假设代码、依赖、模型和机器未变化。顶层 environment 不是逐 run 快照，不能把混合结果精确归因到某次环境变化。若维护者在这些条件变化后继续同一报告，报告可能混合不可直接比较的结果，这是取消跨实现恢复保证后明确接受的操作者责任，而不是 runner 提供的一致性保证。

如果报告不符合当前结构，runner 直接要求使用新报告路径。错误不区分“旧 schema”“未来 schema”或“手工损坏”，因为这些情况都不属于兼容范围。

### Reference

`benchmark/references/manifest.json` 删除顶层 `schema_version`，继续保留：

- 固定来源信息；
- 增量文本片段顺序；
- 文本文件 SHA-256；
- 样本音频 SHA-256。

Reference loader 只实现当前 manifest shape。字段、路径、digest、UTF-8/LF、音频格式和累积文本验证继续保留，因为它们验证的是当前实验输入，而不是旧格式兼容性。

## 重构范围

### 预计修改的实现

- `scripts/asr/pipeline_types.py`
  - 移除全局私有 schema 和 plan 的 schema 字段；
  - 增加稳定的 plan canonical payload 与 `plan_id`；
  - 缩小持久化 chunk 数据。
- `scripts/asr/workspace.py`
  - 删除 progress 路径和构建逻辑；
  - 简化 VAD cache；
  - 以 `plan_id` 加载 chunk；
  - 移除内部 schema 分支。
- `scripts/asr/pipeline.py`
  - 调整 plan、VAD 和 chunk 恢复顺序；
  - 停止写 progress 和 metrics；
  - 接收 `run_transcribe()` 传入的预期 audio/variant identity，不自行重算；
  - 返回内存 diagnostics。
- `scripts/artifacts.py`
  - 更新无 schema、带结果身份的 workspace snapshot 读写；
  - 保持公共 artifact schema 和发布顺序不变。
- `scripts/transcribe.py`
  - 避免一次调用重复执行 VAD；
  - 验证而非仅检查 workspace result 是否存在；
  - 把 canonical audio/variant identity 传入 pipeline 和 engine workspace 写入路径；
  - 把 pipeline diagnostics 传给 benchmark 调用路径。
- `scripts/benchmark.py`
  - 删除。
- `benchmark/__init__.py`、`benchmark/__main__.py`
  - 建立新的 benchmark 包入口。
- `benchmark/runner.py`、`benchmark/worker.py`、`benchmark/report.py`、`benchmark/metrics.py`
  - 接收原单文件职责。
- `benchmark/reference.py`
  - 删除 reference schema 和旧格式分支，保留当前输入验证。
- `pyproject.toml`
  - 将 `benchmark` 加入 Pyright 检查范围。

### 预计修改的测试

- `tests/test_asr_workspace.py`
- `tests/test_asr_pipeline.py`
- `tests/test_asr_pipeline_runtime.py`
- `tests/test_public_artifacts.py`
- `tests/test_benchmark.py`
- `tests/test_benchmark_reference.py`
- `tests/test_cli_output.py`

必要时按新模块边界拆分 benchmark 测试，但不以机械追求一一对应为目标。测试应覆盖可观察恢复行为和当前合同，不固定无意义的私有调用顺序。

### 预计修改的文档

- `README.md`
- `SKILL.md`
- `references/ARCHITECTURE.md`
- `references/ERROR-HANDLING.md`
- `benchmark/README.md`
- `AGENTS.md`

重点更新命令、workspace 文件列表、恢复语义、benchmark 报告边界和测试命令。

## 实施步骤

### 阶段 1：建立新的私有身份和读取边界

1. 为 plan 定义不含 `plan_id` 的 canonical payload。
2. 计算和验证 `plan_id`。
3. 定义缩小后的 chunk payload 和严格 loader。
4. 定义无 schema、带 audio/variant identity 的 workspace result，并由 `run_transcribe()` 显式向 pipeline 和 engine 路径传递该身份。
5. 增加当前格式合法、错误类型、错误身份和损坏输入测试。

### 阶段 2：简化 pipeline 落盘状态

1. 删除 progress 写入和相关代码。
2. 把 VAD 改为只在 plan miss 时读取的可选 cache。
3. 删除合法 plan 与 VAD 之间的 layouts 重算检查。
4. 删除 chunk 中不参与恢复的数据。
5. 用内存 outcome 替代 `metrics.json`。
6. 修正正常 CLI 对损坏 workspace result 的 chunk 重建路径。

### 阶段 3：迁移 Benchmark 包

1. 先机械迁移现有职责，保持行为测试通过。
2. 把 metrics、worker、report 和 runner 按边界拆分。
3. 添加 `python -m benchmark` 入口。
4. 更新 worker 子进程模块路径和测试 import。
5. 删除 `scripts/benchmark.py`。

### 阶段 4：简化 Benchmark 状态合同

1. 删除 report 和 reference schema 字段及常量。
2. 把完整 matrix config 冻结在新报告中。
3. 将续跑限制为相同 config。
4. 删除旧 schema 特判、迁移和已有成功结果重算。
5. 保留成功跳过、失败 attempt 追加、reference/audio identity 校验和原子写入。

### 阶段 5：文档和验证

1. 同步更新所有直接相关文档。
2. 检查旧命令、旧模块名、旧 workspace 文件和 schema 3 恢复描述。
3. 运行聚焦测试和静态检查。
4. 聚焦测试通过后运行完整测试。

## 测试与验证计划

实施开始后按顺序验证：

```powershell
uv run pytest tests/test_asr_workspace.py tests/test_asr_pipeline.py tests/test_asr_pipeline_runtime.py
uv run pytest tests/test_public_artifacts.py tests/test_cli_output.py
uv run pytest tests/test_benchmark.py tests/test_benchmark_reference.py
uv run ruff check
uv run pyright
uv run ruff format --check .
uv run pytest
git diff --check
```

建议增加或调整以下回归场景：

- 新 workspace 不生成 progress 和 metrics。
- 合法 plan hit 不读取 VAD。
- plan miss 可以复用匹配 VAD。
- VAD 损坏只触发 VAD 重算，不影响合法 plan/chunks。
- chunk 只通过匹配的 `plan_id` 复用。
- 旧 chunk 或 plan 自然失效，不执行迁移。
- workspace result 存在但损坏时，CLI 可以从合法 chunks 重建。
- pipeline 或 engine 写入的 workspace result 使用 `run_transcribe()` 提供的 audio/variant identity，错误身份无法发布。
- 公共 manifest 和 artifact schema、digest、identity 与恢复行为不回归。
- benchmark 新入口和 worker 入口可用。
- 当前格式报告中断后跳过成功 run，并为失败 run 追加 attempt。
- worker 完成但报告提交前中断时，不消耗持久化 attempt 编号。
- 两个 mode 均成功但缺少应有 comparison 的报告被视为非法，不执行补算。
- 恢复时显式提供的部分 CLI 矩阵参数继承报告其余 config，并在规范化后精确比较。
- 报告中存在 config 之外的 run/warmup，或 repetition 超出冻结上限时，恢复在启动 worker 前失败。
- 自动语言检测产生 VAD 但最终命中合法 plan 时，不为了补齐 cache 写入 VAD。
- 有效公共 manifest cache hit 返回 manifest 且不伪造 pipeline diagnostics。
- 报告 config 不一致时要求新报告路径。
- 旧 schema 3 报告不进入专门迁移或重新评分路径。
- reference manifest 无 schema 时仍严格验证字段、路径、摘要和音频身份。

真实转写验证仍依赖本地模型和硬件。除非实施阶段另有明确要求，先以单元测试和模拟边界完成验证；真实性能验证应避免与占用相同模型、CPU 或 GPU 的任务并发。

## 预期结果

重构完成后：

- 公共结果结构和 consumer API 保持不变。
- 私有 workspace 不再使用共享的 pipeline schema 版本。
- 新运行不再生成 `progress.json` 和 `metrics.json`。
- `vad_result.json` 保留，但只在 plan miss 时作为可选 cache 使用。
- plan 是 chunk 恢复的唯一权威，chunk 通过短 `plan_id` 绑定 plan，不再复制完整 plan。
- workspace result 通过 audio 和 variant identity 防止跨结果误用，并可由正常 CLI 从 chunks 重建。
- pipeline diagnostics 在内存中传递，不再借助文件连接生产代码和 benchmark。
- benchmark 代码位于 `benchmark/` 包，CLI、worker、report 和 metrics 职责分离。
- benchmark report 和 reference 不再维护独立 schema 版本或旧格式兼容分支。
- benchmark 仍能在同一当前格式、相同 config 和相同输入身份下断点续跑。
- 已经存在的私有文件、旧报告和临时目录不会被主动删除；是否可复用由当前 loader 自然决定。

## 代价与风险

- 重构后的代码不会恢复旧私有 cache；未完成的旧转写可能重新运行部分或全部 ASR。
- 取消跨实现 benchmark 恢复保证后，维护者必须确保一次报告的续跑期间没有切换代码、依赖、模型或机器。新实现会记录这些信息用于审计，但不承担自动兼容判断。
- 删除 cached chunk cleanup warning 重放会缩小新 attempt 日志的历史信息；原 chunk 内容和转写正确性不受影响。
- diagnostics 从文件改为内存返回会调整内部函数签名，需要同步修改 benchmark worker 和相关测试。
- benchmark 模块迁移会破坏 `scripts.benchmark` import 和旧命令；这是本计划接受的有意不兼容变更。

## 验收标准

- `result_manifest.json`、`transcript.json`、`raw_timestamps.json` 和 contract v1 测试保持通过。
- 私有 plan、VAD、chunk 和 workspace result 不包含 `schema_version`。
- Benchmark report、reference manifest 和 `reference_set` 不包含 `schema_version`。
- 新运行不会创建或更新 `progress.json`、`metrics.json`。
- 已经存在的上述文件不会被清理逻辑删除。
- chunk payload 不包含完整 plan、Provider metadata、elapsed timing 或 cleanup report。
- 合法 plan 可以独立于 VAD cache 恢复 chunks。
- plan 无效时可以复用合法 VAD，也可以在 VAD 无效时重新计算。
- 损坏的 workspace result 不会阻断 chunk cache 重建。
- benchmark 主入口为 `uv run --no-sync python -m benchmark`。
- `scripts/benchmark.py` 被删除，且不提供兼容 shim。
- 当前格式 benchmark report 可以在相同 config 下断点续跑。
- 当前格式报告拒绝任何不属于冻结 config 的 run 或 warmup，以及超出冻结 repetition 上限的正式 run。
- 不存在旧 benchmark schema 的迁移、重算或专门兼容分支。
- README、SKILL、architecture、error handling、benchmark 文档和开发命令与实现一致。
