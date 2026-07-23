# bili-audiosummary 开发说明

## 项目定位

`bili-audiosummary` 是面向 Bilibili 视频的音频总结 Agent Skill。项目运行在 Windows 和 Python 3.12 环境中，统一使用 `uv` 管理解释器与依赖。

主要入口与文档职责如下：

- `SKILL.md`：Agent 使用流程与能力边界。
- `README.md`：面向使用者的安装、运行和配置说明。
- `scripts/run_pipeline.py`：完整处理流程入口。
- `scripts/fetch_audio.py`：视频信息、字幕与音频资源获取。
- `scripts/transcribe.py`、`scripts/asr/`：转写入口与 ASR 相关实现。
- `scripts/validate_summary.py`：最终总结校验入口。
- `assets/`：总结模板与提示词资源。
- `references/architecture.md`：模块职责和数据流说明。
- `references/error-handling.md`：故障定位与处理说明。
- `tests/`：与各脚本和公开行为对应的测试。

## 开发边界

- 先读取当前实现、测试和相关文档，再判断现有契约，以实现为准，不要仅凭旧说明、旧记忆及旧文档推断行为。
- 修改用户可见行为时，同步检查 `README.md`、`SKILL.md` 和 `references/` 中与该行为直接相关的内容
- 对于实验性改动，例如效率实验，正确性实验，不要同步到文档中
- `.cache/`、`.venv/`、`models/`、`results/`、`tmp/`、cookie 文件、音频和本地模型均为本地或生成内容，不应纳入提交。
- 保持 Windows PowerShell 和 Python 3.12 兼容；仓库脚本应通过 `uv run python -m scripts.<module>` 形式运行。

## 第三方 ASR 返回契约

以下 JSON 仅用于表示上游返回值的字段结构；Provider 实际返回 Python 对象，不是项目写入的 JSON artifact。依赖升级后应重新核对这些契约。

Qwen3 返回顶层语言、完整带标点文本，以及可选的时间戳项：

```json
{
  "language": "zh",
  "text": "这是完整的转写文本。",
  "time_stamps": {
    "items": [
      {
        "text": "这",
        "start_time": 0.0,
        "end_time": 0.2
      }
    ]
  }
}
```

上游时间戳项通常会清除常规标点，仅保留字母、数字和 ASCII 单引号；中文通常为字级，英文通常按空格分项。

faster-whisper 的转写结果包含多个分段；调用还会返回 `TranscriptionInfo`：

```json
{
  "segments": [
    {
      "start": 0.0,
      "end": 1.5,
      "text": " This is a sentence.",
      "words": [
        {
          "word": " This",
          "start": 0.0,
          "end": 0.4,
          "probability": 0.98
        }
      ]
    }
  ]
}
```

faster-whisper 没有顶层完整文本，需要拼接 `Segment.text`。`Word.word` 默认依 tokenizer 将标点并入前词或后词，双引号和单引号的方向取决于位置；中文粒度不代表语义分词。

简要对照：Qwen3 是“完整带标点文本 + 通常无常规标点的时间戳项”，faster-whisper 是“多个带标点分段 + 通常携带相邻标点的词项”。

## 环境与常用命令

面向使用者的核心环境安装：

```powershell
.\scripts\setup\setup_windows.bat
```

开发环境同步：

```powershell
uv sync --python 3.12
```

提交前运行代码检查：

```powershell
uv run ruff check
uv run pyright
uv run ruff format --check .
```

运行完整测试：

```powershell
uv run pytest
```

优先针对改动范围运行聚焦测试，例如：

```powershell
uv run pytest tests/test_run_pipeline.py
uv run pytest tests/test_transcribe.py
```

`tests/test_live_pipeline.py` 会访问真实 Bilibili 资源并依赖本地运行条件。除非任务明确需要端到端验证，否则使用常规单元测试和模拟边界完成验证。

## 测试与验证

- 修改 pipeline、资源获取、转写、总结校验或 setup 时，运行对应的 `tests/test_*.py`。
- 变更跨越多个模块或公共契约时，在聚焦测试通过后运行完整 `uv run pytest`。
- 测试临时文件使用 `tests/conftest.py` 提供的 `workspace_tmp_path`；不要重新引入固定的系统临时目录或仓库外测试目录。
- 文档改动至少检查命令、路径和模块名是否与当前仓库一致。
- 交付前运行 `git diff --check`，并确认没有夹带生成文件、凭据、模型、音频或无关改动。

## 提交信息

编写提交信息时，遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) 规范。
