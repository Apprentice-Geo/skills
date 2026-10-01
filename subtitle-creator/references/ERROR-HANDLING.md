# 错误处理

## 日志与终端输出

每次 setup、dependency checker 和 workflow CLI 调用使用独立日志。Python 日志会话把同一条业务记录同时写入文件和指定终端：状态与结果写入 stdout，显式 warning 和 error 写入 stderr，调试明细、第三方 logger、Python warnings 及完整 traceback 仅写入文件。不得记录 transcript 正文、用户提供的源文本或上游模型对象。

以下输出不属于 Python 日志会话：argparse 的 `--help` 和解析器自身输出、`.bat` 的 `where uv` 与路径切换检查，以及 `uv python install 3.12` 的提示、原始输出和失败输出。若 Python bootstrap 尚未启动，setup 失败时可以没有 setup 日志，也不得声称存在 `Full log`。

dependency checker 将 JSON 原子发布到 `.cache/logs/`，并从同一份 report 生成日志记录。PASS 明细只进入文件，聚合结果和报告路径进入 stdout，WARN 与 FAIL 进入 stderr；退出码保持 `0`、`1`、`2` 三档语义。

## Workflow 日志位置

`create_subtitle`、`attach_transcription` 和 `finalize_subtitle` 先在 `.cache/logs/` 创建日志。只有在命令成功获得并验证可信 job 路径后，才把活动日志移动到对应的 `results/<audio-id>/`；最终结果记录在移动后写入。移动失败时继续使用 cache 中的原日志，并在日志内记录 traceback，不改变命令结果。

`remove_subtitle_job` 的日志始终保留在 `.cache/logs/`，避免日志随 job 目录删除或产生打开句柄冲突。任何 workflow 在获得可信 job 路径前失败时，日志也留在 cache。

成功时 stdout 只包含原有的一行结果：

```text
subtitle_job: <absolute-path>
normalized_transcript: <absolute-path>
subtitle: <absolute-path>
removed_subtitle_job: <absolute-path>
subtitle_job_absent: <absolute-path>
```

失败时 stdout 为空，stderr 只包含简洁错误和 `Full log` 路径。根据日志修复输入或环境后，从上一个成功状态重试；不得根据未发布的临时文件推断成功。


## 合同包检查

检查器和 setup 复用本 Skill 的 `scripts/contract_check.py`：从 `pyproject.toml` 读取合同包要求，通过当前解释器的 `importlib.metadata` 读取 distribution 版本，检查 `load_result`、`TranscriptionResult`、`ResultValidationError` 和 `PUBLIC_SCHEMA_VERSION=3`。版本不符、包缺失、声明不可读或无效、API 不完整时返回失败，不能报告 ready。JSON 检查项包含 expected、actual 和同步建议。

`uv pip check` 仅检查已安装包之间的一致性，不证明它们满足当前项目声明。按本 Skill 环境策略显式同步后，使用 `uv run --no-sync python -m scripts.check_dependencies` 复查，避免自动同步掩盖旧包问题。实际接入转写时仍通过 `load_result()` 验证完整公共 bundle；能力检查不能代替该验证。
