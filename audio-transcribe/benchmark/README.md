# 转写 Benchmark

本文主要面向维护者、开发者和执行 benchmark 任务的 Agent，普通转写用户无需阅读。它是 benchmark 方法、数据、reference、运行、恢复、报告和维护规则的唯一详细说明；当前可观察行为由 `benchmark/` 实现和对应测试共同验证。

本 benchmark 比较生产 `project-slicing` pipeline 与把完整音频直接交给 Provider 的 `provider-native` 路径。它不修改公开 manifest 合同，也不把内部 workspace 暴露给其他 Skill。生产 Skill 不下载媒体；benchmark 数据准备是隔离的开发流程，可以按下述明确命令下载固定测试素材。

## 数据来源与准备

数据源记录在 [sources.json](sources.json)：LibriVox 的中文《呐喊》和英文 *The Great Gatsby*，均标记为 Public Domain Mark 1.0。首次正式准备必须显式下载并固定摘要：

```powershell
uv run --no-sync python -m benchmark.prepare_audio --pin-sha256
```

该命令会访问网络、写入本地测试音频并固定来源摘要。Agent 仅在用户明确要求首次准备或更新 benchmark 数据身份时运行它，不得把它作为普通测试或环境检查的一部分。

提交固定后的 `sources.json`。之后省略 `--pin-sha256`，工具会在使用缓存前校验 SHA256。下载和发布均使用临时文件；摘要不匹配会停止。

工具使用项目打包的 FFmpeg 生成 16 kHz 单声道 PCM WAV，再用生产 Silero VAD 为 8、16、32、64 分钟的嵌套前缀选择截点。目标落在语音内时顺延到该段结束；默认 300 秒范围内没有安全边界则停止，可用 `--lookahead-seconds` 增大范围。生成文件和原始音频位于被忽略的 `benchmark/data/`。

## 固定 Reference

提交到 [references/](references/) 的八个 UTF-8/LF 文本片段为中英文 8、16、32、64 分钟样本提供累积 reference。`manifest.json` 记录作品、作者、朗读者、音频和固定底本 URL，并把每个片段的原始 SHA256、每个 WAV 的 SHA256 与 `benchmark/data/samples.json` 绑定。运行期只读这些文件，不生成、修复或改写 reference。

Reference 以公版原著底本为正文来源，并用 Qwen3-ASR 的 `project-slicing` 和 `provider-native` 去重输出辅助定位，再对底本外口播、朗读/底本冲突和四个截点定点进行人工听校。人工听校并非对整段录音进行了全量逐字听写式校订；因此该 Reference 是固定评估基准，而不是完整录音 ground truth，Reference CER/WER 也不应表述为绝对准确率。

中文 hypothesis 与 reference 对称执行 NFKC、OpenCC `t2s`，再删除 Unicode 空白和标点，以 reference 字符数为 CER 分母。英文两侧执行 NFKC、`casefold` 后提取 Unicode 单词，保留词内 `'` 和 `’`，以 reference 词数为 WER 分母。标点单独按 Unicode 标点计数。

## 运行与恢复

真实 benchmark 会加载本地模型、执行预热并处理长音频，可能长时间占用 CPU、GPU、内存和磁盘。完整矩阵为 2 种语言 × 2 个 Provider × 4 档长度 × 2 种 mode × 3 次重复，即 96 个正式 run，另有各模型配置的预热：

```powershell
uv run --no-sync python -m benchmark
```

只有任务明确需要真实性能或准确率证据时，才运行覆盖目标行为的最小筛选矩阵；完整矩阵仅在用户明确要求时运行。未执行真实 benchmark 时，应说明验证范围，不得声称已验证真实性能、RTF 或转写准确率。

筛选参数可以重复提供，具体见下表：

| 参数 | 期望输入形式 | 作用 |
| --- | --- | --- |
| `-h`、`--help` | 不接收值 | 显示命令帮助和可选参数后退出。 |
| `--provider` | `faster-whisper` 或 `qwen3-asr`；每次一个值，可重复提供 | 选择参与 benchmark 的 Provider；新报告省略时包含全部 Provider，续跑省略时继承已有报告。 |
| `--language` | `zh` 或 `en`；每次一个值，可重复提供 | 选择参与 benchmark 的语言；新报告省略时包含全部语言，续跑省略时继承已有报告。 |
| `--minutes` | `8`、`16`、`32` 或 `64`；每次一个整数，可重复提供 | 选择参与 benchmark 的音频长度；新报告省略时包含全部长度，续跑省略时继承已有报告。 |
| `--mode` | `project-slicing` 或 `provider-native`；每次一个值，可重复提供 | 选择参与 benchmark 的执行模式；新报告省略时包含全部模式，续跑省略时继承已有报告。 |
| `--repetitions` | 大于等于 `1` 的整数 | 设置每个 Provider、语言、长度和 mode 组合的重复次数；新报告省略时为 `3`，续跑省略时继承已有报告。 |
| `--report` | JSON 文件路径，例如 `benchmark/reports/experiment.json` | 指定报告及恢复点；省略时为 `benchmark/reports/<当天日期>.json`，Markdown 摘要写入同路径、同文件名的 `.md` 文件。指向已有报告时进入续跑。 |

新报告会冻结完整的 Provider、语言、时长、mode 和 repetition config；续跑时，未显式提供的矩阵参数继承报告值，显式参数规范化后必须与完整 config 一致。扩展或缩小矩阵必须使用新报告路径。

当前格式 JSON 会原子保存恢复点：成功 run 按 identity 跳过，失败 attempt 保留并在下次执行时追加重试。旧格式不迁移、不重评分，也不为已有成功 run 重新计算指标；缺少当前硬件身份、session 或 warmup 信息的旧报告必须使用新路径。

每个仍有待执行项的 Provider 启动一个持久 worker 子进程。正式 run 所需的每种模型加载配置首次出现时，worker 使用该 run 相同的音频和 mode 完成一次不计入统计的预热，然后在同一 session 中复用 prepared model；语言不影响模型加载，因此不属于缓存 key。faster-whisper 的 `project-slicing` 配置可能随时长改变，`provider-native` 则固定使用单 worker 和生产 policy 算出的全部 CPU 线程预算；不同配置会分别预热。每个正式 run 仍使用带随机 nonce 的独立 `benchmark/tmp/<run-id>-<nonce>/results/`，不会复用先前 run 的 workspace。worker 意外退出时当前 run 失败，后续 run 在新 session 中重新预热。续跑也总是创建新 session 并重新预热待执行配置；若没有待执行项，则不启动 worker。

顶层 `environment` 将 CPU 型号、逻辑核心数、物理内存总量以及按设备序号排序的 GPU 型号和总显存记录为硬件身份。续跑前会重新采集并严格比较这些字段；不同或缺失时，在 worker 启动前拒绝并要求使用新报告路径。不支持跨设备续跑。系统、Python、commit、依赖和模型 revision 是只展示的审计信息，不参与自动续跑判断；代码、依赖或模型变化后仍应主动使用新报告路径。

## 模式与报告

- `project-slicing` 调用生产 `run_transcribe()`，包括 VAD、规划、Provider、合并和公开 artifact 发布。
- `provider-native` 复用生产 Provider adapter 和模型参数，但完整音频只作为一个 Provider 输入，不调用项目 VAD、规划、合并或发布。faster-whisper 使用 `num_workers=1`，并把生产 CPU policy 为该机器算出的全部线程预算作为 `cpu_threads`；该 benchmark identity 不执行项目切片合法性检查。Qwen3-ASR 0.0.6 在时间戳模式下仍可能原生按最长 180 秒切分。

这两个 mode 比较的是两套端到端策略。wall time、RTF、相对速度和文本差异同时包含两条路径的多项差别。

JSON 保留冻结 config、原始 run、失败、预热、session、环境、模型 revision、wall/Provider stage 和 RTF。报告级 `reference_set` 固定 manifest、音频和规范化 reference 摘要；每个正式 run 记录 `audio_sha256`，每个成功 run 记录以 reference 为分母的 `reference_comparison`。报告与 reference manifest 都只支持当前结构，不包含内部 schema 版本。当前 benchmark 不测量或比较进程内存与 GPU 显存占用；“测试设备”中的物理内存和 GPU 总显存仅表示硬件容量。

现有模式间比较仍按相同 repetition 配对，只有配对成功的 `project-slicing` run 记录 `output_comparison`：中文为 CER、英文为 WER，`provider-native` 是分母。它只表示两种执行模式的输出差异，与 Reference CER/WER 含义不同。Markdown 对每个 mode 显示 Reference CER/WER 中位数和 `hypothesis/reference` 标点中位数，仅在 `project-slicing` 行显示 `Mode difference`；缺失或失败 counterpart 不参与模式差异。

## 开发与维护

- 修改 runner、worker、预热、执行模式、指标或报告摘要时，运行 `uv run pytest tests/test_benchmark.py tests/test_cli_output.py`。
- 修改固定 reference、样本身份、报告恢复或验证边界时，运行 `uv run pytest tests/test_benchmark_reference.py`；若同时改变 CLI、runner、worker、指标或报告摘要，再运行上一项中的对应测试。
- 变更跨越 benchmark 与生产 pipeline 或公开合同时，在聚焦测试通过后运行完整 `uv run pytest`，并同步检查相应的长期架构或错误处理文档。
- `benchmark/data/`、`benchmark/tmp/` 和 `benchmark/reports/` 是被忽略的本地或生成内容，不应提交。提交到 `benchmark/references/` 的固定 reference 和 manifest 属于 benchmark 数据合同，不得由运行期自动修复或改写。
- 仓库根目录 `references/` 下的阶段性计划、调查、已知问题记录和 benchmark 报告只用于追溯特定工作或运行，不是当前 benchmark 合同；不要从中推断当前命令或行为，是否删除由维护者决定。
