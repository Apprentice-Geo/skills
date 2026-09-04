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
| KI-004 | benchmark 续跑身份不绑定代码、环境和实际模型 | P2 | 已关闭（2026-09-05）：自动校验硬件身份 |
| KI-005 | warmup 不会预热正式 run 使用的模型进程 | P2 | 已关闭（2026-09-05）：持久 worker 复用 prepared model |
| KI-006 | GPU 显存指标统计整机所有 compute process | P2 | 已关闭（2026-09-05）：取消资源占用指标 |
| KI-007 | 两种 mode 的比较不能单独证明 chunk optimizer 的收益 | P2 | 已关闭（2026-09-05）：限定为端到端策略比较 |
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

**结论：[PARTIALLY RESOLVED 2026-09-04]** 没有完整 manifest 时，`run_transcribe()` 现在会先严格验证 `workspace/result.json`；文件损坏或身份不匹配时进入 pipeline，并可利用合法 plan 和 chunks 重建后再发布。

类似地，已有 manifest 的公共 artifact 恢复只尝试使用 workspace snapshot；workspace 也损坏时不会转入 chunk cache 重建。后一个场景还涉及已发布 manifest 的不可变性和 digest 恢复规则，不能仅靠删除文件解决。

剩余问题仅是“已有完整 manifest，但公共 artifact 与 workspace 同时损坏”的恢复边界。当前仍只尝试 workspace snapshot 恢复，不会转入 chunk pipeline；这涉及已发布 manifest 的不可变性和 digest 规则，需另行决策。

关闭条件：对于剩余场景完成以下二选一决策：若保持当前失败边界，在架构和错误处理文档中把它声明为有意停止条件，并用测试固定原 manifest 与现有公共 artifact 不被改写；若扩展恢复边界，则覆盖 pipeline 重建后 digest 一致和不一致两种结果。

## KI-003：Reference 校验状态与 provenance 不一致

**状态：已关闭（2026-09-04）。** 维护者确认已完成本计划要求的人工听校，原问题是实施记录未同步。已更新 reference 计划中的完成状态和 provenance 边界，并移除 `summarize()` 生成的固定 method/assistance 限制说明。`method` 和 `assistance` 字段是维护者在人工听校后有意手动移除，不再要求写入 manifest 或每份报告；制作过程继续由 `benchmark/README.md` 记录。

## KI-004：benchmark 续跑不绑定运行环境

**状态：已关闭（2026-09-05）。** Benchmark 报告现在把环境拆分为硬件身份和审计信息。硬件身份包含 CPU 型号、逻辑核心数、物理内存总字节数，以及按序号排序的 GPU 型号和总显存；续跑在启动 worker 前严格比较这些字段，缺失或不同均要求新报告路径，因此不支持跨设备续跑。

系统、Python、commit、依赖和模型 revision 继续作为审计快照展示，不参与自动拒绝；这些内容变化后由操作者主动使用新路径。这个关闭边界只保证同一测试设备，不声称验证本地模型字节或完整实验环境。

## KI-005：warmup 与正式 run 不共享模型进程

**状态：已关闭（2026-09-05）。** 每个仍有待执行项的 Provider 使用一个持久 worker session。每种实际模型加载配置首次出现时，以对应正式 run 的相同音频和 mode 预热；正式 run 通过 `run_transcribe()`、pipeline 和 execution policy 的可选 prepared model 通道复用同一 Python 对象。faster-whisper 的 key 包含模型、device、compute type、CPU threads 和 worker 数；其中 `project-slicing` 使用生产配置，`provider-native` 使用单 worker 和生产 policy 算出的全部 CPU 线程预算，因此二者配置不同时会分别预热。Qwen3-ASR 的 key 包含模型、aligner、device、dtype 和 batch size，language 不进入 key。

Warmup 和正式 run 记录 session ID，报告校验成功 run 必须存在同 session、同配置的成功 warmup。续跑创建新 session 并重新预热；worker 退出后下一项也创建新 session。普通转写 CLI 未提供 prepared model 时仍执行原有 `prepare()` 行为。

## KI-006：GPU 显存不是 worker 归因指标

**状态：已关闭（2026-09-05）。** Benchmark 已取消进程树 RSS 和 compute-process 显存采样，新结果不再包含 `peak_rss_bytes`、`peak_gpu_memory_mb` 或 `gpu_metric_unavailable_reason`。仅为该采样直接声明的 `psutil` 依赖和相关采样、轮询死代码已移除。

报告中的物理内存与 GPU 总显存只属于测试设备身份，表示容量而非 run 占用；当前 benchmark 不测量或比较内存、显存占用。

## KI-007：mode 比较不能隔离 optimizer 收益

**状态：已关闭（2026-09-05）。** README 和生成的 Markdown 报告现在始终将 `project-slicing` 与 `provider-native` 定义为两套端到端策略比较，并明确 wall time、RTF、相对速度和文本差异不能单独归因于 chunk optimizer。本轮不新增 optimizer 消融实验；若未来需要该结论，应另行设计控制变量实验。

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

数据流重构直接处理 KI-002 的无 manifest 场景；KI-003 已作为文档与报告说明同步问题关闭。后续 benchmark 方法改动关闭了 KI-004～KI-007。KI-002 的已发布结果场景，以及 KI-001、KI-008 和 KI-009 仍需独立决策。

后续改动仍应避免把内部实现变化误认为公共合同问题已经解决。例如，删除 benchmark schema 不等于 reference provenance 可以删除；保持公共 schema v1 也不等于当前 exact-shape 描述已经正确。
