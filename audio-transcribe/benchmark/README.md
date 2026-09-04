# 转写 Benchmark

本 benchmark 比较生产 `project-slicing` pipeline 与把完整音频直接交给 Provider 的 `provider-native` 路径。它不修改公开 manifest 合同，也不把内部 workspace 暴露给其他 Skill。

## 数据来源与准备

数据源记录在 [sources.json](sources.json)：LibriVox 的中文《呐喊》和英文 *The Great Gatsby*，均标记为 Public Domain Mark 1.0。首次正式准备必须显式下载并固定摘要：

```powershell
uv run --no-sync python -m benchmark.prepare_audio --pin-sha256
```

提交固定后的 `sources.json`。之后省略 `--pin-sha256`，工具会在使用缓存前校验 SHA256。下载和发布均使用临时文件；摘要不匹配会停止。

工具使用项目打包的 FFmpeg 生成 16 kHz 单声道 PCM WAV，再用生产 Silero VAD 为 8、16、32、64 分钟的嵌套前缀选择截点。目标落在语音内时顺延到该段结束；默认 300 秒范围内没有安全边界则停止，可用 `--lookahead-seconds` 增大范围。生成文件和原始音频位于被忽略的 `benchmark/data/`。

## 固定 Reference

提交到 [references/](references/) 的八个 UTF-8/LF 文本片段为中英文 8、16、32、64 分钟样本提供累积 reference。`manifest.json` 记录作品、作者、朗读者、音频和固定底本 URL，并把每个片段的原始 SHA256、每个 WAV 的 SHA256 与 `benchmark/data/samples.json` 绑定。运行期只读这些文件，不生成、修复或改写 reference。

Reference 以公版原著底本为正文来源，并用 Qwen3-ASR 的 `project-slicing` 和 `provider-native` 去重输出辅助定位，再对底本外口播、朗读/底本冲突和四个截点定点进行人工听校。

人工听校并非对整段录音进行了全量逐字听写式校订；因此该 Reference 是固定评估基准，而不是完整录音 ground truth，Reference CER/WER 也不应表述为绝对准确率。

中文 hypothesis 与 reference 对称执行 NFKC、OpenCC `t2s`，再删除 Unicode 空白和标点，以 reference 字符数为 CER 分母。英文两侧执行 NFKC、`casefold` 后提取 Unicode 单词，保留词内 `'` 和 `’`，以 reference 词数为 WER 分母。标点单独按 Unicode 标点计数。

## 运行与恢复

完整矩阵为 2 种语言 × 2 个 Provider × 4 档长度 × 2 种 mode × 3 次重复：

```powershell
uv run --no-sync python -m benchmark
```

筛选参数可以重复提供：

```powershell
uv run --no-sync python -m benchmark --provider faster-whisper --language zh --minutes 8 --mode project-slicing --mode provider-native
```

`--repetitions` 控制重复次数，`--report` 指定 JSON 路径。默认报告为 `benchmark/reports/<当天日期>.json`，Markdown 写到同名文件。新报告会冻结完整的 Provider、语言、时长、mode 和 repetition config；续跑时，未显式提供的矩阵参数继承报告值，显式参数规范化后必须与完整 config 一致。扩展或缩小矩阵必须使用新报告路径。

当前格式 JSON 会原子保存恢复点：成功 run 按 identity 跳过，失败 attempt 保留并在下次执行时追加重试。旧格式不迁移、不重评分，也不为已有成功 run 重新计算指标；无法按当前结构读取时必须使用新报告路径。每个 Provider 在自身测试开始前使用 config 的第一个样本，以 `project-slicing` 和 `repetition=0` 预热一次；warmup 不进入统计，也不计算 Reference CER/WER。每次 worker 使用独立子进程和带随机 nonce 的 `benchmark/tmp/<run-id>-<nonce>/results/`，不会复用旧报告或先前运行留下的结果、workspace 和模型进程。

续跑不会根据代码提交、依赖、模型 revision、Provider identity、execution identity 或机器自动拒绝已有报告。恢复提示假设这些条件没有变化；任何条件变化后都应使用新报告路径，否则报告可能混合不可直接比较的结果。顶层 `environment` 仅是报告创建时的快照。

## 模式与报告

- `project-slicing` 调用生产 `run_transcribe()`，包括 VAD、规划、Provider、合并和公开 artifact 发布。
- `provider-native` 复用生产 Provider adapter、模型参数和 execution identity，但完整音频只作为一个 Provider 输入，不调用项目 VAD、规划、合并或发布。faster-whisper 的单次原生推理显式使用与 `project-slicing` 相同的 CPU budget 作为 `cpu_threads`，使两种模式拥有相同的 CPU 线程预算。Qwen3-ASR 0.0.6 在时间戳模式下仍可能原生按最长 180 秒切分。

JSON 保留冻结 config、原始 run、失败、预热、环境、模型 revision、wall/Provider stage、RTF、进程树峰值 RSS 和可用时的 NVIDIA 显存。报告级 `reference_set` 固定 manifest、音频和规范化 reference 摘要；每个正式 run 记录 `audio_sha256`，每个成功 run 记录以 reference 为分母的 `reference_comparison`。报告与 reference manifest 都只支持当前结构，不包含内部 schema 版本。

现有模式间比较仍按相同 repetition 配对，只有配对成功的 `project-slicing` run 记录 `output_comparison`：中文为 CER、英文为 WER，`provider-native` 是分母。它只表示两种执行模式的输出差异，与 Reference CER/WER 含义不同。Markdown 对每个 mode 显示 Reference CER/WER 中位数和 `hypothesis/reference` 标点中位数，仅在 `project-slicing` 行显示 `Mode difference`；缺失或失败 counterpart 不参与模式差异。
