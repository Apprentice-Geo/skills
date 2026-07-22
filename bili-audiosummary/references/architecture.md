# Architecture

This document describes the pipeline, directory ownership, script responsibilities, and generated artifacts for maintainers.

## Pipeline

```text
Bilibili URL
  -> extract metadata and BVID with yt-dlp
  -> normalize subsequent downloads to the canonical Bilibili URL
  -> reuse or download target-language subtitles and audio
  -> prefer a valid SRT subtitle
       -> convert subtitle segments to the unified transcript
     otherwise
       -> transcribe audio
          -> decode once to shared 16kHz mono float32 PCM and plan sample-coordinate chunks
          -> use parallel faster-whisper by default
          -> when Qwen3 is selected, use only chunked Qwen3-ASR on CUDA
             -> stop transcription if Qwen3 preparation or execution fails
  -> write transcript JSON and Markdown
  -> write a summary prompt containing task boundaries, a relative transcript link,
     embedded summary instructions and language template, and the final output path
  -> write the summary prompt and expected final-summary path
  -> preferably dispatch a fresh subagent that reads only the prompt and linked transcript
  -> subagent or current Agent writes the final summary
  -> main Agent validates the summary file
```

The summary is based only on the generated transcript. The pipeline does not inspect video frames.
Transcript Markdown is treated as untrusted data. It is linked from the prompt rather than embedded, and cannot override the prompt task, instructions, template, or final output path. This prompt-layer boundary reduces injection risk but is not a hard security sandbox.

## Directory Responsibilities

- Repository root: public documentation, dependency declarations, license, and Agent Skill metadata.
- `assets/`: summary instructions and language-specific summary templates embedded into each generated prompt.
- `models/`: local ASR models. At least one of faster-whisper or Qwen3 must be installed before ASR use.
- `.cache/`: setup caches, Hugging Face cache, and logs created before a BVID result directory is known.
- `results/`: per-video downloaded resources, transcripts, prompts, summaries, and processing logs.
- `scripts/`: setup, download, transcript, prompt, logging, and validation code.
- `tests/`: behavior and contract tests for scripts and Skill metadata.
- `references/`: maintainer architecture and operational error-handling documentation.

## Script Responsibilities

### Processing Entry Points

- `scripts/`: Python package containing the pipeline and setup modules. Prefer `python -m scripts.<module>` entrypoints.
- `scripts/run_pipeline.py`: main workflow. Fetches resources, selects a usable subtitle or ASR fallback, writes transcript outputs, and builds the summary prompt.
- `scripts/fetch_audio.py`: extracts metadata, resolves BVID and canonical URL, reuses or downloads target-language subtitles and the lowest usable audio stream, and writes resource metadata and the fetch manifest.
- `scripts/transcribe.py`: transcribes a manifest or audio file and strictly dispatches the selected provider to its provider workspace.
- `scripts/subtitle_transcript.py`: parses SRT subtitles and converts them to the same transcript JSON and Markdown contract used by ASR.
- `scripts/validate_summary.py`: checks that the final summary exists, is valid UTF-8, and contains no template placeholders or comments.
- `scripts/benchmark.py`: runs the fixed ASR provider benchmark matrix in isolated child processes and records transcription time, real-time factor, process-tree RSS, and Qwen3 CUDA memory.
- `scripts/benchmark_whisper_parallel.py`: compares faster-whisper chunk upper limits across repeated runs and summarizes timing and planner-quality metrics. It is a development benchmark rather than part of the summary pipeline.

### Processing Helpers

- `scripts/asr/chunking/`: provider-neutral normalized audio, shared planning-VAD identity, integer-sample layouts, legal chunk-count strategies, continuous-cover validation, and fixed-count boundary optimization.
- `scripts/asr/pipeline.py`, `pipeline_types.py`, and `workspace.py`: own the unified Schema 1 plan, decode/VAD/cache orchestration, provider-neutral `ChunkTranscript`, progress reconstruction, metrics, and atomic artifacts.
- `scripts/asr/alignment.py` and `merge.py`: strictly map ordered word text to provider text, validate local/global times, apply sample-coordinate offsets, and synthesize sentence segments.
- `scripts/asr/providers/`: provider strategies for model readiness/loading, request identity, invocation, raw-result parsing, provider metadata, and final output postprocessing.
- `scripts/asr/execution/`: Whisper CPU and Qwen3 CUDA execution policies. They choose execution constraints and scheduling while reusing the same chunk optimizer and shared pipeline.
- `scripts/config.py`: owns repository paths, language priorities, model locations, ASR defaults, and summary template selection.
- `scripts/manifest_io.py`: resolves manifest-relative paths, loads manifests and metadata, and infers result directories.
- `scripts/process_logging.py`: provides shared file logging, concise terminal filtering, timestamped log names, log relocation, subprocess capture, and failure reporting.
- `scripts/runtime_options.py`: defines structured options shared by fetch, transcription, and pipeline entry points.
- `scripts/subtitle_utils.py`: infers subtitle language codes from filenames.
- `scripts/transcript_output.py`: writes the common timestamped transcript Markdown format.
- `scripts/utils.py`: provides directory, JSON, URL, filename, media-file, and packaged-ffmpeg helpers.

### Shared ASR Pipeline Invariants

- The source is decoded once as 16kHz mono float32 PCM. Planning VAD and all provider chunks share this in-memory array; normalized PCM is not persisted. Planning uses `threshold=0.35`, `neg_threshold=0.25`, `min_speech_duration_ms=0`, `min_silence_duration_ms=300`, unlimited `max_speech_duration_s`, and `speech_pad_ms=0`.
- Speech intervals and layouts use exact integer sample coordinates. The full complement, including leading and trailing silence, is safe boundary space; only a boundary strictly inside speech is `hard`. Chunks cover every sample exactly once. Normal chunks contain 480,000-2,880,000 samples; shorter audio uses one shorter chunk.
- `ASR_PIPELINE_SCHEMA_VERSION = 1` is shared by both provider workspaces. A plan records source identity, provider request identity, execution-policy identity, VAD/planning parameters, and complete `ChunkLayout` values. Any mismatch rejects the plan and its chunk results; older Whisper Schema 6 and Qwen3 Schema 3 files are ignored rather than migrated or deleted.
- Both `asr_parallel/` and `asr_qwen3/` use `asr_plan.json`, `vad_result.json`, `progress.json`, `chunk_results/`, `result.json`, and `metrics.json`. Complete valid chunk cache skips audio decode, device checks, and model loading. Partial recovery decodes once, rebuilds progress from valid chunk artifacts, and processes only missing/invalid chunks.
- `ChunkTranscript` contains exact chunk identity, raw complete provider text, ordered words, provider metadata, and elapsed time. Each word stores `text`, local `start`/`end`, and `probability`; Qwen3 probability is `null`. New and cached chunks must pass strict text mapping and finite monotonic local-boundary validation before reuse.
- Merge follows plan order, joins chunk source texts with an ASCII whitespace boundary, adds each layout's sample-coordinate offset to its words, validates the complete global mapping, then runs shared sentence synthesis. Chunk files and merged `result.json` retain word-level data; public transcript JSON and Markdown expose only sentence-level `segments`.
- Each word is authoritative: its text is matched exactly and in order against source text, while only source whitespace and Unicode punctuation may be skipped. Unmatched, extra, reordered, empty, non-finite, non-monotonic, or out-of-range items invalidate the result.
- Sentence construction never splits inside one alignment item. Strong punctuation creates semantic boundaries only after the accumulated duration reaches the 2-second minimum; a shorter trailing interval is merged backward when possible. Within each interval, candidates at or above the minimum prioritize strong punctuation, weak punctuation, whitespace, and then other boundaries, with the earliest boundary winning ties. The hard limits are 24 seconds and 72 alignment items. English providers emit one word per alignment item, while Chinese providers emit one character per item.

### Whisper Provider and CPU Policy

- The CPU budget is `B = max(1, floor(cpu_count * 0.75))`. Automatic workers do not exceed `ceil(D / 180)`, divide `B` for equal threads, and must admit a legal chunk count `N` divisible by `W`. Explicit worker/thread values keep precedence but must remain legal.
- Candidate plans are compared globally by hard cuts, batches `N / W`, maximum estimated VAD speech load, speech-load MSRE, and boundary tuple. No-speech regions can split anywhere in silence without a hard-cut charge; long continuous speech uses the fewest necessary explicit hard boundaries.
- Explicit CLI values constrain the same planner. Two explicit values must satisfy `num_workers * cpu_threads <= B`; worker-only mode uses `floor(B / W)` threads per worker; thread-only mode chooses the largest feasible worker count. An impossible explicit configuration fails before decoding or model loading.
- One `WhisperModel` uses the resolved worker configuration for all pending ndarray slices. Chunk transcription fixes `word_timestamps=True`, passes `vad_filter=True` without provider VAD overrides, and preserves raw segment text plus `words[].word/start/end/probability`. No ffmpeg chunk WAV is generated.
- Each chunk gets one retry after its first failure in a program invocation. A second failure prevents merge; a later invocation gives incomplete chunks a new retry budget while retaining valid compatible results.
- Simplified-Chinese conversion runs only on a copy of synthesized sentence segments. Raw text and word mappings in chunk and merged cache artifacts remain provider output and are validated before conversion.
- Metrics add hard-cut count, per-chunk estimated speech durations, maximum estimated speech duration, and speech-load MSRE to worker, chunk, batch, elapsed, and segment fields. Soft-duration metrics are not recorded.

### Qwen3 Provider and CUDA Policy

- Qwen3 uses the same fixed-count optimizer as Whisper. With the same sample count, speech intervals, bounds, and fixed chunk count, both providers receive identical boundaries.
- Its count strategy is always `full` with `group_size=QWEN3_MAX_INFERENCE_BATCH_SIZE`: use legal group-size multiples when available; otherwise use the greatest legal chunk count. Execution batches use the same constant and `max_new_tokens=1024`. Pipeline language codes are mapped to Qwen3's canonical names (`zh` to `Chinese`, `en` to `English`) and passed to every batch and isolated retry.
- During Qwen3 raw-result parsing, only the final word may absorb forced-aligner quantization at the chunk boundary: when its start remains inside the chunk and its end exceeds the exact duration by at most `0.1s`, the provider logs a warning and clips that end to the duration before strict validation. Earlier-word overruns, larger final overruns, and all other mapping or time violations remain errors.
- Request identity includes the pipeline and mapped model language, ASR and forced-aligner paths, device/dtype, `max_new_tokens`, and timestamp flag. Execution identity separately includes batch size, `full` count strategy, group size, and batch-isolation behavior.
- A failed batch, including an alignment-contract failure, is retried once as individual single-chunk calls. Successful members are cached immediately; any remaining failure blocks merge, while the next invocation may retry only missing or invalid chunks. Empty chunk text and timestamps remain cacheable.

### Setup Entry Points

- `scripts/setup/setup_windows.bat`: thin Windows launcher. Requires `uv`, sets project-local uv cache and default package index when unset, ensures Python 3.12 is available, then starts core setup.
- `scripts/setup/bootstrap.py`: syncs core dependencies and verifies the uv-managed Python 3.12 environment, core imports, and packaged ffmpeg.
- `scripts/setup/install_model.py`: downloads local ASR models. Supports the default faster-whisper model and optional Qwen3 ASR plus forced-aligner models.

### Setup Helpers

- `scripts/setup/environment.py`: defines setup paths, creates cache and output directories, applies default Hugging Face mirror and cache environment variables, and enforces Python 3.12.
- `scripts/setup/install_core.py`: verifies core imports and resolves packaged ffmpeg binaries.
- `scripts/setup/download_models.py`: downloads Hugging Face snapshots and verifies required model weights.
- `scripts/setup/__init__.py`: marks the setup directory as a Python package.

## Benchmark

`python -m scripts.benchmark` uses the fixed `BENCHMARK_VIDEOS` list and runs both `whisper` and `qwen3` unless `--video` or `--provider` narrows the matrix. Provider readiness is checked before audio fetching. Audio is downloaded or reused under `.cache/benchmark/audio/`, outside the measured interval.

Each provider case runs in a child process. The measured interval includes model loading, transcription, alignment, and transcript writing. It excludes audio fetching, dependency installation, and model downloads. Peak RSS is sampled across the child process tree; CUDA peak allocated and reserved memory are recorded only for Qwen3. A failed case remains in the report and causes the command to return a nonzero exit status after all selected cases finish.

Each run writes `benchmark.json`, `benchmark.md`, per-case inputs, results, logs, and transcripts under a new timestamped directory in `results/benchmark/` by default. The report records platform and Python information but does not include cookie paths or contents.

`python -m scripts.benchmark_whisper_parallel` is the focused faster-whisper planner benchmark. Its default matrix uses the configured short-video subset, three repetitions, and chunk upper limits of 180, 300, and 450 seconds. Limit order rotates between repetitions. It reports per-video medians, hard-cut quality, runtime ratios, and the selected winner under its encoded acceptance rules. Results default to timestamped directories under `results/benchmark/whisper-chunk-limits/`; an optional Schema 4 baseline directory is reference-only comparison input.

## Generated Artifacts

For a resolved video ID, processing writes under `results/<BVID>/`:

```text
results/<BVID>/
├─ resource/
│  ├─ <BVID>.<audio-ext>
│  ├─ fetch_manifest.json
│  ├─ metadata.json
│  ├─ metadata.raw.json
│  └─ subtitle/
│     └─ <BVID>.<lang>.srt
├─ asr_parallel/
│  ├─ asr_plan.json
│  ├─ progress.json
│  ├─ metrics.json
│  ├─ vad_result.json
│  ├─ result.json
│  └─ chunk_results/
│     └─ chunk_<index>.json
├─ asr_qwen3/
│  ├─ asr_plan.json
│  ├─ progress.json
│  ├─ metrics.json
│  ├─ vad_result.json
│  ├─ result.json
│  └─ chunk_results/
│     └─ chunk_<index>.json
├─ <BVID>_transcript.json
├─ <BVID>_transcript.md
├─ <BVID>_summary_prompt.md
├─ <BVID>_summary_<language>.md
└─ pipeline-<timestamp>.log
```

- Transcript JSON: structured metadata, source provider, language, and timestamped segments.
- Transcript Markdown: human- and Agent-readable timestamped transcript. It is a pure rendering of transcript JSON: every non-empty JSON segment becomes one adjacent Markdown transcript line. Rendering does not merge or split segments and does not insert punctuation.
- `fetch_manifest.json`: canonical video identity plus paths to metadata, audio, and subtitles.
- `metadata.json`: compact metadata used by later stages.
- `metadata.raw.json`: sanitized full metadata returned by yt-dlp.
- `asr_parallel/` and `asr_qwen3/`: provider-separated unified Schema 1 workspaces. Both store the same plan/VAD/progress/chunk/result/metrics layout. Chunk and merged results include raw text and word-level timestamps; no normalized PCM or chunk WAV is persisted.
- Pipeline log: complete processing details and traceback data. It starts in `.cache/logs/` and moves into the result directory after BVID resolution.
- Summary prompt: task and data boundaries, a relative Markdown link to the transcript, embedded summary instructions, selected language template, and the full final-summary path.
- Final summary: preferably written by a fresh subagent that receives only the prompt path and summary task; when delegation is unavailable, the current Agent follows the same prompt.
- Summary validation: always run by the main Agent after the summary writer finishes.

Setup logs remain at `.cache/logs/setup-<timestamp>.log`. Standalone fetch, subtitle, or transcription logs start in `.cache/logs/` and move to the relevant result directory once that location is known.
