# 架构

`audio-transcribe` 拥有本地音频解析、转写和公共产物发布。总结与字幕 Skill 只通过公共 loader 导入结果，各自拥有导入后的本地快照。

## 控制流与数据流

```text
本地音频 -> SHA-256 与解析信息 -> 语言和 ready Provider
  -> resolved request 与 config_digest
  -> 完整公共结果复用，或 workspace 恢复
  -> VAD / 精确切片 / Provider 推理 / alignment 接受
  -> 全局合并与文字规范化 -> 句子分段
  -> 候选 bundle 验证 -> 正文发布 -> manifest 最后发布
```

源码、模型和 `.venv` 独立于运行数据目录。CLI 日志始终位于 `<data-dir>/.cache/logs/`，不随结果搬迁。Python API 可以显式指定日志；未指定时保留结果目录内的 `transcribe.log`。路径配置见[运行目录](ERROR-HANDLING.md#运行目录与写入失败)。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `scripts/transcribe.py` | 音频身份、请求解析、完整结果复用及恢复编排 |
| `scripts/input_cache.py` | 可再生的解码长度和语言检测缓存 |
| `scripts/asr/chunking/` | VAD、整数采样时间线、长度约束与精确边界求解 |
| `scripts/asr/providers/` | 第三方模型加载及返回值适配 |
| `scripts/asr/execution/` | CPU 并发、GPU batch、失败隔离和 chunk 缓存回调 |
| `scripts/asr/alignment.py` | alignment 接受、字符归属、零时长清理、偏移与验证 |
| `scripts/asr/pipeline.py`、`workspace.py` | plan/chunk 恢复、合并、规范化与私有结果 |
| `scripts/artifacts.py` | 候选 bundle、自验证、锁与 manifest-last 发布 |
| `scripts/model_identity.py`、`model_artifacts.py`、`setup/` | 本地模型身份和环境安装校验 |
| `packages/audio-transcribe-contract/` | 独立于模型运行时的公共类型及只读 loader |

## 身份与缓存

- `audio_id` 是输入文件字节 SHA-256，与路径无关。
- `config_digest` 是 canonical resolved request 的 SHA-256，包含实际模型 revision、执行策略、VAD、规划、alignment 和文字规范化字段，不是一次调用编号。
- `audio_id + config_digest` 定位公共结果。正文 SHA-256 标识该次发布的完整文件字节；同身份的损坏修复可以重新发布不同正文。
- `boundary_strategy=minimum-hard-cuts-minimax-backtrack` 明确当前切片行为，参与 config 和 plan identity。旧启发式策略的结果与 plan 不作为当前结果复用。

每个音频目录的 `input.json` 或 `input-auto.json` 只缓存采样数量与检测语言。身份绑定音频、解码依赖及实际检测模型、依赖、VAD 参数，内容 digest 和精确字段不符则重新解析；不维护迁移。生产默认解码和检测才使用此缓存，自定义函数不借用缓存。缓存命中仍核对实际语言模型和所选 Provider 安装。

完整公共结果命中可跳过解码、VAD、语言模型推理和 ASR；需重建 workspace 时再解码。解析缓存不认证公共结果，不替代 `load_result()`。

## 切片保证

固定切片数与长度约束下，先精确最小化语音内部的硬切数量，再精确最小化最大语音负载。通过区间可达性回溯取得确定性合法边界；仍需硬切时直接使用这些边界。

不再搜索启发式硬切候选、事件节点或优化负载均方差。无语音与全语音使用对应闭式解。各候选切片数按硬切数、执行轮数、最大负载及确定性边界比较；切片始终连续覆盖完整输入。

这些保证针对 VAD 时间线与代理目标，不等同于实际 CER/WER 或耗时最优。benchmark 方法见[benchmark 文档](../benchmark/README.md)。

## Alignment 与规范化

Provider 返回 candidate 后统一验证有限数值、范围、顺序、概率与字符归属。仅删除原本零时长 item 及其所属文字；Qwen 尾部误差的有限修正由 adapter 处理。合并应用整数采样起点对应的全局偏移，再验证全局 alignment。

公开文字统一 NFKC，仅 `zh` 再执行 OpenCC `t2s`。规范化通过字符归属反向投影至 items，不使用全局文本替换。句子分段从合法 alignment 生成，保留时间精度；SRT 毫秒取整属于字幕消费者。

## 私有 workspace

```text
results/<audio_id>/
├─ input.json 或 input-auto.json
└─ <provider>-<language>-<config_digest>/
   ├─ manifest.json
   ├─ transcript.json
   └─ workspace/
      ├─ asr_plan.json
      ├─ vad_result.json
      ├─ chunk_results/
      └─ result.json
```

plan 是 chunk 布局的唯一权威，`plan_id` 绑定 source、请求、执行策略、VAD、规划与 layouts；命中 plan 不重新推导 VAD。chunk 保存 accepted 原始文字与 items；合并后 `result.json` 保存规范化文字与全局 items，不保存句子分段。

私有数据仅接受当前严格结构，损坏或不兼容即 miss。完整 chunk 可重新合并；缺失 chunk 才推理。进度和 metrics 不另设持久化状态机，诊断由内存结果与日志承载。

## 公共 contract 与发布

`audio-transcribe-contract` 0.4.0 接受公共 schema 4。两个公共文件可保持字节和相对路径一起移动，不依赖模型、原音频或 workspace。旧 schema 不自动迁移、不删除；需要新格式时重新运行生产入口。

manifest 包含音频身份、resolved request、正文相对路径及 SHA-256；正文包含一致的身份、Provider、语言、duration、`segments` 与 `items`。消费者必需字段严格验证；六类来源配置为有限 JSON 对象并参与 canonical digest，loader 不解释生产端模型、batch、worker 或切片策略。生产端负责实际配置合法性。

`load_result()` 完整校验形状、摘要、路径包含关系、时间轴和跨文件身份，返回内存 snapshot。`load_manifest()` 只校验元数据与路径，不能认证正文。修改返回对象不写回磁盘。

发布与恢复在 result lock 内进行：先验证 staging 中完整候选，再替换正文，最后原子发布 manifest。多文件替换不构成整体原子事务，异常后尝试回滚，下次读取必须完整验证。

有效完整 bundle 直接复用；有效但身份不匹配的 manifest 拒绝覆盖。损坏结果优先从合法 `workspace/result.json` 恢复，其次重建 plan/chunks。恢复正文 digest 不变时保留原 manifest 字节，变化时仅更新正文摘要；候选验证失败保留旧公共文件。
