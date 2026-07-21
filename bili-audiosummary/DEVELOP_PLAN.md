# Bilibili Audio Summary Development Plan

**Tech Stack:** Python 3.12, Windows BAT, yt-dlp, faster-whisper, optional Qwen3-ASR, pytest.

## Assumptions

- 当前目录是 `bili-audiosummary` skill 根目录。
- 本计划只整理后续开发任务，不要求一次性实现所有任务。
- 计划中的 P0 是下一轮开发应优先处理的问题；P1 是紧随其后的体验和正确性改进；P2 是排期型质量与文档完善。
- 当前代码已经部分解决 cookies 自动检测、Qwen3 可选 fallback、字幕优先 fallback 等问题；这些内容只在需要补强时列入任务。

## File Map

- Modify: `scripts/setup/`
  使用薄 BAT 启动 Python 3.12 setup 模块，设置项目内缓存、简化终端输出并保留完整日志。

- Modify: `scripts/run_pipeline.py`  
  增加 `--summary-language`、改进 summary prompt 顺序、输出更透明的阶段日志、报告分 P 信息。

- Modify: `scripts/runtime_options.py`  
  为 pipeline 增加 summary language 等选项字段。

- Modify: `scripts/transcribe.py`  
  已支持 faster-whisper 并行分段转写、chunk 结果缓存、断点续跑和明确的 ASR 过程日志。

- Modify: `scripts/fetch_audio.py`  
  补充分 P / 多 P metadata 报告，并把非致命警告与致命错误区分得更清楚。

- Create: `scripts/validate_summary.py`  
  校验最终 summary 文件是否存在、UTF-8 可读、无模板占位符、无模板注释、未复制全文。

- Modify/Create tests:
  - `tests/test_setup_windows.py` or PowerShell-focused setup test helper if adopted
  - `tests/test_run_pipeline.py`
  - `tests/test_transcribe.py`
  - `tests/test_fetch_audio.py`
  - `tests/test_validate_summary.py`
  - `tests/test_skill_spec.py`

- Modify: `SKILL.md`  
  精简执行步骤，补充 Windows sandbox 缓存路径、summary validator、长视频耗时说明和 Qwen3 推荐表达。

- Modify: `README.md` and `references/error-handling.md`  
  同步缓存、长视频、cookies、Qwen3、STT 质量限制和校验命令说明。

---

## P0 Tasks

### Task 1: Localize Hugging Face Cache（已完成）

### Task 2: Add Summary Validator（已完成）

### Task 3: Separate Transcript Language From Summary Language（已完成）

### Task 4: 规范化脚本输出，避免上下文污染（已完成）

#### Task4.1 拆分依赖安装脚本（已完成）

#### Task4.2 规范化处理脚本输出（已完成）

#### Task4.3 补充对 uv 路径的规范化输出和默认源设置（已完成）

### Task 5: 避免提示词注入风险，不再将转写结果作为指令的一部分，明确它们是数据（已完成）

### Task 6: 尝试使用subagent做总结和文件写入，隔离不同的上下文（已完成）

### Task 7: 拆分和修改目前的文档（已完成）

### Task 8: 修改并行切分策略，使用 Silero VAD 预先切分音频（已完成）

### Task 9: 增加 Qwen3-ASR 的中间结果落盘（已完成）

---

## P1 Tasks

### Task 1: Make Page And Multi-Part Handling Explicit（已完成）

### Task 2: 细化 whisper 转写切分精度（已完成）

## P2 Tasks

### Task 1: Add Parallel Whisper Chunk Cache And Resume（已完成）

### Task 2: Simplify Qwen3 Documentation And Runtime Messaging

**Problem:** Qwen3 说明容易让 Agent 误以为它是常规路径，但它实际是 CUDA 环境下的可选优化路径。

**Success Criteria:**
- `SKILL.md` 明确默认使用 faster-whisper。
- Qwen3 表述为 CUDA 用户可选优化。
- runtime 输出说明 `--asr-provider qwen3` 是“优先尝试并 fallback”，不是强制模式。

---

### Task 3: Document Typical Runtime And STT Quality Limits

**Problem:** 用户不知道短视频也可能需要数分钟；STT 错词风险没有足够突出。

**Success Criteria:**
- 文档列出短、中、长视频典型耗时范围。
- 总结要求标注转写可能存在错词。
- 超时说明强调“部分完成可重跑复用缓存”。

---

### Task 4: Add Lightweight Quality Gates（已完成）

## Verification Before Closing A Development Pass

Run:

```powershell
pytest tests -q
```

For live/manual validation when network and cookies are available:

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>"
uv run --no-sync python -m scripts.validate_summary "<summary-path>"
```

Expected:

```text
Pipeline completed.
Summary validation passed.
```

## Remaining Decisions

- `--summary-language` 默认继承 transcript 语言；调用方需要跨语言总结时显式传入 `zh` 或 `en`。（已确认）
- `--page` / `--all-pages`: report current P first; implement selection only if real usage confirms demand.

uv run --no-sync python -m scripts.benchmark_whisper_parallel --video BV1W694BEE7F --video BV1yt4y1Q7SS --video BV1Ls41127sG --video BV1MN4y177PB  --repetitions 3 --output-dir results/benchmark/whisper-parallel-clean
