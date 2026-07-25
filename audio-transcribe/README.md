# audio-transcribe

`audio-transcribe` 接受本地音频文件，使用 faster-whisper 或 Qwen3 生成可复用的转写 artifact。

## 环境准备

项目使用 Python 3.12 和 `uv`：

```powershell
.\scripts\setup\setup_windows.bat
```

首次使用前安装至少一个转写模型。安装命令同时准备固定 revision 的语言识别模型：

```powershell
uv run --no-sync python -m scripts.setup.install_model --model faster-whisper
```

Qwen3 需要可用的 CUDA 环境及可选依赖：

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

## 转写本地音频

自动检测语言并选择已经就绪的模型：

```powershell
uv run --no-sync python -m scripts.transcribe ".\audio.m4a"
```

显式指定语言或模型：

```powershell
uv run --no-sync python -m scripts.transcribe ".\audio.m4a" --language zh --model faster-whisper
```

Whisper CPU 参数：

```powershell
uv run --no-sync python -m scripts.transcribe ".\audio.m4a" `
  --language zh `
  --model faster-whisper `
  --num-workers 4 `
  --cpu-threads 3
```

命令成功后会突出打印 `result_manifest.json` 的绝对路径。完整结果位于：

```text
results/<audio-id>/<provider>-<language>-<64位variant-id>/
```

同一音频改名或移动后仍使用相同 `audio_id`；语言、模型、固定模型 revision、执行策略、VAD、规划或断句配置变化时会使用不同 `variant_id`。

## 公开 artifact

- `result_manifest.json`：唯一公开入口，包含完整请求、音频身份、相对 artifact 路径和 SHA-256。
- `transcript.json`：有序短句及时间范围；文本保持 Provider 原文，不执行简繁转换。
- `raw_timestamps.json`：标准化的 `text/start/end/probability` alignment items。
- `transcribe.log`：首次成功调用日志。

`workspace/` 是内部缓存与恢复目录，不是其他 Skill 的公开读取接口。complete manifest 发布后保持不可变；公开 artifact 缺失或损坏时，命令只会在 workspace 能够确定性重建出相同 digest 时恢复成功入口。

## 维护参考

- [Architecture](references/architecture.md)：当前转写流程、result identity、cache 和公开 artifact 合同。
- [Error Handling](references/error-handling.md)：常见 setup、模型、推理、alignment、cache 和 artifact 失败处理。

## 验证

```powershell
uv run pytest
uv run ruff check
uv run pyright
uv run ruff format --check .
```
