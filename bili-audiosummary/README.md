# bili-audiosummary

`bili-audiosummary` 是一个遵循 [Agent Skills](https://agentskills.io/home) 开放标准的 Agent Skill。它接收 Bilibili 视频 URL，优先使用可用字幕，在字幕不可用时通过 ASR 转写音频，并生成供 Agent 撰写最终总结的 summary prompt。

## 功能亮点

- 字幕优先：优先复用或下载目标语言字幕，减少不必要的 ASR。
- ASR Provider：默认使用 faster-whisper；具备 CUDA 环境时可显式选择严格的 Qwen3-ASR 路径。
- 统一产物：生成带时间戳的 transcript、summary prompt 和处理日志。
- 缓存复用：重跑时复用有效字幕、音频、本地模型，以及与音频指纹、ASR/VAD 参数、worker 配置和切片布局完整匹配的 faster-whisper 计划与 chunk 转写结果。
- 简洁终端输出：终端显示关键阶段、并行转写计划、chunk 进度与结果路径，完整细节写入日志。

## 能力边界

- 总结仅依据字幕或 ASR 生成的 transcript，不分析视频画面。
- 不适合主要信息来自画面、图表、动作、屏幕文字或视觉演示的视频。
- 目前仅支持 Bilibili 视频 URL。
- pipeline 生成 summary prompt；最终 summary 由执行该 Skill 的 Agent 根据 prompt 写入。
- Qwen3-ASR 需要可用的 CUDA 环境、可选依赖和额外模型；显式选择后若准备或转写失败，本次 ASR 直接失败，不会回退到 faster-whisper。

## 使用方式

### 作为 Agent Skill 安装

按照所用 Agent 客户端的 Skill 安装说明，将本仓库中的 `bili-audiosummary` 目录完整安装、复制或链接到该客户端的 Skill 目录。安装时需保留 `SKILL.md`、`assets/`、`references/` 和 `scripts/` 等全部项目文件。

安装后，向 Agent 提供 Bilibili 视频 URL，并提出总结、笔记、要点提炼或时间戳整理等请求。Agent 会根据 [SKILL.md](SKILL.md) 执行流程。

### Clone 后直接运行

在 Windows PowerShell 中进入项目目录，先完成默认环境配置：

```powershell
.\scripts\setup\setup_windows.bat
```

默认 setup 会准备 Python 3.12 虚拟环境、核心依赖和 `ffmpeg-binaries-compat`。uv 默认使用清华 PyPI 源；如需覆盖，可在运行前设置 `UV_DEFAULT_INDEX`。

首次使用前，需要至少下载一种本地 ASR 模型。默认 CPU 路径推荐下载 faster-whisper：

```powershell
uv run --no-sync python -m scripts.setup.install_model --model faster-whisper
```

随后运行：

```powershell
uv run --no-sync python -m scripts.run_pipeline "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

处理英文内容：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --language en
```

选择最终总结语言时，使用独立的 `--summary-language`。例如，对英文转写生成中文总结：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --language en --summary-language zh
```

未传 `--summary-language` 时，pipeline 保持原有行为，按 transcript 的语言选择总结模板。

跳过字幕并强制使用 ASR：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --skip-subtitles
```

单独运行 faster-whisper 转写时，省略 `--num-workers` 和 `--cpu-threads` 会按音频长度、VAD 切点与 CPU 预算联合规划：

```powershell
uv run --no-sync python -m scripts.transcribe --audio "<audio-path>" --output-dir "<result-dir>" --asr-provider whisper
```

也可以显式覆盖 worker 数和每个 worker 的 CPU 线程数：

```powershell
uv run --no-sync python -m scripts.transcribe --audio "<audio-path>" --output-dir "<result-dir>" --asr-provider whisper --num-workers 1 --cpu-threads 1
```

CPU 线程预算为 `B = max(1, floor(cpu_count * 0.75))`。自动模式只考虑不超过必要块数、能整除 `B` 且存在合法切片方案的 worker 数，并选择最大的可行值。只指定 `--num-workers` 时，每个 worker 使用 `floor(B / W)` 个线程；只指定 `--cpu-threads` 时，在预算内选择最大的可行 worker 数；同时指定时保留两个显式值，但要求乘积不超过 `B`。显式值不会被静默降低，非正数、预算超限或无法生成合法切片计划都会在写入切片和加载模型前直接失败。

两种 ASR 路径都把源音频一次解码为 16kHz 单声道 float32 PCM；规划 VAD、边界规划和模型推理共享这份内存样本，不持久化 normalized PCM。faster-whisper 路径复用固定版本 `faster-whisper==1.2.1` 内置的 ONNX Silero VAD，不需要额外安装 `silero-vad`、PyTorch 或 torchaudio。规划 VAD 固定使用 `threshold=0.35`、`neg_threshold=0.25`、最短语音 `0ms`、最短静音 `300ms`、不限制最长语音、`speech_pad_ms=0` 和 `sampling_rate=16000`。VAD 区间、切点和 chunk 身份统一使用整数样本坐标；只有严格落在语音区间内部的边界才记为硬切。

正常切片连续覆盖完整音频、互不重叠，时长为 `30s-180s`；完整音频不足 30 秒时只允许单 worker、单切片例外。切片数 `N` 必须是 worker 数 `W` 的整数倍，批次数严格为 `N / W`。自动 worker 数不超过 `ceil(D / 180)`，并继续遵守 CPU 预算与等线程分配；显式 worker/thread 参数仍优先，但必须能产生合法切片。规划器对所有候选依次最小化硬切数、批次数、最大预计 VAD 语音负载、语音负载 MSRE 和边界顺序。chunk 内 faster-whisper 仍只接收 `vad_filter=True`，不接收这组外部规划 VAD 参数。

### faster-whisper 并行产物与缓存

Schema 6 使用平铺的样本坐标 chunk 布局，不生成 chunk WAV：

```text
asr_parallel/
├─ asr_plan.json
├─ progress.json
├─ metrics.json
├─ vad_result.json
├─ merged_transcript.json
└─ chunk_results/
   └─ chunk_<index>.json
```

只有音频指纹、ASR 参数、VAD 参数、规划参数、worker 配置和最终样本布局全部匹配时，Schema 6 计划与有效 chunk result 才会复用。Schema 5 及旧结果均不复用。完整缓存命中会在音频解码、VAD 和模型加载前返回；部分恢复只解码一次，并让同一个 `WhisperModel` 并发消费缺失 chunk 的 ndarray 视图。配置变化时会重建 plan 和 progress；同名 chunk 成功后，新结果通过原子替换覆盖旧文件。

`vad_result.json` 使用样本坐标 Schema 2，只按音频指纹与 VAD 参数校验，空语音区间也是合法结果。完整 plan 命中时直接跳过 VAD；旧秒坐标 VAD 缓存不会复用。

合并时，chunk 内时间戳直接加上该 chunk 的全局开始时间。segment 按 chunk index 和时间排序；只有时间范围真正相交时才合并，端点仅相接时保持分离。中文文本直接拼接，其他语言以一个空格连接，不执行字符级去重。`metrics.json` 记录 worker、chunk、batch、硬切数、逐 chunk 预计语音时长、最大预计语音负载、语音负载 MSRE、各切片耗时和最终 segment 数，不再记录软时长指标。

有可用 CUDA 时，可安装 Qwen3-ASR 的可选依赖和模型：

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

安装完成后严格使用 Qwen3-ASR：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --asr-provider qwen3
```

该选项只运行 Qwen3-ASR。依赖、模型、CUDA、模型加载、推理或对齐失败都会终止本次转写；如需使用 faster-whisper，请显式选择 `--asr-provider whisper` 或省略 provider 参数后重新运行。Qwen3 固定使用 `full` 数量策略：分片数存在 `QWEN3_MAX_INFERENCE_BATCH_SIZE` 的合法倍数时只比较这些倍数；不足一组时选择最多合法分片，25/60/100/119/120–180 秒分别产生 1/2/3/3/4 个分片。每批最多提交该常量数量的 ndarray slice，不向模型传 `language`，`max_new_tokens=1024`。

`results/<BVID>/asr_qwen3/` 使用 Schema 2 保存 plan、progress、逐 chunk result 和合并后的 `result.json`。完整缓存命中不解码、不检查 CUDA且不加载模型；部分恢复只解码一次、加载模型一次。batch 失败时，该批成员分别获得一次单 chunk 隔离尝试，成功项立即原子缓存，仍失败的 chunk 阻止合并并可在下次运行重新尝试。空文本和空时间戳是合法的 chunk 缓存内容；缓存只做身份、schema 和数据类型安全校验，不额外判断文本内容质量。

## ASR Benchmark

benchmark 会对 9 个固定 Bilibili 视频分别运行 faster-whisper 与 Qwen3-ASR，记录每次转写的总耗时、转写实时系数、转写进程树的采样峰值 RSS，以及 Qwen3 的 CUDA 峰值已分配/保留显存。计时包含模型加载、切片、推理、对齐和转写文件写入；音频下载、依赖安装和模型下载不计入结果。

| 视频 | 标注时长 |
| --- | --- |
| https://www.bilibili.com/video/BV1W694BEE7F/ | 00:01:03 |
| https://www.bilibili.com/video/BV1Nt4y1D7pW/ | 00:07:56 |
| https://www.bilibili.com/video/BV1MN4y177PB/ | 00:11:27 |
| https://www.bilibili.com/video/BV1ks411e7W4/ | 00:19:45 |
| https://www.bilibili.com/video/BV1Fa411c7Vh/ | 00:30:23 |
| https://www.bilibili.com/video/BV1rb4y1D7Gf/ | 00:39:51 |
| https://www.bilibili.com/video/BV1jJ411r7EL/ | 01:02:23 |
| https://www.bilibili.com/video/BV1e24y1D7qt/ | 01:47:01 |
| https://www.bilibili.com/video/BV1mL411z7Kf/ | 02:59:43 |

请先完成默认依赖、两种模型和 Qwen3 可选依赖的安装，再显式执行：

```powershell
uv run --no-sync python -m scripts.benchmark
```

默认会运行全部 9 个视频与两种模型，耗时很长。需要局部重测时可重复传入 `--video` 或 `--provider`：

```powershell
uv run --no-sync python -m scripts.benchmark --video BV1W694BEE7F --provider whisper
```

benchmark 会先下载或复用 `.cache/benchmark/audio/` 中的音频，再开始性能计时；结果写入 `results/benchmark/<timestamp>/benchmark.json` 和 `benchmark.md`。运行时仍自动识别项目根目录的 cookie 文件，不会把 cookie 路径或内容写入 benchmark 结果。

pipeline 会打印 `Summary Prompt` 和 `Final Summary Path`。根据 prompt 写入最终 summary 后，可执行：

```powershell
uv run --no-sync python -m scripts.validate_summary "<summary-path>"
```

完整数据流、目录职责、脚本说明和输出位置见 [项目架构](references/architecture.md)。命令失败时见 [错误处理](references/error-handling.md)。

## Cookies 导出

Bilibili 返回 `HTTP 412` 或请求需要登录态时，可准备 Netscape 格式的 cookie 文件。以下两种方法已经测试：

- Chrome：安装 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/)，登录 Bilibili 后导出，文件会保存到下载目录。
- Edge：安装 [Cookie-Editor](https://microsoftedge.microsoft.com/addons/detail/cookieeditor/)，登录 Bilibili 后选择 `Netscape` 格式导出，将剪贴板内容保存为 `cookies.txt`。

将文件放到项目根目录并命名为 `cookies.txt`、`www.bilibili.com_cookies.txt` 或 `bilibili_cookies.txt`，pipeline 会自动检测。也可以显式指定：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --cookies .\cookies.txt
```

## 第三方依赖

- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)：解析 Bilibili 元信息并下载字幕和音频。
- [`ffmpeg-binaries-compat`](https://pypi.org/project/ffmpeg-binaries-compat/)：提供项目使用的 `ffmpeg` 和 `ffprobe`；不依赖系统 PATH 中的 ffmpeg。
- [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper)：默认 ASR 引擎，可在 CPU 环境运行；其固定版本内置并行切分使用的 ONNX Silero VAD。
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)：可选 CUDA ASR 引擎，配合 `Qwen/Qwen3-ASR-0.6B` 和 `Qwen/Qwen3-ForcedAligner-0.6B` 本地模型使用。
- [`uv`](https://docs.astral.sh/uv/)：唯一支持的 Python 3.12 环境与依赖同步入口。
