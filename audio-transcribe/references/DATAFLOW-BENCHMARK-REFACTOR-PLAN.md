# 私有数据流与 Benchmark 重构计划

## 完成状态

- 状态：已完成（阶段 1–5）
- 完成日期：2026-09-04
- 范围：私有 ASR workspace、pipeline diagnostics、benchmark 包布局与当前报告合同
- 公开兼容性：`result_manifest.json`、`transcript.json`、`raw_timestamps.json` 和 `audio-transcribe-contract` v1 保持不变

## 完成结果

- 私有 pipeline 已移除共享 schema、`progress.json` 和 `metrics.json` 数据流；plan、VAD、chunk、workspace result 与内存 diagnostics 已按恢复职责简化。
- Benchmark 已迁移到 `benchmark/` 包，主入口为 `python -m benchmark`，worker 入口为 `python -m benchmark.worker`；旧 `scripts/benchmark.py` 已删除且无兼容 shim。
- Benchmark report、reference manifest 和 `reference_set` 已移除内部 schema。新报告冻结 canonical config，仅在 config 和输入身份一致时续跑。
- 当前格式继续支持成功跳过、失败 attempt 追加、原子恢复点和配对 comparison；旧格式不迁移，已有成功结果不重新评分。
- 相关命令、恢复限制、历史说明和 Pyright 检查范围已同步。

## 验证

- 聚焦测试：54 项通过、1 项因本地 benchmark 音频条件跳过。
- 完整测试：296 项通过、1 项跳过。
- `ruff check`、`pyright`、`ruff format --check .` 和 `git diff --check` 通过。
- 唯一警告为当前环境无权写入 `.pytest_cache`，不影响测试结果。
