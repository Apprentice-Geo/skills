# 2026-08-22 转写 Benchmark 记录

## 目的与范围

本次实验比较生产 `project-slicing` pipeline 与 `provider-native` 路径的性能和输出差异。前者包含项目的 VAD、规划、Provider 调用、合并和公开 artifact 发布；后者复用生产 Provider adapter、模型参数与 execution identity，但将完整音频作为单个 Provider 输入，不执行项目 VAD、规划、合并或发布。

实验覆盖 `faster-whisper` 和 `qwen3-asr` 两个 Provider、中英文两种语言、8/16/32/64 分钟四种长度、两种模式各 3 次重复。2 次预热不计入统计；96 次正式运行均成功，没有失败或重试。

## 运行条件

- 测试时间：2026-08-22。
- 代码版本：`b2f63a099cde01056bd21bd93d77a4b574c16138`。
- 操作系统：Windows 11 `10.0.26200`（64 位）；Python 3.12.13。
- 硬件：13th Gen Intel Core i9-13900HX (2.20 GHz)，32 个逻辑 CPU；NVIDIA GeForce RTX 4060 Laptop GPU。
- 模型修订：`faster-whisper-small`（`Systran/faster-whisper-small`，`536b0662742c02347bc0e980a01041f333bce120`）；`qwen3-asr-0.6b`（`Qwen/Qwen3-ASR-0.6B`，`5eb144179a02acc5e5ba31e748d22b0cf3e303b0`）；其强制对齐模型为 `Qwen/Qwen3-ForcedAligner-0.6B`（`c7cbfc2048c462b0d63a45797104fc9db3ad62b7`）；语言识别模型为 `speechbrain/lang-id-voxlingua107-ecapa`（`0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9`）。
- 数据：LibriVox 的中文《呐喊》（朗读者 Jing Li）和英文 *The Great Gatsby*（朗读者 Kara Shallenberg），均为 Public Domain Mark 1.0。输入统一转换为 16 kHz、单声道 PCM WAV；使用生产 Silero VAD 为嵌套时长前缀选择安全截点。
- 执行方式：每个 Provider 先用最短中文样本预热一次；每次正式运行均使用独立子进程和结果目录，不复用结果、workspace 或模型进程。`faster-whisper` 的原生路径显式使用与切片路径相同的 CPU 线程预算。Qwen3-ASR 0.0.6 在时间戳模式下仍可能按最长 180 秒在 Provider 内部切分。

## 指标与比较口径

- 表中时间、RTF 与 Provider stage 均为同一组合 3 次成功运行的中位数。RTF 为 wall time 除以音频时长，越低越快。
- “切片相对速度”是 `provider-native` 中位 wall time 除以 `project-slicing` 中位 wall time；大于 1 表示切片路径更快。
- 输出差异按相同 repetition 配对。中文经 NFKC、OpenCC `t2s` 后忽略 Unicode 空白和标点，计算相对 native 输出的 CER；英文经 NFKC 与 casefold 后按 Unicode 单词计算相对 native 输出的 WER。标点数量单独统计。
- 输出差异仅说明两种路径的转写文本差异，不是相对于人工标注的准确率指标。

## 结果

### faster-whisper

| 语言 | 时长 | 模式 | 中位 wall time | 中位 RTF | Provider stage | 切片相对速度 | 输出差异 | 标点数（切片/原生） |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 中文 | 8 分钟 | project-slicing | 96.676s | 0.1980 | 93.240s | 1.980x | CER 3.523% | 132/6 |
| 中文 | 8 分钟 | provider-native | 191.418s | 0.3920 | 188.527s | — | — | — |
| 中文 | 16 分钟 | project-slicing | 149.668s | 0.1554 | 145.508s | 2.492x | CER 4.690% | 333/6 |
| 中文 | 16 分钟 | provider-native | 372.982s | 0.3874 | 370.024s | — | — | — |
| 中文 | 32 分钟 | project-slicing | 260.276s | 0.1355 | 254.337s | 2.730x | CER 4.115% | 852/6 |
| 中文 | 32 分钟 | provider-native | 710.554s | 0.3700 | 707.452s | — | — | — |
| 中文 | 64 分钟 | project-slicing | 549.076s | 0.1424 | 539.897s | 2.550x | CER 5.342% | 1680/7 |
| 中文 | 64 分钟 | provider-native | 1399.901s | 0.3631 | 1396.446s | — | — | — |
| 英文 | 8 分钟 | project-slicing | 61.636s | 0.1284 | 57.843s | 2.017x | WER 0.180% | 152/141 |
| 英文 | 8 分钟 | provider-native | 124.298s | 0.2590 | 121.623s | — | — | — |
| 英文 | 16 分钟 | project-slicing | 100.191s | 0.1044 | 94.930s | 2.496x | WER 0.806% | 323/310 |
| 英文 | 16 分钟 | provider-native | 250.124s | 0.2605 | 247.284s | — | — | — |
| 英文 | 32 分钟 | project-slicing | 205.748s | 0.1069 | 196.042s | 2.518x | WER 0.902% | 956/776 |
| 英文 | 32 分钟 | provider-native | 518.094s | 0.2691 | 514.927s | — | — | — |
| 英文 | 64 分钟 | project-slicing | 429.890s | 0.1119 | 405.215s | 2.514x | WER 0.673% | 2079/1787 |
| 英文 | 64 分钟 | provider-native | 1080.925s | 0.2815 | 1077.516s | — | — | — |

### qwen3-asr

| 语言 | 时长 | 模式 | 中位 wall time | 中位 RTF | Provider stage | 切片相对速度 | 输出差异 | 标点数（切片/原生） |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 中文 | 8 分钟 | project-slicing | 31.039s | 0.0636 | 27.314s | 1.236x | CER 5.340% | 179/173 |
| 中文 | 8 分钟 | provider-native | 38.369s | 0.0786 | 35.328s | — | — | — |
| 中文 | 16 分钟 | project-slicing | 52.061s | 0.0541 | 47.441s | 1.304x | CER 6.704% | 388/372 |
| 中文 | 16 分钟 | provider-native | 67.912s | 0.0705 | 64.663s | — | — | — |
| 中文 | 32 分钟 | project-slicing | 102.646s | 0.0534 | 96.133s | 1.093x | CER 5.815% | 876/842 |
| 中文 | 32 分钟 | provider-native | 112.220s | 0.0584 | 108.580s | — | — | — |
| 中文 | 64 分钟 | project-slicing | 203.634s | 0.0528 | 193.376s | 1.072x | CER 5.705% | 1867/1830 |
| 中文 | 64 分钟 | provider-native | 218.281s | 0.0566 | 214.323s | — | — | — |
| 英文 | 8 分钟 | project-slicing | 30.005s | 0.0625 | 25.861s | 1.249x | WER 4.899% | 158/155 |
| 英文 | 8 分钟 | provider-native | 37.480s | 0.0781 | 34.548s | — | — | — |
| 英文 | 16 分钟 | project-slicing | 50.129s | 0.0522 | 44.577s | 1.338x | WER 4.852% | 311/311 |
| 英文 | 16 分钟 | provider-native | 67.051s | 0.0698 | 63.802s | — | — | — |
| 英文 | 32 分钟 | project-slicing | 107.395s | 0.0558 | 96.993s | 1.015x | WER 3.490% | 853/855 |
| 英文 | 32 分钟 | provider-native | 109.024s | 0.0566 | 105.554s | — | — | — |
| 英文 | 64 分钟 | project-slicing | 226.330s | 0.0589 | 199.868s | 1.391x | WER 4.124% | 1834/1823 |
| 英文 | 64 分钟 | provider-native | 314.834s | 0.0820 | 310.583s | — | — | — |

## 观察与限制

- 在本次的全部 16 个 Provider、语言和时长组合中，`project-slicing` 的中位 wall time 均低于 `provider-native`。
- 对 `faster-whisper`，切片路径相对速度为 1.980x–2.730x；对 `qwen3-asr`，为 1.015x–1.391x。
- 这些结果只适用于上述硬件、代码提交、模型修订、数据和运行参数；不应外推为不同设备、模型版本或真实业务音频上的通用性能/准确率结论。
