# bili-audiosummary

该项目是一个 Agent Skill，遵循 [Agent Skills](https://agentskills.io/home) 开放标准。可根据 B 站字幕，或者音频转写结果生成视频内容总结。

已实现：输入 B 站视频 URL，解析 BVID，下载目标语言字幕和最低可用音频流；若存在符合目标语言且可用的 `.srt` 字幕，则直接转换为统一 transcript；否则默认使用 faster-whisper 执行 STT，在本机有可用 CUDA 时也可选用 Qwen3-ASR，并拼接 instructions、总结模板和转写文本生成 summary prompt。

## 目录结构

```text
bili-audiosummary/
├─ SKILL.md
├─ README.md
├─ requirements.txt
├─ .gitignore
├─ .venv/                         # Python 3.12 虚拟环境
├─ .cache/
│  ├─ uv/                          # setup 默认 uv 缓存
│  ├─ huggingface/                 # setup 默认 Hugging Face 下载缓存
│  └─ logs/                        # setup 日志及尚未识别 BVID 的处理日志
├─ models/
│  ├─ faster-whisper-small/         # 默认 STT 本地模型
│  ├─ qwen3-asr-0.6b/               # 可选 Qwen3 ASR 本地模型
│  └─ qwen3-forcedaligner-0.6b/     # 可选 Qwen3 对齐模型
├─ scripts/
│  ├─ process_logging.py          # setup 与处理脚本共用的 logging 配置
│  ├─ setup/
│  │  ├─ setup_windows.bat
│  │  ├─ setup.py
│  │  ├─ environment.py
│  │  ├─ install_core.py
│  │  ├─ download_models.py
│  │  └─ install_qwen3.py
│  ├─ run_pipeline.py             # 总入口：抓字幕/音频，优先用字幕，必要时执行 STT，生成 summary prompt
│  ├─ fetch_audio.py              # 解析 URL，下载目标语言字幕和音频
│  ├─ transcribe.py               # 默认使用 faster-whisper，可选优先尝试 Qwen3-ASR 生成转写结果
│  ├─ config.py
│  └─ utils.py
├─ references/
│  └─ error-handling.md           # Skill 执行过程中的错误处理参考
├─ assets/
│  ├─ summary_instructions.md     # 总结生成规则
│  ├─ summary_template_en.md      # 英文总结输出模板
│  └─ summary_template_zh.md      # 中文总结输出模板
└─ results/
   └─ <BVID>/
      ├─ resource/
      │  ├─ <BVID>.<audio-ext>
      │  ├─ fetch_manifest.json
      │  ├─ metadata.json
      │  ├─ metadata.raw.json
      │  └─ subtitle/
      │     └─ <BVID>.<lang>.srt
      ├─ <BVID>_transcript.json
      ├─ <BVID>_transcript.md
      ├─ pipeline-<timestamp>.log
      └─ <BVID>_summary_prompt.md
```

## 当前流程

```text
URL
  -> yt-dlp 解析元信息和 BVID
  -> 将后续下载入口规范化为 https://www.bilibili.com/video/<BVID>/
  -> results/<BVID>/ 创建结果目录
  -> 优先复用符合目标语言且可正常解析的 .srt 本地字幕缓存；无匹配缓存或缓存损坏时尽力下载目标语言字幕
  -> 优先复用已下载音频；无缓存时尽力下载最低可用音频流
  -> 字幕可用时直接生成 transcript；无可用字幕或字幕无效时执行 ASR (default faster-whisper; optional Qwen3-ASR on CUDA)
  -> 若字幕和音频都不可用，则报错退出
  -> 输出 transcript.json 和 transcript.md
  -> 拼接 summary_instructions、summary_template 和 transcript
  -> 输出 <BVID>_summary_prompt.md
```

## 环境配置

Windows 默认使用：

```powershell
.\scripts\setup\setup_windows.bat
```

脚本会：

- 优先使用 `uv` 启动 Python 3.12；没有 `uv` 时回退到 `py -3.12`
- 使用 Python 3.12 创建 `.venv/`；已有 `.venv/` 不是 Python 3.12 时停止，不自动删除
- 通过 `.venv` Python 单次执行 `pip install -r requirements.txt --disable-pip-version-check --progress-bar off`
- 按 `requirements.txt` 的精确版本、版本范围或无版本声明校验安装结果
- 只使用 `ffmpeg-binaries-compat` 提供的 `ffmpeg/ffprobe`，不读取系统 PATH
- 下载默认模型 `Systran/faster-whisper-small` 到 `models/faster-whisper-small/`
- 未显式设置 `UV_CACHE_DIR` 时，默认将 uv 缓存放到 `.cache/uv/`
- 未显式设置 `HF_HOME` 时，默认将 Hugging Face 下载缓存放到 `.cache/huggingface/`
- 将完整 stdout/stderr 写入 `.cache/logs/setup-<timestamp>.log`，终端只显示简短进度和失败命令输出

推荐安装 [`uv`](https://docs.astral.sh/uv/) 以获得更稳定使用体验。


默认 setup 只准备 faster-whisper。Qwen3-ASR 是有可用 CUDA 时的可选项，需要显式执行：

```powershell
.\.venv\Scripts\python.exe scripts\setup\install_qwen3.py
```

该命令一次完成 Qwen3 可选依赖和两个本地模型的准备。`torch` 与 `torchaudio` 单独使用 PyTorch 官方 CUDA wheel 源，其余依赖继续支持 `PIP_INDEX_URL`，模型下载继续支持 `HF_ENDPOINT`。

setup 脚本会默认使用 [PyPI 清华源](https://pypi.tuna.tsinghua.edu.cn/simple)与 [Hugging Face 镜像站](https://hf-mirror.com)。
如果 pip 通过配置镜像安装依赖失败，脚本会自动重试官方 PyPI 源 `https://pypi.org/simple`。实际使用中，系统代理可能导致镜像源的 simple 索引解析异常，表现为常见包提示 `from versions: none`；遇到这类情况可以先关闭代理重试，或直接使用官方源。

需要使用原生源可在运行前设置：

```powershell
$env:PIP_INDEX_URL="https://pypi.org/simple/"
$env:HF_ENDPOINT="https://huggingface.co/"
```

`ffmpeg-binaries-compat` 是唯一 ffmpeg 来源。缺失时运行时会停止并要求重新执行 setup。

## 使用方式

如果遇到 B 站返回 `HTTP 412` 或其他需要登录态的情况，可先准备 `cookies.txt`。

推荐方式：

- Chrome：安装 `Get cookies.txt LOCALLY` 扩展，然后在已登录 B 站的情况下导出，文件会直接保存到下载目录。
  扩展地址：<https://chromewebstore.google.com/detail/get-cookiestxt-locally/>
- Edge：安装 `Cookie-Editor` 扩展，在已登录 B 站的情况下选择导出格式为 `Netscape`，扩展会将内容复制到剪贴板。随后新建一个 `cookies.txt` 文件，将内容粘贴并保存。
  扩展地址：<https://microsoftedge.microsoft.com/addons/detail/cookieeditor/>

将导出的 cookie 文件复制到 SKILL 根目录。若文件名是 `cookies.txt`、`www.bilibili.com_cookies.txt` 或 `bilibili_cookies.txt`，脚本会自动检测并使用；也可以在命令中显式指定：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>" --cookies .\cookies.txt
```

总入口：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

正常运行时，终端只显示抓取、字幕转换或 ASR、构建 prompt 等关键阶段，以及最终 `Result`、`Summary Prompt` 和 `Final Summary Path`。BVID、manifest、metadata、transcript 路径、缓存命中、segments、详细 fallback 原因和 yt-dlp warning 写入完整日志。Qwen3 不可用并回退到 faster-whisper 时，终端会额外显示一条简短 warning。

pipeline 日志启动时写入 `.cache/logs/pipeline-<timestamp>.log`。识别 BVID 后，日志迁移到 `results/<BVID>/pipeline-<timestamp>.log`；若元信息提取失败，日志保留在 `.cache/logs/`。失败时终端会回放 traceback 并显示日志路径。

跳过字幕复用/下载并强制使用 ASR：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "https://www.bilibili.com/video/BV12kXmBCEDi/" --skip-subtitles
```

抓取元信息、字幕和音频：

```powershell
.\.venv\Scripts\python.exe scripts\fetch_audio.py "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

同一 `BVID` 重跑时：

- `fetch_audio.py` 会优先复用符合当前请求语言且可正常解析的 `.srt` 字幕缓存；缓存损坏时会尝试重新拉取字幕
- 已下载音频会直接复用
- 字幕或音频下载失败时将 warning 写入日志，不会立即中止流程
- `run_pipeline.py --skip-subtitles` 会跳过字幕复用和下载，直接使用音频 ASR
- `run_pipeline.py` 只有在既没有可用字幕、也没有可用音频可供 STT 回退时才会报错退出

仅执行 STT：

```powershell
.\.venv\Scripts\python.exe scripts\transcribe.py results/BV12kXmBCEDi/resource/fetch_manifest.json
```

在本机有可用 CUDA，且本地 Qwen3 模型已下载完成时，可选优先尝试 Qwen3-ASR。若 Qwen3-ASR 不可用或运行失败，代码会回退到 faster-whisper：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "https://www.bilibili.com/video/BV12kXmBCEDi/" --asr-provider qwen3
```

处理英文内容时，可显式指定目标语言：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "https://www.bilibili.com/video/BV12kXmBCEDi/" --language en
```

Qwen3 路径实际运行时内部固定使用：

- `Qwen/Qwen3-ASR-0.6B`
- `Qwen/Qwen3-ForcedAligner-0.6B`
- `device_map="cuda:0"`
- `dtype="bfloat16"`

Qwen3 的安装策略：

- 默认依赖继续走当前 `PIP_INDEX_URL`，例如清华源
- `torch` 和 `torchaudio` 单独从 PyTorch 官方 CUDA wheel 源安装
- Qwen3 模型和 aligner 仍通过 `huggingface_hub` 下载，继续受 `HF_ENDPOINT` 控制
- 使用 `--asr-provider qwen3` 前，需要先执行 Qwen3 setup 命令
- `--asr-provider qwen3` 表示优先尝试 Qwen3-ASR，不是严格只允许 Qwen3；可查看 transcript JSON 的 `source` 字段确认实际使用的是 `qwen3-asr` 还是 `faster-whisper`

## 依赖组件

- 视频元信息、字幕和音频下载：`yt-dlp`
- 音频处理：`ffmpeg-binaries-compat`
- 默认语音转写：`faster-whisper`
- faster-whisper 默认使用非批处理转写，以获得更细的段落时间戳；相较批处理模式会增加单进程转写耗时。
- 中文 faster-whisper 转写会使用简体中文提示，并通过 OpenCC 将输出规范化为简体中文。
- 可选 CUDA 语音转写：`qwen-asr` + `torch` + `torchaudio` + `transformers` + `accelerate` + `huggingface_hub` + `numpy` + `soundfile` + `librosa`

## 能力边界

- 该 Skill 只生成 summary prompt；最终 summary 由调用该 Skill 的 Agent 根据 prompt 写入。
- 当前不分析视频画面；transcript 优先来自目标语言字幕，字幕不可用时才回退到音频 STT。
- 只支持 bilibili 平台的视频 URL。
- Qwen3-ASR 仅建议在本机有可用 CUDA，且本地模型已准备完成时启用；无 CUDA 或未准备模型时继续使用默认 faster-whisper。
