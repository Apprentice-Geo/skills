# 错误处理

本地音频转写、Provider 执行、cache 复用或公共 artifact 验证失败时，使用此 reference。

## 快速索引

- [Setup 与依赖](#setup-与依赖)
- [模型安装](#模型安装)
- [输入音频与解码](#输入音频与解码)
- [VAD 与语言检测](#vad-与语言检测)
- [Provider 选择](#provider-选择)
- [Execution Policy](#execution-policy)
- [模型加载与推理](#模型加载与推理)
- [Alignment 与空结果](#alignment-与空结果)
- [公共 Artifact 验证](#公共-artifact-验证)
- [Cache 恢复](#cache-恢复)
- [日志](#日志)
- [停止条件](#停止条件)

## Setup 与依赖

- 从此 Skill 目录运行 `.\scripts\setup\setup_windows.bat` 执行 setup。
- 使用 Python 3.12 和 `uv`；未经明确批准，不得修复或替换现有 `.venv`。
- 依赖安装完成后，使用 `uv run --no-sync python` 运行转写命令和 setup 子命令。
- 依赖同步失败时，先检查 setup 日志、`pyproject.toml` 和 `uv.lock`，再重试。
- 项目 setup 提供打包的 ffmpeg 支持。如果由于找不到 ffmpeg 或缺少 import 依赖而解码失败，应重新运行或修复 setup，不得依赖无关的系统安装。

## 模型安装

转写前至少安装一个本地转写模型：

```powershell
uv run --no-sync python -m scripts.setup.install_model --model faster-whisper
```

对于 Qwen3-ASR，先安装可选依赖，并确保 CUDA 可用：

```powershell
uv sync --python 3.12 --no-dev --extra qwen3-asr
uv run --no-sync python -m scripts.setup.install_model --model qwen3-asr
```

如果固定 revision 的模型 artifact 缺失、不完整或 revision 错误，重新安装所请求的模型。不得臆造 revision 值，也不得把仅部分下载的模型目录视为 ready。

省略 `--language` 时必须使用语言识别模型。如果该模型缺失，通过 setup 安装模型，或传入明确的 `--language` 重新运行。

## 输入音频与解码

- 如果输入路径不存在或不是文件，停止执行并要求提供有效的本地音频路径。
- 不得用下载的副本或其他媒体来源替代缺失的本地文件。
- 如果解码未返回 sample，停止执行。解码后的空音频不得发布完整 manifest。
- 如果解码失败，检查 faster-whisper 音频解码依赖和打包的 ffmpeg 是否可用。
- Audio identity 基于文件字节。不得仅根据文件名、标题或目录就假定两个路径是同一输入。

## VAD 与语言检测

- 自动语言检测运行前，VAD 必须生成可用语音。
- 自动语言检测最多使用 30 秒按 VAD 顺序排列的语音。不得对任意静音或无关音频运行语言检测。
- 如果语言检测返回空语言或无效语言，停止执行。
- 如果语言置信度较低，命令可以使用得分最高的语言继续，并应记录警告。
- 如果用户提供了 `--language`，使用该 resolved language，不得猜测或修正。

## Provider 选择

- 受支持的公共 Provider 为 `faster-whisper` 和 `qwen3-asr`。
- 如果 `--provider` 指定了不受支持的 Provider，停止执行。
- 如果 `--provider qwen3-asr` 搭配不受支持的语言，停止执行并报告受支持的语言集合。
- 如果未指定 Provider，仅从当前环境中 ready 的 Provider 里选择。
- 如果没有 Provider ready，停止执行，并要求用户安装 Qwen3-ASR 或 faster-whisper。
- Provider 一旦解析完成，发生加载、推理、alignment 或 artifact 失败后，不得静默切换 Provider。

## Execution Policy

- faster-whisper 按 CPU policy 运行。提供 `--num-workers` 和 `--cpu-threads` 时，其值必须为正整数。
- faster-whisper 的 worker/thread 乘积必须处于计算得到的 CPU budget 内。如果超出 budget，减少 worker 或 thread 后重新运行。
- Qwen3-ASR 按 CUDA policy 运行，并要求 CUDA 可用，同时存在本地 ASR 和 forced-aligner 模型 artifact。
- Execution policy value 是 `variant_id` 的组成部分。不得编辑 manifest，伪装失败运行使用了不同的 policy。

## 模型加载与推理

- 如果 faster-whisper 未安装或其本地 `model.bin` 缺失，安装或修复 faster-whisper 后再重试。
- 如果缺少 Qwen3-ASR 依赖、CUDA、ASR weight 或 forced-aligner weight，安装或修复 Qwen3-ASR 后再重试。
- 如果 Provider 返回意外的结果 shape，先检查固定的依赖版本和 adapter，再考虑更改公共 artifact schema。
- 不得在公共 artifact 中存储第三方模型对象、原始 Provider response 或大型内部 metadata。
- Provider chunk 文本不执行改写。在合并后的 workspace/公共边界，对所有语言应用 NFKC，仅对 `zh` 应用 OpenCC `t2s`。

## Alignment 与空结果

- 完整转写必须包含非空文本和 timestamp item。
- 在执行可恢复的 zero-duration cleanup 前，把 Provider candidate 量化到毫秒。量化产生的 zero-duration item 连同且仅连同归其所有的字符文本一起移除；没有 owner 的标点和空白应予以保留，除非 accepted chunk 因此只剩这些内容而变为空。
- 单个 accepted chunk 可以为空，并在合并时忽略。如果所有 chunk 合并后得到空 transcript，停止执行。
- 拒绝包含负数、非有限、重叠、递减、反向或超出 duration 时间的 timestamp。accepted item 和公共 item 必须满足 `0 <= start < end <= duration` 和 `start >= previous_end`。
- 拒绝文本为空的 timestamp item。
- 拒绝 `[0, 1]` 之外的非 null probability，以及任何非有限 probability。
- 拒绝 probability 非 null 的 Qwen3-ASR timestamp item。
- 保留由标点驱动的分段行为。如果句子输出看起来有误，检查 alignment item 和 segmentation 规则，不得手动编辑已发布的 `transcript.json`。
- alignment 验证失败时，不得发布 `result_manifest.json`。
- 如果文本规范化失败，或规范化后的文本与 item 不再对齐，停止执行，不得回退到未规范化的公共文本。
- 不得把超出范围的 end time 裁剪到 duration，也不得把格式错误的 workspace/公共字段强制转换为字符串或浮点数。

## 公共 Artifact 验证

使用结果前，以 `result_manifest.json` 调用 `audio_transcribe_contract.load_result`。它验证：

1. schema version 为 1，status 为 `complete`，且 `request.alignment_policy` 与受支持的 v1 policy 精确匹配；
2. `audio.id` 和 `request.variant_id` 是 64 字符 SHA-256 值；
3. `variant_id` 与排除 `variant_id` 后的 canonical request JSON 匹配；
4. artifact 路径相对于 manifest 目录，且无法逃逸该目录；
5. transcript 和 raw timestamp 文件存在，并与 manifest 中的 SHA-256 digest 匹配；
6. transcript 和 raw timestamp 的 schema、精确字段 shape、identity、Provider、language、duration、probability 和严格 timing contract 均有效；
7. 日志存在，且 workspace 目录存在。

artifact 路径不是相对于 manifest 目录、artifact 使用绝对路径、存在 `..` 逃逸、schema 错误、alignment policy 缺失或遭修改、status 不是 complete、digest 不匹配、identity 无效、存在 zero-duration timestamp、日志缺失或 workspace 缺失，都会使结果不安全。contract package 0.1.2 独立于内部 pipeline 代码验证该 policy。

公共 schema 保持 v1，但在强制要求 `alignment_policy` 之前创建的结果被有意设为不兼容。不得手动添加该字段：它参与 `variant_id`，因此必须重新转写以生成新结果。

不得手动修复公共 JSON。重新运行命令，由 artifact layer 验证或恢复结果。

## Cache 恢复

如果存在完整 manifest，命令首先验证它。有效 cache hit 返回现有 manifest 路径，不重新运行推理。

私有 ASR cache 使用 schema v2，且仅存储 accepted 结果。旧 schema、错误 policy 或格式错误的 chunk 会失效并重新计算；cache 读取时严格重新验证 alignment。不得迁移或手动编辑旧 cache 条目。

毫秒量化移除 zero-duration item 时，日志为每个受影响的 chunk 发出一条不含 transcript 文本的聚合 `WARNING`。部分恢复会为每个受影响的 cached chunk 重放一次警告，使新的 attempt 保持可审计。完整 manifest hit 以及复用所有 chunk cache 的 attempt 不会再次生成 cleanup 警告。

如果公共 artifact 缺失或损坏，仅当 `workspace/result.json` 能够重建 `transcript.json` 和 `raw_timestamps.json`，且其 SHA-256 digest 与隐藏 manifest 中记录的值完全一致时，才允许恢复。恢复成功时逐字节还原原始 manifest。

workspace recovery snapshot 已包含规范化文本。恢复流程重新验证其精确 shape 和 alignment，但不再次运行 OpenCC，也不修改 Provider chunk cache。

如果 workspace 重建失败或产生不同的 digest，保留原始完整 manifest 及其最后已知的公共 artifact，报告恢复失败，并在稍后运行中重试恢复或转写。不得从其他 variant 复制文件，也不得编辑 digest 以匹配新字节。

variant lock 防止验证、恢复、推理和发布受到并发写入影响。如果某个进程似乎阻塞在 lock 上，删除任何 lock 相关文件前，先检查正在运行的转写进程。

首次发布期间，`.result_manifest.json.incomplete` 只是 candidate。artifact layer 通过 `load_result()` 验证它，然后才以正式 manifest 原子替换。如果 candidate 验证失败，停止执行；删除 candidate，并且不创建正式成功标记。`.result_manifest.json.recovery` 保留给恢复流程，禁止把它视为成功结果。

## 日志

- CLI 成功时输出耗时和 `result_manifest.json` 绝对路径。
- 不得根据部分文件、workspace 文件或结果目录名声称成功。
- 失败时，CLI 输出 `Transcription failed: ...` 并以非零状态退出。
- 报告失败时，应包含精确命令、简洁错误、可用的结果或日志路径，以及是否存在完整 manifest。
- 报告中不得包含 transcript 文本、其他 workflow 的 Cookie 内容、原始模型对象或不必要的敏感本地路径。
- 可以使用精确的 logger/message-prefix filter 过滤已知的嘈杂第三方警告。不得抑制未知警告或异常。
- Cache hit 不得改写首次成功的 `transcribe.log`。

## 停止条件

出现以下情况时，停止执行，不得生成或使用 transcript：

- 输入的本地音频文件缺失；
- 解码失败或解码后的音频为空；
- 需要自动语言检测，但 language-id 模型或可用语音不可用；
- resolved language 为空或无效；
- 请求的 Provider 不受支持或未 ready；
- 请求 Qwen3-ASR，但缺少 CUDA 或所需的本地模型 artifact；
- 请求 faster-whisper，但缺少所需依赖或本地模型 artifact；
- CPU worker/thread 选项无效或超出 budget；
- Provider 推理失败；
- alignment 验证失败；
- 无法按 source 顺序把 Provider 文本映射到 timestamp item；
- zero-duration cleanup 后，每个 accepted chunk 都为空；
- 文本或 timestamp item 为空；
- resolved request 缺少精确匹配的受支持 alignment policy，包括加载旧 manifest 时；
- workspace 或公共 artifact 字段具有无效类型、shape、probability，或超出范围/zero-duration timestamp；
- 正式 manifest 安装前，publication candidate 验证失败；
- 公共 artifact 验证失败，且恢复无法复现已发布的 digest；
- 没有任何完整 `result_manifest.json` 验证成功。

禁止通过编辑 `result_manifest.json`、`transcript.json`、`raw_timestamps.json` 或 `workspace/` 下的文件绕过停止条件。
