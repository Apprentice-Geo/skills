# 转写 Benchmark

本 benchmark 比较生产 `project-slicing` pipeline 与把完整音频直接交给 Provider 的 `provider-native` 路径。它不修改公开 manifest 合同，也不把内部 workspace 暴露给其他 Skill。

## 数据来源与准备

数据源记录在 [sources.json](sources.json)：LibriVox 的中文《呐喊》和英文 *The Great Gatsby*，均标记为 Public Domain Mark 1.0。首次正式准备必须显式下载并固定摘要：

```powershell
uv run --no-sync python -m benchmark.prepare_audio --pin-sha256
```

提交固定后的 `sources.json`。之后省略 `--pin-sha256`，工具会在使用缓存前校验 SHA256。下载和发布均使用临时文件；摘要不匹配会停止。

工具使用项目打包的 FFmpeg 生成 16 kHz 单声道 PCM WAV，再用生产 Silero VAD 为 8、16、32、64 分钟的嵌套前缀选择截点。目标落在语音内时顺延到该段结束；默认 300 秒范围内没有安全边界则停止，可用 `--lookahead-seconds` 增大范围。生成文件和原始音频位于被忽略的 `benchmark/data/`。

## 运行与恢复

完整矩阵为 2 种语言 × 2 个 Provider × 4 档长度 × 2 种 mode × 3 次重复：

```powershell
uv run --no-sync python -m scripts.benchmark
```

筛选参数可以重复提供：

```powershell
uv run --no-sync python -m scripts.benchmark --provider faster-whisper --language zh --minutes 8 --mode project-slicing --mode provider-native
```

`--repetitions` 控制重复次数，`--report` 指定 JSON 路径。默认报告为 `benchmark/reports/<当天日期>.json`，Markdown 写到同名文件。已有 JSON 会原子合并：成功 run 跳过，失败 run 保留并在下次执行时重试。每个 Provider 的最短样本先预热一次且不进入统计；每次 run 使用独立子进程和 `benchmark/tmp/<run-id>/results/`，不会复用结果、workspace 或模型进程。

## 模式与报告

- `project-slicing` 调用生产 `run_transcribe()`，包括 VAD、规划、Provider、合并和公开 artifact 发布。
- `provider-native` 复用生产 Provider adapter、模型参数和 execution identity，但完整音频只作为一个 Provider 输入，不调用项目 VAD、规划、合并或发布。Qwen3-ASR 0.0.6 在时间戳模式下仍可能原生按最长 180 秒切分。

JSON 保留原始 run、失败、预热、环境、模型 revision、wall/Provider stage、RTF、进程树峰值 RSS 和可用时的 NVIDIA 显存。报告 schema 2 按相同 repetition 配对；只有 `project-slicing` run 记录 `output_comparison`。中文内容差异为 NFKC、OpenCC `t2s` 后删除 Unicode 空白与标点的 CER，英文为 NFKC、casefold 后 Unicode 单词 WER，native 是分母；双方 Unicode 标点数量另行记录。Markdown 使用完整配对的差异率和标点数量中位数，缺失或失败 counterpart 不参与。旧 schema 报告不迁移，应重新运行生成。
