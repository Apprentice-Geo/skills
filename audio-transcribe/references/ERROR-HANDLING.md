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

安装校验要求 `.model_identity.json` 是无重复键且仅包含字符串 repo/revision 的 JSON 对象，并与固定配置完全匹配；必需文件与权重必须存在且非空，indexed safetensors 的索引和全部分片必须合法且位于模型目录内。setup 复用、依赖检查、自动 Provider 候选检查和运行时使用相同规则。模型加载仍负责识别文件内容是否可用。

marker 缺失、损坏、不匹配或模型文件不完整时，重新安装所请求的模型。禁止推测 revision 或手工补写 marker。显式 Provider 失败时立即停止；自动选择排除不合格候选，解析完成后不再切换。模型加载前复核；完整 cache 不能绕过请求身份前的模型检查。Whisper 自定义路径同样必须匹配固定身份；Qwen 的 ASR 与 aligner 独立校验。

这只证明目录具有匹配安装记录及基本文件结构，不证明安装后每个模型字节未变。项目不计算全量/抽样权重摘要，不以 mtime 或文件大小指纹冒充内容身份。运行期间不得替换模型目录。consumer loader 只验证 bundle，不访问模型。

prepared model 必须携带加载时绑定的身份和配置摘要；缺失或不匹配时停止，不能用当前磁盘 marker 为未知内存模型补认身份。

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
- Execution policy value 是 `config_digest` 的组成部分。不得编辑 manifest，伪装失败运行使用了不同的 policy。

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
- alignment 验证失败时，不得发布 `manifest.json`。
- 如果文本规范化失败，或规范化后的文本与 item 不再对齐，停止执行，不得回退到未规范化的公共文本。
- 不得把超出范围的 end time 裁剪到 duration，也不得把格式错误的 workspace/公共字段强制转换为字符串或浮点数。

## 公共 Artifact 验证

使用结果前，以 `manifest.json` 调用 `audio_transcribe_contract.load_result`。契约包 0.2.0 验证：

1. manifest 和正文的公共 schema version 为 3，status 为 `complete`；`request.public_schema_version` 为 3，固定 `alignment_policy` 与受支持的 v1 policy 匹配；
2. `audio.id` 和 `request.config_digest` 是 64 字符 SHA-256 值，后者与排除自身字段后的 canonical request JSON 匹配；
3. manifest 只声明 transcript artifact 及其 SHA-256；路径相对于 manifest 目录，不能逃逸该目录或指向 manifest 自身；
4. `transcript.json` 存在，文件字节与记录的 SHA-256 匹配；
5. 两份 JSON 的所有对象（包括 request 内全部配置）均要求精确字段集合和正确类型；拒绝未知字段、缺失字段和跨 Provider 配置。identity、Provider、language、duration、句子分段、item probability 和严格 timing contract 均有效。

日志和 workspace 不参与公共验证，缺少它们不影响 bundle 读取。`load_manifest()` 只验证元数据和路径，正文不存在或损坏时也可能成功；不得据此声称完整转写成功。

旧公共 schema v1/v2 和旧入口/API 不兼容。不手动重命名或编辑旧文件迁移；重新运行命令生成 v3 结果。固定 alignment policy 的版本继续为 1，与公共 schema v3 是不同层面的版本。

## Cache 恢复

恢复只由生产命令执行，consumer loader 不修复文件。输入音频仍需存在；命令计算 `audio_id` 和当前 resolved request 的 `config_digest`，定位本地结果目录及其固定 `workspace/result.json`。manifest 不记录或控制私有路径。自动 Provider、语言或其他配置变化会定位到不同结果，不能仅凭相同音频复用另一配置。

私有 ASR cache 只支持当前严格格式，且仅存储 accepted 结果。plan 通过重新计算的 `plan_id` 验证内容身份，chunk 必须绑定当前 `plan_id` 并在读取时重新验证 alignment。旧字段、未知字段、错误身份或格式错误的条目自然失效；不得迁移或手动编辑旧 cache。

有效且匹配当前请求的 bundle 直接命中 cache，无需日志或 workspace。有效 manifest 若与当前音频或 resolved request 不匹配，停止执行并保留文件。

manifest 或正文缺失、损坏时，优先严格读取 workspace snapshot；snapshot 不可用时进入 pipeline，从合法 plan/chunks 重建，缓存不足再执行正常推理。合法 plan 不依赖 VAD cache；只有 plan miss 时才读取或重算 VAD。已有完整 manifest 不再阻断 chunk 恢复。

- manifest 元数据有效且身份匹配：重建 SHA-256 相同时精确恢复，保留原 manifest 字节；不同时允许重新发布，仅更新 `artifact_sha256.transcript`，保留原 artifact 相对路径和其他元数据。SHA-256 覆盖整个正文文件，包括元数据、segments、items 和 JSON 字节格式。
- manifest 缺失或无效：允许使用当前音频、resolved request 和合法 snapshot 重新发布；不承诺复现历史结果字节。

workspace snapshot 已包含规范化文本，公共转换不再次运行 OpenCC。chunk 重建仍执行完整合并和规范化。新接受 candidate 的 zero-duration cleanup 发出聚合警告；chunk cache 不保存 cleanup report，恢复不重放历史警告。pipeline 不新写或删除历史 `progress.json`、`metrics.json`。

发布前在临时 staging 目录验证完整两文件 candidate；loader 仍严格检查候选正文与候选 manifest 的 digest 一致。重建失败或 candidate 无效时保留已有公共文件。正式正文替换后才安装 manifest；安装失败时尝试恢复原正文。强制终止或磁盘持续故障可能阻断回滚，下次运行必须重新验证，不以部分文件判断成功。临时 staging 目录不属于公共入口。

result lock 覆盖检查、重建与发布。如果某个进程阻塞在 lock 上，删除锁文件前先检查运行中的转写进程。详细协议见 [架构](ARCHITECTURE.md#公共-contract-与发布)。

## 日志

- CLI 成功时输出耗时和 `manifest.json` 绝对路径。
- 不得根据部分文件、workspace 文件或结果目录名声称成功。
- 失败时，CLI 输出 `Transcription failed: ...` 并以非零状态退出。
- 报告失败时，应包含精确命令、简洁错误、可用的结果或日志路径，以及是否存在完整 manifest。
- 报告中不得包含 transcript 文本、其他 workflow 的 Cookie 内容、原始模型对象或不必要的敏感本地路径。
- 可以使用精确的 logger/message-prefix filter 过滤已知的嘈杂第三方警告。不得抑制未知警告或异常。
- Cache hit 不得改写首次成功的 `transcribe.log`。已有 manifest 的恢复尝试追加日志；日志缺失时可以重新创建，不影响公共结果合同。
- 成功安装后才记录发布诊断：相同 digest 的精确恢复为 INFO；不同 digest 的重新发布为 WARNING，包含 `audio_id`、`config_digest` 和旧/新 digest；无有效原 manifest 的发布为 INFO。诊断不包含转写文本，失败不记录发布成功，不增加公共审计字段。

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
- 无效 workspace/cache 无法重建为合法结果，或候选公共正文具有无效字段、probability 或 timestamp；
- 正式 manifest 安装前，publication candidate 验证失败；
- 有效 manifest 与当前音频或 resolved request 身份不匹配；
- 公共 artifact 验证失败且无法重建或完成发布；
- 没有任何完整 `manifest.json` 验证成功。

禁止通过编辑 `manifest.json`、`transcript.json` 或 `workspace/` 下的文件绕过停止条件。
