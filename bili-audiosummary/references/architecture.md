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
          -> use parallel faster-whisper by default
          -> when Qwen3 is selected, use only whole-audio Qwen3-ASR on CUDA
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
- `scripts/transcribe.py`: transcribes a manifest or audio file and strictly dispatches the selected provider: parallel faster-whisper or whole-audio Qwen3-ASR.
- `scripts/subtitle_transcript.py`: parses SRT subtitles and converts them to the same transcript JSON and Markdown contract used by ASR.
- `scripts/validate_summary.py`: checks that the final summary exists, is valid UTF-8, and contains no template placeholders or comments.

### Processing Helpers

- `scripts/asr/common.py`: shared ASR segment normalization helpers.
- `scripts/asr/qwen3.py`: loads local Qwen3 ASR and forced-aligner models, runs CUDA transcription, and builds timestamped sentence segments.
- `scripts/asr/parallel/`: faster-whisper parallel ASR package. It contains planning, ffmpeg media splitting, schema-versioned chunk-result caching, resume state, worker execution, merge logic, metrics, process logging, and runner orchestration.
- `scripts/config.py`: owns repository paths, language priorities, model locations, ASR defaults, and summary template selection.
- `scripts/manifest_io.py`: resolves manifest-relative paths, loads manifests and metadata, and infers result directories.
- `scripts/process_logging.py`: provides shared file logging, concise terminal filtering, timestamped log names, log relocation, subprocess capture, and failure reporting.
- `scripts/runtime_options.py`: defines structured options shared by fetch, transcription, and pipeline entry points.
- `scripts/subtitle_utils.py`: infers subtitle language codes from filenames.
- `scripts/transcript_output.py`: writes the common timestamped transcript Markdown format.
- `scripts/utils.py`: provides directory, JSON, URL, filename, media-file, and packaged-ffmpeg helpers.

### Parallel faster-whisper Invariants

- Audio is decoded as 16kHz mono for the bundled ONNX Silero VAD. The fixed parameters are `threshold=0.5`, `min_speech_duration_ms=250`, `min_silence_duration_ms=500`, `speech_pad_ms=0`, and `sampling_rate=16000`.
- The midpoint of each qualifying silence gap is a natural boundary. The global planner adds hard boundaries when VAD reports no speech or leaves continuous speech longer than 300 seconds. Chunks continuously cover the complete audio without sharing samples. Normal chunks are 60-300 seconds; audio shorter than 60 seconds may use one shorter chunk.
- The CPU budget is `B = max(1, floor(cpu_count * 0.75))`. Automatic worker candidates must divide `B`; the planner chooses the largest worker count `W` for which a legal chunk count exists. For `D >= 60`, a legal `N` is between `ceil(D / 300)` and `floor(D / 60)` and satisfies `N % W == 0`.
- Worker count has the highest planning priority. For a fixed `W`, candidate plans minimize hard speech cuts, then batches `N / W`, then the sum of squared deviations from target duration `D / N`. Planning consumes all candidate boundaries at once, so reversing candidate input order produces the same layout.
- Explicit CLI values constrain the same planner. Two explicit values must satisfy `num_workers * cpu_threads <= B`; worker-only mode uses `floor(B / W)` threads per worker; thread-only mode chooses the largest feasible worker count. An impossible explicit configuration fails before chunk files are written or the ASR model is loaded.
- Schema 4 stores a flat `chunks` list. Each `AsrChunkPlan` contains only its global `index`, `start`, `duration`, output `path`, and `end_boundary` (`silence`, `hard`, or `audio_end`). The plan also records the source fingerprint, ASR parameters, VAD parameters, CPU budget, worker count, threads per worker, and final layout.
- `vad_result.json` has its own Schema 1 identity based only on the source fingerprint and VAD parameters. A valid result, including an empty interval list, can rebuild a plan without decoding the audio or running VAD again. A matching complete plan skips VAD without loading the separate file.
- One `WhisperModel` uses the resolved worker configuration for all pending chunks. Chunk transcription keeps `vad_filter=True` to skip internal silence and does not pass `initial_prompt`.
- Merge adds `chunk.start` to every local segment timestamp and orders segments by chunk index and time. Adjacent segments whose time ranges truly intersect are combined; endpoint contact is kept separate. Chinese text is concatenated directly, other languages use one separating space, and no character-level deduplication is performed.
- Metrics record worker count, threads per worker, chunk count, batch count, elapsed time per chunk, and the final merged segment count.

### Setup Entry Points

- `scripts/setup/setup_windows.bat`: thin Windows launcher. Requires `uv`, sets project-local uv cache and default package index when unset, ensures Python 3.12 is available, then starts core setup.
- `scripts/setup/bootstrap.py`: syncs core dependencies and verifies the uv-managed Python 3.12 environment, core imports, and packaged ffmpeg.
- `scripts/setup/install_model.py`: downloads local ASR models. Supports the default faster-whisper model and optional Qwen3 ASR plus forced-aligner models.

### Setup Helpers

- `scripts/setup/environment.py`: defines setup paths, creates cache and output directories, applies default Hugging Face mirror and cache environment variables, and enforces Python 3.12.
- `scripts/setup/install_core.py`: verifies core imports and resolves packaged ffmpeg binaries.
- `scripts/setup/download_models.py`: downloads Hugging Face snapshots and verifies required model weights.
- `scripts/setup/__init__.py`: marks the setup directory as a Python package.

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
│  ├─ chunks/
│  │  └─ chunk_<index>.wav
│  └─ chunk_results/
│     └─ chunk_<index>.json
├─ asr_qwen3/
│  └─ result.json
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
- `asr_parallel/`: faster-whisper-only workspace. It stores the Schema 4 plan, progress state, Schema 1 VAD result, generated audio chunks, per-chunk results, merged intermediate transcript, and metrics used for cache reuse and interrupted-run recovery. Plan, progress, VAD, and chunk-result JSON writes are atomic.
- `asr_qwen3/result.json`: atomic Schema 1 Qwen3 cache containing source/request identity, raw text, and word-level timestamps. Reuse requires exact identity plus non-empty valid text and timestamps, and occurs before dependency, CUDA, or model checks. Fields that the model does not return are omitted and incomplete results are not reusable.
- Pipeline log: complete processing details and traceback data. It starts in `.cache/logs/` and moves into the result directory after BVID resolution.
- Summary prompt: task and data boundaries, a relative Markdown link to the transcript, embedded summary instructions, selected language template, and the full final-summary path.
- Final summary: preferably written by a fresh subagent that receives only the prompt path and summary task; when delegation is unavailable, the current Agent follows the same prompt.
- Summary validation: always run by the main Agent after the summary writer finishes.

Setup logs remain at `.cache/logs/setup-<timestamp>.log`. Standalone fetch, subtitle, or transcription logs start in `.cache/logs/` and move to the relevant result directory once that location is known.
