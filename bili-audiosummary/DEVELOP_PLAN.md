# Bilibili Audio Summary Development Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复本地测试暴露的稳定性、长视频、摘要输出和验证体验问题，让该 skill 在受限 Windows 环境、长视频和多语言总结场景下更可靠。

**Architecture:** 先处理低风险高收益的缓存路径、摘要校验和语言命名问题，再增强日志与 prompt 结构，最后单独推进长视频分段与断点续跑。改动保持外科式：优先扩展现有 `scripts/` 入口、`tests/` 单测和 `SKILL.md` 文档，不重构无关代码。

**Tech Stack:** Python 3.12, Windows BAT, yt-dlp, faster-whisper, optional Qwen3-ASR, pytest.

---

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

**Problem:** faster-whisper / huggingface_hub 在受限环境下可能尝试写入 `C:\Users\ApprenTice\.cache\huggingface`，导致权限错误。

**主要改动:** setup 默认使用项目内 `.cache/huggingface`，保留用户显式配置，并在模型下载失败时提示本地 cache 路径和重跑命令。

---

### Task 2: Add Summary Validator（已完成）

**Problem:** 最终 summary 校验依赖人工检查，容易漏掉占位符、模板注释或全文复制。

**主要改动:** 新增 `scripts/validate_summary.py`，集中检查 summary 文件、占位符、模板注释和全文复制风险；`SKILL.md` 将该脚本作为最终验证步骤。

---

### Task 3: Separate Transcript Language From Summary Language

**Problem:** 当前 summary 文件名跟随 transcript language，英文转写但中文总结时可能生成 `summary_en.md`，误导用户。

> 需要进一步阅读代码调查目前的行为，该任务暂缓

**Success Criteria:**

- pipeline 支持 `--summary-language zh|en`。
- transcript language 继续控制字幕 / ASR 语言。
- summary template 和输出文件名由 summary language 控制。
- 默认行为在文档中明确。

### Task 4: 规范化脚本输出，避免上下文污染

**Problem** 目前的脚本运行时会输出大量信息污染上下文并且浪费 token，应该只输出简洁的状态提示，详细信息以及报错写入log文件

#### Task4.1 拆分依赖安装脚本（已完成）

**Problem** 目前的依赖安装由一个ps1脚本实现，当调用依赖安装工具时会产生大量输出污染上下文

**主要改动:** 将 Windows setup 公共入口改为 `.\scripts\setup\setup_windows.bat`，依赖同步交给 uv，Python setup 逻辑拆分到 `scripts/setup/`，终端只输出关键步骤，完整日志写入 `.cache/logs/`。

#### Task4.2 规范化处理脚本输出（已完成）

**Problem** 目前的处理脚本 run_pipeline 会产生以下阶段性输出，并且没有log功能

修改目标：

- 为 run_pipeline 涉及的流程文件增加 log 功能，可以考虑复用当前setup下的process_logging，将其提到外面一级文件夹来作文通用log入口
- 除了标注保留的内容，其余内容写入log但是不打印到stdout
- 可暂不实现 ASR 百分比；后续并行 ASR 任务已补充 plan、chunk、缓存和断点进度

**主要改动:** 新增通用 `scripts/process_logging.py`，让 setup、pipeline、fetch、字幕转换和 ASR 共用日志；终端只输出关键阶段和最终路径，完整运行信息、traceback、yt-dlp 细节、缓存状态、fallback 和 warning 写入日志文件。进一步优化 setup 分层：`setup_windows.bat` 只确保 uv 和 Python 3.12 可用，Python setup 负责调用 uv 同步基础依赖；模型下载从基础 setup 拆出到 `scripts/setup/install_model.py`，支持 `--model faster-whisper|qwen3`，使用前至少安装一种本地 ASR 模型。

#### Task4.3 补充对 uv 路径的规范化输出和默认源设置（已完成）

**Problem** 全量切换到 uv 以后，没有做默认依赖源优化和 uv 安装输出规范化

**修改目标:** 为 uv 路径设置默认国内依赖源，并在 setup 入口打印规范化的 uv sync 命令头，保留 uv 原生安装、完成和重复安装输出。

**主要改动:** `pyproject.toml` 将清华 PyPI 配置为 uv 默认源并保留 PyTorch CUDA 显式源；`setup_windows.bat` 在用户未显式设置时提供 `UV_DEFAULT_INDEX`，继续使用项目内 `UV_CACHE_DIR`，并在执行前打印 Python 3.12 准备命令；同步 README、错误处理和架构文档，补充 launcher 与 uv index 配置测试。

### Task 5: 避免提示词注入风险，不再将转写结果作为指令的一部分，明确它们是数据（已完成）

**Problem** 目前的转写结果会写入prompt文件中，有提示词注入的风险

修改目标：

- 不再拼接ASR结果到prompt中
- prompt改为使用"[]()"文件引用格式，在prompt中明确ASR结果文件的相对路径
- prompt只包含结果保存路径，指令，模板，ASR结果文件的相对路径，四者的编排顺序需要做考虑

**主要改动:** summary prompt 不再嵌入 transcript 正文，改为引用同目录 transcript；prompt 明确不可信数据边界，并固定任务、数据链接、instructions、template 和输出路径的顺序。

### Task 6: 尝试使用subagent做总结和文件写入，隔离不同的上下文（已完成）

**Problem** 目前的脚本调用和总结在同一个对话中由同一个agent完成，上下文连通可能会影响总结效果

修改目标：

- 在SKILL.md的步骤中做修改
- 总结方式改为优先使用subagent总结并写入文件
- 如果无法使用subagent，允许在同一个对话中完成总结并写入文件

**主要改动:** `SKILL.md` 要求优先使用不继承父对话的 subagent 完成总结和写入；无 subagent 能力时允许当前 Agent 按相同 prompt 执行，最终 validator 仍由主 Agent 运行。

### Task 7: 拆分和修改目前的文档（已完成）

**Problem** 目前的文档出现了杂糅现象，例如README中有过多架构相关信息，SKILL.md的操作步骤被解释分隔，并包含了过多正常使用不需要的信息

修改目标：

- README只包含skill介绍（包括功能亮点）、能力边界、使用方式、第三方依赖（yt-dlp，Qwen3，whisper，两种测试过的cookies导出方法等应该列出的）。
- 能力边界指明总结只根据ASR结果，目前只支持bili视频
- 使用方式写两种，作为skill安装，以及clone代码在代码目录直接运行
- SKILL.md只包含yaml头，使用场景，主要步骤，处理时间估算（待补充）
- 使用场景中需要增加一句对于两种模型的简略说明，并指出使用Qwen3可以获得更好的转写效果与转写效率
- 在reference中增加一个项目架构说明，说明各脚本的行为和项目文件目录的作用，因此README和SKILL中不需要再介绍，但是需要保留相对路径引用链接
- 将skill.md中的错误处理指导全部移到error-handing中去，考虑到错误情况可能较多，在错误指导文档开头需要增加一个简要目录供快速查阅

**主要改动:** 精简 `README.md` 和 `SKILL.md`，将架构说明集中到 `references/architecture.md`，将错误处理集中到 `references/error-handling.md` 并补充快速目录。

---

## P1 Tasks

### Task 1: Make Page And Multi-Part Handling Explicit

**Problem:** 分 P / 多语言场景下，管线可能只处理当前 P，但用户不一定知道。

> 该改动涉及范围较大，建议先完成其它任务

**Success Criteria:**
- metadata 输出当前处理页。
- 检测到多 P 时输出总页数和当前页。
- 文档明确当前默认只处理当前 URL 指向的 P。
- 后续 `--page` / `--all-pages` 可单独开发，不在本任务实现。

### Task 2: 细化 whisper 转写切分精度（已完成）

**Problem:** 目前Qwen3的ASR链路和字幕复用的切分效果较好，但是whisper的输出切分粒度太粗

**主要改动:** faster-whisper 默认改为非批处理转写，直接使用模型生成的较细 segments；transcript JSON/Markdown 仍只输出段级时间戳，并补充中文简体规范化依赖。

---

## P2 Tasks

### Task 1: Add Parallel Whisper Chunk Cache And Resume（已完成）

**Problem:** 长视频整段转写容易超时；失败后缺少断点续跑。

**主要改动:** faster-whisper 统一使用并行转写计划，按 CPU budget 和音频时长生成 macro/chunk；每个 chunk 原子写入独立结果，完整 plan 匹配时复用有效结果并恢复未完成任务；合并后的 transcript JSON/Markdown 保持现有结构。终端输出 plan、macro worker、缓存、续跑、成功、重试和合并信息，详细失败 traceback 写入日志。

---

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

### Task 4: Add Lightweight Quality Gates

**Problem:** 后续改动会涉及多个脚本和文档，需要最小化回归风险。

**Success Criteria:**
- 有一个本地质量检查命令。
- 运行单测覆盖 validator、pipeline、fetch、transcribe、skill spec。
- 不要求引入重型 CI，除非仓库外层已有统一 CI 约定。

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

- Default `--summary-language`: choose `zh` for Chinese user workflows, or inherit transcript language for backward compatibility.
- `--page` / `--all-pages`: report current P first; implement selection only if real usage confirms demand.
