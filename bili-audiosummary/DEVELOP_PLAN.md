# Bilibili Audio Summary Development Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复本地测试暴露的稳定性、长视频、摘要输出和验证体验问题，让该 skill 在受限 Windows 环境、长视频和多语言总结场景下更可靠。

**Architecture:** 先处理低风险高收益的缓存路径、摘要校验和语言命名问题，再增强日志与 prompt 结构，最后单独推进长视频分段与断点续跑。改动保持外科式：优先扩展现有 `scripts/` 入口、`tests/` 单测和 `SKILL.md` 文档，不重构无关代码。

**Tech Stack:** Python 3.12, PowerShell, yt-dlp, faster-whisper, optional Qwen3-ASR, pytest.

---

## Assumptions

- 当前目录是 skill 根目录：`D:\codes\skills\bili-audiosummary`。
- 本计划只整理后续开发任务，不要求一次性实现所有任务。
- 计划中的 P0 是下一轮开发应优先处理的问题；P1 是紧随其后的体验和正确性改进；P2 是排期型质量与文档完善。
- 当前代码已经部分解决 cookies 自动检测、Qwen3 可选 fallback、字幕优先 fallback 等问题；这些内容只在需要补强时列入任务。

## File Map

- Modify: `scripts/setup_windows.ps1`  
  设置 Hugging Face 相关缓存默认落到项目内可写目录，并在模型下载失败时给出明确提示。

- Modify: `scripts/run_pipeline.py`  
  增加 `--summary-language`、改进 summary prompt 顺序、输出更透明的阶段日志、报告分 P 信息。

- Modify: `scripts/runtime_options.py`  
  为 pipeline 增加 summary language、长视频阈值等选项字段。

- Modify: `scripts/transcribe.py`  
  后续支持长音频分段转写、片段缓存、断点续跑和更明确的 ASR 阶段日志。

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

**Success Criteria:**
- setup 默认使用项目内 `.cache/huggingface`。
- `HF_HOME` 和 `HUGGINGFACE_HUB_CACHE` 已存在时不覆盖用户显式配置。
- 模型下载失败时错误信息包含本地 cache 路径和可重跑命令。

---

### Task 2: Add Summary Validator（已完成）

**Problem:** 最终 summary 校验依赖人工检查，容易漏掉占位符、模板注释或全文复制。

**Success Criteria:**

- 新增 `scripts/validate_summary.py`。
- 校验失败时一次性输出所有 error。
- 校验通过时输出明确通过结论。
- `SKILL.md` 使用该脚本作为最终验证步骤。


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

#### Task4.1 拆分依赖安装脚本

**Problem** 目前的依赖安装由一个ps1脚本实现，当调用pip等工具时会产生大量输出污染上下文

修改目标：

- 重构依赖安装脚本，使用py脚本负责调用uv创建指定py312版本的虚拟环境，如果没有uv但是本机py有312，也可用本机py的venv创建.venv,如果以上两个条件均不满足，退出并给出明确提示
- 使用py脚本在虚拟环境中安装依赖，脚本应该把pip的大量正常输出变为简洁的状态提示，例如“[xx/all] installing xxx”，但是报错信息可以原样输出。此脚本应该只可使用Python虚拟环境创建后就有的包，读入skill根目录下的requirements.txt，并调用虚拟环境中的pip安装需要列出的依赖
- 通过subprocess调用.venv内的pip安装依赖，即`pip install -r requirements.txt --disable-pip-version-check --progress-bar off` 。主进程接受子进程的pip输出并将其简化为安装状态输出到命令行
- 安装过程中的输出和报错信息应该写入log文件中，便于排查。

#### Task4.2 规范化处理脚本输出

**Problem** 目前的处理脚本

修改目标：

### Task 5: 避免提示词注入风险，不再将转写结果作为指令的一部分，明确它们是数据

**Problem** 目前的转写结果会写入prompt文件中，有提示词注入的风险

修改目标：

- 不再拼接ASR结果到prompt中
- prompt改为使用"[]()"文件引用格式，在prompt中明确ASR结果文件，总结模板文件，总结指令文件的相对路径以及它们的作用

### Task 6: 尝试使用subagent做总结和文件写入，隔离不同的上下文

**Problem** 目前的脚本调用和总结在同一个对话中由同一个agent完成，上下文连通可能会影响总结效果

修改目标：

- 总结方式改为使用subagent总结并写入文件
- 如果无法使用subagent，允许在同一个对话中完成总结并写入文件

---

## P1 Tasks

### Task 1: Reorder Summary Prompt For Better Context Attention（已完成）

**Problem:** 当前 prompt 顺序是保存路径、指令、模板、转写文本；长文本时最终指令离输出位置较远。

---

### Task 2: Improve Stage Logs And Warning Severity

**Problem:** 用户侧不容易判断当前是在字幕检测、音频复用、ASR 转写还是 prompt 生成阶段；B 站非致命警告也容易被误解为任务失败。一方面应该减少无用、杂乱信息对Agent的干扰，另一方面应该保存log在对应视频文件夹下便于问题排查。

**Success Criteria:**

- pipeline 输出明确阶段日志。
- 非致命警告以 `Warning:` 开头，致命错误以 `Error:` 开头。
- 日志包含字幕状态、ASR provider、缓存音频复用状态和最终路径。
- 新增日志功能，保存log日志在视频文件夹下

测试结果：

run_pipeline,参数带的URL不确定有没有解析成标准https://www.bilibili.com/video/{bvid}。使用这个URL：https://www.bilibili.com/video/BV17DD6BME7D/?spm_id_from=333.1245.playlist.watchlater.redirect&vd_source=f48b605b5bcb6ac762ff04f0056b21c9
进入
[Stage] Fetch metadata, subtitles, and audio
Using auto-detected cookies: D:/codes/skills/bili-audiosummary/www.bilibili.com_cookies.txt
后卡死无输出。
首先应该给出阶段性输出

---

### Task 3: Make Page And Multi-Part Handling Explicit

**Problem:** 分 P / 多语言场景下，管线可能只处理当前 P，但用户不一定知道。

> 该改动涉及范围较大，建议先完成其它任务

**Success Criteria:**
- metadata 输出当前处理页。
- 检测到多 P 时输出总页数和当前页。
- 文档明确当前默认只处理当前 URL 指向的 P。
- 后续 `--page` / `--all-pages` 可单独开发，不在本任务实现。

### Task 4: 细化 whisper 转写切分精度

**Problem:** 目前Qwen3的ASR链路和字幕复用的切分效果较好，但是whisper的输出切分粒度太粗


**Success Criteria:**
- 使用 whisper 时寻找方案做进一步的细化切分

---

## P2 Tasks

### Task 1: Add Long Video Mode With Segment Cache

**Problem:** 长视频整段转写容易超时；失败后缺少断点续跑。

**Success Criteria:**
- 超过阈值的视频进入长视频路径，进行音频切分后再进行转写。
- 默认每段不超过 10 分钟。
- 每段转写结果单独写入转写结果文件。
- 重跑时跳过已完成片段。
- 合并后 transcript JSON/Markdown 保持现有结构。

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
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>"
.\.venv\Scripts\python.exe scripts\validate_summary.py "<summary-path>" --transcript "<transcript-md-path>"
```

Expected:

```text
Pipeline completed.
Summary validation passed.
```

## Remaining Decisions

- Default `--summary-language`: choose `zh` for Chinese user workflows, or inherit transcript language for backward compatibility.
- Long video threshold: start with 10 minutes per segment, then tune after local tests.
- `--page` / `--all-pages`: report current P first; implement selection only if real usage confirms demand.
