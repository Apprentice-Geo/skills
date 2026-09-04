# 已知问题与审查记录

## 状态

- 状态：持续维护
- 首次记录日期：2026-09-04
- 审查基线：私有数据流与 Benchmark 重构实施前的当前工作树
- 证据范围：静态检查当前实现、测试和引用文档；未通过本文件重新运行真实转写或 benchmark

## 目的

本文集中记录审查中已经发现、但不一定属于 [`DATAFLOW-BENCHMARK-REFACTOR-PLAN.md`](DATAFLOW-BENCHMARK-REFACTOR-PLAN.md) 范围的问题。这样可以避免为了完成数据流简化而顺便改变实验方法、公共合同或模型安装策略，也避免这些问题只存在于对话上下文中。

本文使用以下证据标签：

- **[KNOWN]**：当前代码或仓库文档可以直接证明。
- **[INFERRED]**：影响由已知行为推导，但严重程度取决于运行环境或使用方式。
- **[UNKNOWN]**：仓库本身无法证明，需要维护者或真实运行补充证据。

优先级不是实施顺序承诺：P1 表示可能影响结果身份、恢复正确性或 benchmark 解读；P2 表示测量、可移植性或合同清晰度问题。

## 问题总览

| ID | 问题 | 优先级 | 当前处理 |
| --- | --- | --- | --- |
| KI-001 | 运行时声明的模型 revision 未与本地安装状态核对 | P1 | 延后，需独立正确性改动 |
| KI-002 | 损坏的 workspace result 无法从正常 CLI 进入 chunk 重建 | P1 | 部分纳入；已发布结果场景待决策 |
| KI-003 | Reference 人工校验状态和 provenance 文档相互冲突 | P1 | 已关闭（2026-09-04） |
| KI-004 | benchmark 续跑身份不绑定代码、环境和实际模型 | P2 | 重构计划接受此风险，但必须明确限制 |
| KI-005 | warmup 不会预热正式 run 使用的模型进程 | P2 | 未纳入，benchmark 方法问题 |
| KI-006 | GPU 显存指标统计整机所有 compute process | P2 | 未纳入，资源测量问题 |
| KI-007 | 两种 mode 的比较不能单独证明 chunk optimizer 的收益 | P2 | 未纳入，实验设计问题 |
| KI-008 | 公共 loader 的可用性依赖私有 workspace 和日志存在 | P2 | 待确认公共结果的可移植边界 |
| KI-009 | 文档所称“精确字段 shape”严于 contract 实际验证 | P2 | 待决定修正文档还是收紧 contract |

## KI-001：模型 revision 未在运行时验证

**结论：[KNOWN]** Provider identity 记录的是代码中配置的 revision，不一定是本次推理实际加载的模型 revision。

证据：

- `scripts/model_identity.py` 中的 `MODEL_REVISIONS` 是 setup 和 `variant_id` 使用的声明值。
- `scripts/setup/download_models.py` 在安装和复用下载目录时校验 `.model_identity.json`。
- 生产 readiness 和 Provider `prepare()` 只通过 `model_has_weights()` 检查权重文件是否存在及分片是否完整，不读取 `.model_identity.json`。
- `WhisperProvider.request_identity()` 和 `Qwen3AsrProvider.request_identity()` 直接把 `MODEL_REVISIONS` 写入请求身份。

如果模型目录被手动替换、复制自旧安装，或 identity marker 缺失/错误但权重形状仍合法，运行时仍可能加载这些文件，同时让 `variant_id`、manifest 和 benchmark 报告声称使用了当前固定 revision。[INFERRED] 这可能造成错误 cache 复用和无法审计的结果混合。

建议将它作为独立正确性修复：在 Provider 进入请求身份和模型加载前验证 marker 的 repo/revision。需要明确的是，marker 校验只能证明目录由预期安装流程标记，不能证明安装后每个模型字节未被修改；若要求字节级证明，需要另行权衡大模型摘要成本。

关闭条件：自动选择和显式选择 Provider 都会在形成可复用结果前拒绝 marker 缺失或 revision 不匹配，并有相应测试。

## KI-002：损坏 workspace result 阻断 chunk 恢复

**结论：[KNOWN]** 在没有完整 manifest 的路径中，`run_transcribe()` 只检查 `workspace/result.json` 是否存在。文件存在时跳过 pipeline，随后由 `publish_result()` 严格读取；如果文件损坏，调用直接失败，不会利用已有 plan 和 chunks 重建它。

类似地，已有 manifest 的公共 artifact 恢复只尝试使用 workspace snapshot；workspace 也损坏时不会转入 chunk cache 重建。后一个场景还涉及已发布 manifest 的不可变性和 digest 恢复规则，不能仅靠删除文件解决。

影响是恢复能力低于已落盘信息理论上能够提供的能力，用户可能在合法 chunks 已齐全时仍需重新处理或人工移开损坏状态。

数据流重构已经要求正常 CLI 验证 workspace result 而不是只检查存在性，并在无完整 manifest 的情况下从合法 chunks 重建，因此只覆盖本问题的第一个场景。对于“已有完整 manifest，但公共 artifact 与 workspace 同时损坏”的情况，实施前仍需确认是维持当前失败边界，还是允许 pipeline 重建后仅在公共 digest 完全一致时恢复原 manifest；完成第一个场景后不能直接关闭整个 KI-002。

关闭条件：首先覆盖“无 manifest、workspace result 损坏、plan/chunks 合法”的 CLI 回归测试。对于已有 manifest 的第二个场景必须完成以下二选一决策后才能关闭整个问题：若保持当前失败边界，在架构和错误处理文档中把它声明为有意停止条件，并用测试固定原 manifest 与现有公共 artifact 不被改写；若扩展恢复边界，则覆盖 pipeline 重建后 digest 一致和不一致两种结果。

## KI-003：Reference 校验状态与 provenance 不一致

**状态：已关闭（2026-09-04）。** 维护者确认已完成本计划要求的人工听校，原问题是实施记录未同步。已更新 reference 计划中的完成状态和 provenance 边界，并移除 `summarize()` 生成的固定 method/assistance 限制说明。`method` 和 `assistance` 字段是维护者在人工听校后有意手动移除，不再要求写入 manifest 或每份报告；制作过程继续由 `benchmark/README.md` 记录。

## KI-004：benchmark 续跑不绑定运行环境

**结论：[KNOWN]** 当前 `run_id()` 只包含 Provider、语言、分钟数、mode 和 repetition。报告创建时记录一次 `environment()`，但恢复时 `_validate_report()` 只要求它是对象，不与当前 commit、依赖、硬件或模型状态比较。

每个成功 run 虽然记录 `execution_identity` 和 `provider_identity`，跳过逻辑仍只依据 `run_id`。因此同一路径可以在代码、依赖、配置常量、机器或模型目录变化后继续写入，并把新旧结果汇总到同一报告；报告顶层 environment 仍只代表首次创建时的快照。[INFERRED] 对短期中断后的同环境续跑通常没有影响，但跨部署或跨重构续跑会削弱比较可信度。

数据流重构按用户要求不提供旧实现恢复、迁移或重算保证，并有意不重新建立复杂的环境版本合同。因此本项是接受的操作风险，而不是本轮必须修复的 bug。新 benchmark 文档和 CLI 应明确要求：代码、依赖、模型或机器变化后使用新报告路径；顶层 environment 只表示创建快照，不能证明所有 run 的环境。

如果未来需要机器可验证的实验一致性，优先为报告冻结一个小而明确的 experiment fingerprint，而不是恢复通用 schema 迁移系统。

关闭条件：若继续接受风险，则文档和报告声明足够明确；若不再接受，则恢复前比较所定义的 fingerprint，并拒绝混合报告。

## KI-005：warmup 与正式 run 不共享模型进程

**结论：[KNOWN]** 每次 `run_worker()` 都启动新的 `python -m scripts.benchmark --worker` 子进程。warmup 完成后该进程退出，每个正式 run 又在新进程中重新导入依赖、初始化 runtime 并加载模型。

因此 warmup 可能预热操作系统文件缓存、驱动或设备级缓存，但不会保留 Python 对象、已加载模型、CUDA allocator 或当前 Provider 进程内状态。把它理解为“正式 run 的模型预热”是不准确的。[INFERRED] 它可能减少一部分冷存储影响，但具体效果尚未单独测量。

可选处理：

- 保持隔离进程设计，把 `warmup` 更准确地命名或描述为 system/cache priming；
- 让一个持久 worker 在同一进程内完成 warmup 和正式重复；
- 若目标就是衡量完整冷启动，删除 warmup 并明确报告 cold-process cost。

关闭条件：benchmark 文档对测量语义无歧义，且实现与选择的 cold/warm 模型一致。

## KI-006：GPU 显存不是 worker 归因指标

**结论：[KNOWN]** `sample_gpu_mb()` 调用 `nvidia-smi --query-compute-apps=used_memory`，解析所有返回行并求和；它不按 worker PID、进程树或 GPU 设备过滤。

因此 `peak_gpu_memory_mb` 表示采样时整机可见 compute process 的显存总量，而不是当前 benchmark worker 的独占或增量显存。存在其他 CUDA 进程时会污染结果；多 GPU 环境也无法从该单值看出资源归属。

建议在保持该实现时更名为 system-wide sampled GPU memory，并记录测量限制。若要归因给 run，需要查询 PID 与设备并过滤 worker 进程树，或使用能够按进程和设备采样的 API；仍要处理驱动不可见子进程和共享显存等边界。

关闭条件：指标名称和文档准确表达全局口径，或实现提供可测试的 worker/device 归因。

## KI-007：mode 比较不能隔离 optimizer 收益

**结论：[KNOWN]** `project-slicing` 测量生产 `run_transcribe()`，包含 VAD、规划、执行 policy、Provider、合并、规范化和 artifact 发布；`provider-native` 则把完整音频交给 Provider，不执行项目 VAD、规划、合并或发布。faster-whisper native 路径仍启用其内部 VAD，Qwen3-ASR 也可能执行内部切分。

因此两种 mode 的 wall time、RTF 或文本差异只能衡量两套端到端执行策略，不能把差异单独归因于 chunk optimizer。现有历史报告将它称为“切片路径”比较基本成立，但任何“optimizer 带来 N 倍收益”的结论都超出实验设计支持范围。

若要回答 optimizer 的独立贡献，需要增加控制变量更严格的消融实验，例如固定相同 Provider 输入、相同已生成 layouts 和相同发布边界，只替换 optimizer 决策。该实验不应混入数据流重构。

关闭条件：文档始终把现有结果限定为端到端策略比较；若需要 optimizer 结论，新增独立 benchmark 设计和报告。

## KI-008：公共 loader 依赖私有目录和日志存在

**结论：[KNOWN]** 架构文档把 `workspace/` 定义为私有恢复状态，并禁止 consumer 读取它；但 manifest 把 workspace 和日志列为 artifacts，`audio_transcribe_contract.load_result()` 会要求日志是文件、workspace 是目录，缺少任一项都会拒绝结果。

contract 不读取 workspace 内容，也不验证日志或 workspace digest。这意味着 consumer API 没有直接暴露私有数据，但一个可加载的结果目录仍必须携带这些非公共文件。将 manifest、`transcript.json` 和 `raw_timestamps.json` 单独复制到其他位置并不能形成可用结果。

这是公共结果可移植边界上的设计张力，不必然是 bug：如果公开入口只承诺原地引用完整 result directory，当前行为可以保留；如果希望公共三文件成为可独立归档和传输的合同，workspace 和日志就不应是 loader 的可用性前提。

数据流重构明确保持公共 v1 行为不变，因此本项需要另行决策，不能借内部 schema 清理顺带改变。

关闭条件：架构明确选择“原地完整目录”或“可独立公共 bundle”，并使 manifest、contract loader、恢复策略和测试与该选择一致。

## KI-009：“精确字段 shape”描述与实现不符

**结论：[KNOWN]** `references/ERROR-HANDLING.md` 称 transcript 和 raw timestamp 具有“精确字段 shape”，但 contract 的严格程度并不一致：

- raw timestamp 的每个 item 使用键集合相等检查，确实拒绝未知字段；
- transcript segment 只检查必需字段和值，不拒绝额外字段；
- transcript 和 raw timestamp 顶层只读取必需字段，不拒绝额外字段；
- manifest 及其 `audio`、`request`、`artifacts` 等对象也没有统一执行精确键集合检查。

因此当前实际合同更接近“严格验证已知必需字段，并在部分节点拒绝未知字段”，而不是全结构 exact shape。它可能是允许 v1 添加字段的有意兼容策略，也可能只是验证遗漏；现有文档和测试没有把选择说清楚。

建议先决定公共 v1 是否允许 additive fields。若允许，应修正文档并明确只有哪些嵌套对象是 exact；若不允许，应收紧 validator 并增加顶层、segment 和嵌套对象的未知字段回归测试。收紧行为会让目前可加载的结果失效，属于公共合同变更，不能归入内部 schema 简化。

关闭条件：文档、类型、validator 和未知字段测试对同一兼容策略达成一致。

## 与数据流重构的关系

本轮重构直接处理 KI-002 的无 manifest 场景，并在 benchmark 报告设计中显式接受 KI-004 的兼容代价。KI-003 已作为文档与报告说明同步问题关闭。KI-002 的已发布结果场景，以及 KI-001、KI-005、KI-006、KI-007、KI-008 和 KI-009，不应随内部落盘精简顺手修改：它们分别涉及公共恢复、模型身份、实验方法、资源归因或公共合同，需要独立决策和聚焦验证。

实施数据流重构时应保证这些问题不会被无意掩盖。例如，删除 benchmark schema 不等于 reference provenance 可以删除；删除 `metrics.json` 不等于现有 GPU 指标已经变成进程级指标；保持公共 schema v1 也不等于当前 exact-shape 描述已经正确。
