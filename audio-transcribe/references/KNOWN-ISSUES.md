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
| KI-002 | 损坏的 workspace result 无法从正常 CLI 进入 chunk 重建 | P1 | 已关闭（2026-09-05）：统一恢复与重新发布 |
| KI-003 | Reference 人工校验状态和 provenance 文档相互冲突 | P1 | 已关闭（2026-09-04） |
| KI-004 | benchmark 续跑身份不绑定代码、环境和实际模型 | P2 | 已关闭（2026-09-05）：自动校验硬件身份 |
| KI-005 | warmup 不会预热正式 run 使用的模型进程 | P2 | 已关闭（2026-09-05）：持久 worker 复用 prepared model |
| KI-006 | GPU 显存指标统计整机所有 compute process | P2 | 已关闭（2026-09-05）：取消资源占用指标 |
| KI-007 | 两种 mode 的比较不能单独证明 chunk optimizer 的收益 | P2 | 已关闭（2026-09-05）：限定为端到端策略比较 |
| KI-008 | 公共 loader 的可用性依赖私有 workspace 和日志存在 | P2 | 已关闭（2026-09-05）：v2 两文件公共 bundle |
| KI-009 | 文档所称“精确字段 shape”严于 contract 实际验证 | P2 | 待决定修正文档还是收紧 contract |

## KI-001：模型 revision 未在运行时验证

**结论：[KNOWN]** Provider identity 记录的是代码中配置的 revision，不一定是本次推理实际加载的模型 revision。

证据：

- `scripts/model_identity.py` 中的 `MODEL_REVISIONS` 是 setup 和 `config_digest` 使用的声明值。
- `scripts/setup/download_models.py` 在安装和复用下载目录时校验 `.model_identity.json`。
- 生产 readiness 和 Provider `prepare()` 只通过 `model_has_weights()` 检查权重文件是否存在及分片是否完整，不读取 `.model_identity.json`。
- `WhisperProvider.request_identity()` 和 `Qwen3AsrProvider.request_identity()` 直接把 `MODEL_REVISIONS` 写入请求身份。

如果模型目录被手动替换、复制自旧安装，或 identity marker 缺失/错误但权重形状仍合法，运行时仍可能加载这些文件，同时让 `config_digest`、manifest 和 benchmark 报告声称使用了当前固定 revision。[INFERRED] 这可能造成错误 cache 复用和无法审计的结果混合。

建议将它作为独立正确性修复：在 Provider 进入请求身份和模型加载前验证 marker 的 repo/revision。需要明确的是，marker 校验只能证明目录由预期安装流程标记，不能证明安装后每个模型字节未被修改；若要求字节级证明，需要另行权衡大模型摘要成本。

关闭条件：自动选择和显式选择 Provider 都会在形成可复用结果前拒绝 marker 缺失或 revision 不匹配，并有相应测试。

## KI-002：损坏 workspace result 阻断 chunk 恢复

**状态：已关闭（2026-09-05）。** 生产端现在从当前输入音频和 resolved request 计算 `audio_id + config_digest`，独立定位 workspace。无论 manifest 是否存在，公共结果无效且 workspace snapshot 不可用时均可进入 pipeline，从合法 plan/chunks 重建；缓存不足再执行正常推理。

完整有效且身份匹配的 bundle 直接复用；损坏 bundle 重建 digest 相同时保留原 manifest 字节，不同时允许在同身份下重新发布，仅更新正文 digest 并保留原路径及其他元数据。manifest 缺失或损坏时根据当前请求生成结果，不承诺复现历史 digest。完整 candidate 验证后才安装正文与 manifest，最终 manifest 安装失败时尝试回滚正文。

`tests/test_public_artifacts.py` 覆盖已发布结果与 workspace 同时损坏、无需模型的 chunk 重建、digest 一致/不一致、manifest 缺失或损坏后的重新发布，以及发布失败保留原公共文件。长期协议见 [架构](ARCHITECTURE.md#公共-contract-与发布) 和 [错误处理](ERROR-HANDLING.md#cache-恢复)。

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

**状态：已关闭（2026-09-05）。** 维护者选择可独立公共 bundle，并授权破坏性升级。`audio-transcribe-contract` 0.2.0 使用公共 schema v2，入口为 `manifest.json`，唯一正文为合并句子 `segments` 与 alignment `items` 的 `transcript.json`。manifest 不再声明日志或 workspace，loader 只验证两文件，不检查私有内容的存在或类型。

配置身份统一命名为 `config_digest`，公共格式版本也参与摘要。consumer 返回结构简化为两份 snapshot 及其路径；生产端恢复通过当前音频和配置定位私有 workspace，不依赖公共 manifest 的私有路径。旧三文件合同/API 不迁移、不兼容，也不自动删除旧结果。

测试覆盖两文件独立复制、源结果不可用、无私有状态的生产 cache hit，以及 KI-002 所述恢复边界。公开合同及移动规则见 [架构](ARCHITECTURE.md#公共结果结构)。

## KI-009：“精确字段 shape”描述与实现不符

**结论：[KNOWN，部分修正 2026-09-05]** 原错误处理文档把所有公共结构笼统称为“精确字段 shape”，但 validator 只在部分节点拒绝未知字段。v2 升级已修正文档措辞，保留现有验证边界：

- alignment item 使用精确键集合；
- v2 manifest 的 `artifacts` 和 `artifact_sha256` 只允许 `transcript`；
- transcript segment、正文顶层、manifest 顶层及 audio/request 等其他节点主要验证已知必需字段，未统一拒绝未知字段。

本轮未统一决定所有节点的 additive-field 策略或补齐对应未知字段测试，因此不将本项标为完全关闭。后续应明确哪些节点允许增加字段，并使文档、类型、validator 和测试一致；不要因公共 schema 已升级就视为此问题自然解决。

## 与数据流重构的关系

数据流重构直接处理 KI-002 的无 manifest 场景；KI-003 已作为文档与报告说明同步问题关闭。后续 benchmark 方法改动关闭了 KI-004～KI-007。公共 v2 bundle 和统一恢复已关闭 KI-002 与 KI-008；KI-001 和 KI-009 的剩余事项仍需独立决策。

后续改动仍应避免把内部实现变化误认为公共合同问题已经解决。例如，删除 benchmark schema 不等于 reference provenance 可以删除；升级公共 schema 也不等于未知字段策略已经统一。
