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

- `scripts/asr/common.py`: shared ASR segment normalization helpers.
- `scripts/asr/chunking/`: provider-neutral normalized audio, planning VAD, integer-sample layouts, legal chunk-count strategies, continuous-cover validation, and fixed-count boundary optimization.
- `scripts/asr/qwen3.py`: owns the Qwen3 Schema 3 workspace, `full` scheduling, CUDA model/forced-aligner loading, isolated retry, global timestamp offsets, and sentence assembly.
- `scripts/asr/parallel/`: faster-whisper Schema 6 package. `plan.py` owns Whisper identity and worker selection, and worker execution consumes ndarray slices. The package also contains caching, resume state, merge logic, metrics, logging, and orchestration.
- `scripts/config.py`: owns repository paths, language priorities, model locations, ASR defaults, and summary template selection.
- `scripts/manifest_io.py`: resolves manifest-relative paths, loads manifests and metadata, and infers result directories.
- `scripts/process_logging.py`: provides shared file logging, concise terminal filtering, timestamped log names, log relocation, subprocess capture, and failure reporting.
- `scripts/runtime_options.py`: defines structured options shared by fetch, transcription, and pipeline entry points.
- `scripts/subtitle_utils.py`: infers subtitle language codes from filenames.
- `scripts/transcript_output.py`: writes the common timestamped transcript Markdown format.
- `scripts/utils.py`: provides directory, JSON, URL, filename, media-file, and packaged-ffmpeg helpers.

### Parallel faster-whisper Invariants

- The source is decoded once as 16kHz mono float32 PCM. Planning VAD and all provider chunks share this in-memory array; normalized PCM is not persisted. Planning uses `threshold=0.35`, `neg_threshold=0.25`, `min_speech_duration_ms=0`, `min_silence_duration_ms=300`, unlimited `max_speech_duration_s`, and `speech_pad_ms=0`.
- Speech intervals and layouts use exact integer sample coordinates. The full complement, including leading and trailing silence, is safe boundary space; only a boundary strictly inside speech is `hard`. Chunks cover every sample exactly once. Normal chunks contain 480,000-2,880,000 samples; shorter audio uses one shorter chunk.
- The CPU budget is `B = max(1, floor(cpu_count * 0.75))`. Automatic workers do not exceed `ceil(D / 180)`, divide `B` for equal threads, and must admit a legal chunk count `N` divisible by `W`. Explicit worker/thread values keep precedence but must remain legal.
- Candidate plans are compared globally by hard cuts, batches `N / W`, maximum estimated VAD speech load, speech-load MSRE, and boundary tuple. No-speech regions can split anywhere in silence without a hard-cut charge; long continuous speech uses the fewest necessary explicit hard boundaries.
- Explicit CLI values constrain the same planner. Two explicit values must satisfy `num_workers * cpu_threads <= B`; worker-only mode uses `floor(B / W)` threads per worker; thread-only mode chooses the largest feasible worker count. An impossible explicit configuration fails before decoding or model loading.
- Schema 6 stores `start_sample`, `end_sample`, and `estimated_speech_samples`; Schema 5 plan, progress, VAD, and chunk results are rejected. The plan records source sample count/rate, ASR and VAD identities, sample bounds, count strategy, CPU budget, workers, threads, and layout.
- One `WhisperModel` uses the resolved worker configuration for all pending ndarray slices. Chunk transcription passes `vad_filter=True` but no `vad_parameters`, keeping faster-whisper's internal defaults separate from planning VAD. No ffmpeg chunk WAV is generated.
- Each chunk gets one retry after its first failure in a program invocation. A second failure prevents merge; a later invocation gives incomplete chunks a new retry budget while retaining valid compatible results.
- Merge converts `chunk.start_sample` to seconds for every local segment timestamp and orders segments by chunk index and time. Adjacent segments whose time ranges truly intersect are combined; endpoint contact is kept separate. Chinese text is concatenated directly, other languages use one separating space, and no character-level deduplication is performed.
- Metrics add hard-cut count, per-chunk estimated speech durations, maximum estimated speech duration, and speech-load MSRE to worker, chunk, batch, elapsed, and segment fields. Soft-duration metrics are not recorded.

### Qwen3 Invariants

- Qwen3 uses the same fixed-count optimizer as Whisper. With the same sample count, speech intervals, bounds, and fixed chunk count, both providers receive identical boundaries.
- Its count strategy is always `full` with `group_size=QWEN3_MAX_INFERENCE_BATCH_SIZE`: use legal group-size multiples when available; otherwise use the greatest legal chunk count. Execution batches use the same constant and `max_new_tokens=1024`. Pipeline language codes are mapped to Qwen3's canonical names (`zh` to `Chinese`, `en` to `English`) and passed to every batch and isolated retry.
- Schema 3 plan, progress, per-chunk results, and merged `result.json` use exact source/request/layout identity and atomic writes. The request identity includes both the pipeline language code and mapped model language. A complete merged cache returns before decode, dependency import, CUDA check, or model load. Partial recovery decodes and loads once.
- Each chunk's text is stripped before merge, and non-empty chunks are always joined with one ASCII space regardless of language.
- A failed batch is retried once as individual single-chunk calls. Successful members are cached immediately; any remaining failure blocks merge, while the next invocation may retry only missing chunks. Empty chunk text and timestamps are cacheable; validation is limited to schema, identity, coordinate, and data-type safety.

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
│  ├─ merged_transcript.json
│  └─ chunk_results/
│     └─ chunk_<index>.json
├─ asr_qwen3/
│  ├─ asr_plan.json
│  ├─ progress.json
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
- Transcript Markdown: human- and Agent-readable timestamped transcript. When ASR segments are compacted onto one line, Chinese segments use `，` and other languages use `, ` as separators; line breaks caused by compaction limits do not receive extra punctuation.
- `fetch_manifest.json`: canonical video identity plus paths to metadata, audio, and subtitles.
- `metadata.json`: compact metadata used by later stages.
- `metadata.raw.json`: sanitized full metadata returned by yt-dlp.
- `asr_parallel/`: faster-whisper-only Schema 6 workspace with sample-coordinate plan/VAD/chunk results, progress, merged intermediate transcript, and metrics. It contains no normalized PCM or chunk WAV files.
- `asr_qwen3/`: Qwen3-only Schema 3 workspace with plan, progress, sample-coordinate VAD, per-chunk raw text/timestamps, and merged `result.json`.
- Pipeline log: complete processing details and traceback data. It starts in `.cache/logs/` and moves into the result directory after BVID resolution.
- Summary prompt: task and data boundaries, a relative Markdown link to the transcript, embedded summary instructions, selected language template, and the full final-summary path.
- Final summary: preferably written by a fresh subagent that receives only the prompt path and summary task; when delegation is unavailable, the current Agent follows the same prompt.
- Summary validation: always run by the main Agent after the summary writer finishes.

Setup logs remain at `.cache/logs/setup-<timestamp>.log`. Standalone fetch, subtitle, or transcription logs start in `.cache/logs/` and move to the relevant result directory once that location is known.
