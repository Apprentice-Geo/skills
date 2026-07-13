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

**目标：** 使用静音切点替代固定重叠窗口，移除不可靠的字符重叠检查和 macro-chunk 组织，同时保留并行转写、计划落盘、chunk 结果缓存及断点续跑。

#### Task 8.1 开发分支与实验改动隔离

- 开发前先保存当前 `benchmark/whisper-parallel-speedup` 的未提交改动，从 `feat/bili-audiosummary` 创建 `feat/silero-vad-chunking`。
- 新分支带入本计划文档，但不带入当前 `merge.py` 的实验改动。
- `worker.py` 移除 `initial_prompt` 属于独立 bug 修复；不走 merge 流程，在 Task 8 开发中重新实现并移除对应失效 import。
- 当前实验改动需要保留，确保之后仍可回到实验分支恢复。

#### Task 8.2 Silero VAD 与自然切点

- 复用固定版本 `faster-whisper==1.2.1` 内置的 ONNX Silero VAD，不新增 `silero-vad`、PyTorch 或 torchaudio 基础依赖。
- 将音频解码为 16kHz 单声道后获取 start-end 语音区间；使用 `threshold=0.5`、最短语音 `250ms`、最短静音 `500ms`、无 speech padding。
- 相邻语音区间之间的静音中点是自然切点；VAD 未检测到语音或连续语音过长时，由全局规划器生成必要的硬切点。
- 最终切片连续覆盖完整音频、互不重叠，正常时长限制为 `60s-300s`；完整音频不足 60 秒时允许单切片例外。

#### Task 8.3 联合规划切片数与 worker 数

- CPU 线程预算保持 `B = max(1, floor(cpu_count * 0.75))`。
- 自动模式移除 worker 最大值 8 和固定 worker 档位，worker 上限改为 `B`；只考虑满足 `B % W == 0` 的 worker 数，并优先选择最大的可行 `W`，每个 worker 使用 `B / W` 个线程。
- 对音频时长 `D`，正常切片数范围为 `ceil(D / 300)` 到 `floor(D / 60)`，并强制 `N % W == 0`，使每个 worker 获得相同数量的切片。
- 对每个候选切片数 `N`，使用固定段数的有向无环图动态规划选择全局边界：
  1. 切片必须满足 `60s-300s`，完整覆盖音频且没有交叠。
  2. 优先最小化语音中的硬切次数。
  3. 再最小化批次数 `N / W`。
  4. 最后最小化各切片相对目标时长 `D / N` 的平方偏差，使切片和 worker 负载尽可能均衡。
- worker 数是最高层规划优先级；静音切点、批次数和时长均衡只在相同 worker 数下比较。算法使用全部候选切点一次性求解，结果不依赖正序或逆序贪心。
- 显式 CLI 参数优先于自动规划：
  - 同时指定 `--num-workers` 和 `--cpu-threads` 时保留用户值，但要求乘积不超过线程预算。
  - 只指定 worker 数时，每个 worker 使用 `floor(B / W)` 个线程，允许余数线程闲置。
  - 只指定每 worker 线程数时，在预算内选择存在合法切片方案的最大 worker 数。
  - 显式 worker 仍要求 `N % W == 0`；若不存在满足时长限制的切片数，在音频切分和模型加载前报错。

#### Task 8.4 简化 plan、执行和断点续跑

- ASR plan schema 升级，删除 `MacroChunkPlan`、macro index、trusted window、source overlap、左右 overlap 等字段；旧 schema 的 plan 和 chunk result 不复用。
- 每个 chunk 只记录全局 index、start、duration、输出路径和结束边界类型（静音、硬切或音频结尾）。plan 同时记录 VAD 参数、CPU 预算、worker 配置和最终切片布局。
- workspace 简化为 `chunks/chunk_<index>.wav` 和 `chunk_results/chunk_<index>.json`。
- plan 的音频指纹、ASR 参数、VAD 参数及 worker 配置完全匹配时，重跑直接复用切分计划和有效 chunk result；不匹配时重建 plan/progress，旧结果保留但忽略。
- 保留 JSON 原子替换、有效结果优先、单次运行内失败重试一次，以及再次运行时为未完成 chunk 提供新重试预算的行为。
- 移除按 macro 串行执行的逻辑，只加载一个统一配置的 WhisperModel，使用规划出的 worker 数处理全部 pending chunks。
- chunk 转写继续使用 `vad_filter=True` 跳过内部静音，但不再传递 `initial_prompt`。

#### Task 8.5 简化结果合并与指标

- chunk 内 segment 时间戳直接加 `chunk.start` 转为全局时间，不再按 trusted window 裁剪，也不再执行字符级重叠检测。
- 按 chunk index 和 segment 时间排序；若相邻 segment 的时间范围真正重叠，则合并为一个 segment，start 取较早值、end 取较晚值，中文文本直接拼接，其他语言使用单个空格连接。
- 时间端点仅相接时不合并；合并完成后重新连续编号，并继续校验结束时间不得早于开始时间。
- metrics 删除 macro 指标，记录 worker 数、每 worker 线程数、切片数、批次数、各切片耗时和最终 segment 数。

#### Task 8.6 测试与文档验收

- VAD 测试覆盖 500ms 静音配置、静音中点、无语音输入和连续长语音硬切。
- 规划测试覆盖完整音频无缝覆盖、`60s-300s`、`N % W == 0`、最大 worker 优先、硬切最少、负载均衡，以及正序/逆序输入得到相同计划。
- worker 测试覆盖不同 CPU 预算、显式参数和闲置线程；例如预算 24、音频 900 秒时应规划为 12 worker、12 个约 75 秒切片。
- 缓存测试覆盖旧 schema 不复用、新 schema 完整匹配时复用 plan/result，以及音频、VAD 或 worker 配置变化时重建。
- merge 测试覆盖全局时间偏移、真实重叠合并、端点相接不合并、无字符去重和非法时间戳。
- 增加回归测试，确保实际 chunk 转写调用不包含 `initial_prompt`。
- 同步 `README.md`、`references/architecture.md` 和 `references/error-handling.md` 中的 worker 约束、目录结构、schema、缓存及 merge 说明。
- 完成后运行 `pytest tests -q`，再使用已有本地音频验证切片计划、并行转写、缓存复用和最终时间线。

### Task 9: 增加 Qwen3-ASR 的中间结果落盘

目前的 Qwen3-ASR 路径会直接落盘最终结果，但是中间的text和词级别时间戳不会落盘，需要类似 whisper 路径的中间结果落盘，便于排查。 

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

- `--summary-language` 默认继承 transcript 语言；调用方需要跨语言总结时显式传入 `zh` 或 `en`。（已确认）
- `--page` / `--all-pages`: report current P first; implement selection only if real usage confirms demand.

uv run --no-sync python -m scripts.benchmark_whisper_parallel --video BV1W694BEE7F --video BV1yt4y1Q7SS --video BV1Ls41127sG --video BV1MN4y177PB  --repetitions 3 --output-dir results/benchmark/whisper-parallel-clean
