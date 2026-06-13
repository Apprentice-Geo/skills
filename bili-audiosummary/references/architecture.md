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
       -> transcribe audio with faster-whisper
       -> optionally try Qwen3-ASR first on CUDA
       -> fall back to faster-whisper if Qwen3 is unavailable or fails
  -> write transcript JSON and Markdown
  -> combine summary instructions, language template, and transcript
  -> write the summary prompt and expected final-summary path
  -> Agent writes the final summary
  -> validate the summary file
```

The summary is based only on the generated transcript. The pipeline does not inspect video frames.

## Directory Responsibilities

- Repository root: public documentation, dependency declarations, license, and Agent Skill metadata.
- `assets/`: summary instructions and language-specific summary templates embedded into each generated prompt.
- `models/`: local faster-whisper model plus optional Qwen3 ASR and forced-aligner models.
- `.cache/`: setup caches, Hugging Face cache, and logs created before a BVID result directory is known.
- `results/`: per-video downloaded resources, transcripts, prompts, summaries, and processing logs.
- `scripts/`: setup, download, transcript, prompt, logging, and validation code.
- `tests/`: behavior and contract tests for scripts and Skill metadata.
- `references/`: maintainer architecture and operational error-handling documentation.

## Script Responsibilities

### Processing Entry Points

- `scripts/run_pipeline.py`: main workflow. Fetches resources, selects a usable subtitle or ASR fallback, writes transcript outputs, and builds the summary prompt.
- `scripts/fetch_audio.py`: extracts metadata, resolves BVID and canonical URL, reuses or downloads target-language subtitles and the lowest usable audio stream, and writes resource metadata and the fetch manifest.
- `scripts/transcribe.py`: transcribes a manifest or audio file. Uses faster-whisper by default and coordinates optional Qwen3 fallback behavior.
- `scripts/subtitle_transcript.py`: parses SRT subtitles and converts them to the same transcript JSON and Markdown contract used by ASR.
- `scripts/validate_summary.py`: checks that the final summary exists, is valid UTF-8, and contains no template placeholders or comments.

### Processing Helpers

- `scripts/asr_qwen3.py`: loads local Qwen3 ASR and forced-aligner models, runs CUDA transcription, and builds timestamped sentence segments.
- `scripts/config.py`: owns repository paths, language priorities, model locations, ASR defaults, and summary template selection.
- `scripts/manifest_io.py`: resolves manifest-relative paths, loads manifests and metadata, and infers result directories.
- `scripts/process_logging.py`: provides shared file logging, concise terminal filtering, timestamped log names, log relocation, subprocess capture, and failure reporting.
- `scripts/runtime_options.py`: defines structured options shared by fetch, transcription, and pipeline entry points.
- `scripts/subtitle_utils.py`: infers subtitle language codes from filenames.
- `scripts/transcript_output.py`: writes the common timestamped transcript Markdown format.
- `scripts/utils.py`: provides directory, JSON, URL, filename, media-file, and packaged-ffmpeg helpers.

### Setup Entry Points

- `scripts/setup/setup_windows.bat`: thin Windows launcher. Prefers `uv` with Python 3.12 and falls back to `py -3.12`.
- `scripts/setup/setup.py`: orchestrates core setup: environment creation, dependency installation and verification, packaged ffmpeg verification, and faster-whisper model download.
- `scripts/setup/install_qwen3.py`: optional Qwen3 setup. Installs CUDA PyTorch and Qwen3 requirements, verifies imports, and downloads both Qwen3 models.

### Setup Helpers

- `scripts/setup/environment.py`: defines setup paths, creates cache and output directories, applies default mirror and cache environment variables, and enforces Python 3.12.
- `scripts/setup/install_core.py`: installs core requirements, retries official PyPI when the configured index fails, verifies requirements and imports, and resolves packaged ffmpeg binaries.
- `scripts/setup/download_models.py`: downloads Hugging Face snapshots and verifies required model weights.
- `scripts/setup/requirements_check.py`: validates installed packages against exact pins, ranges, markers, and unpinned requirements.
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
├─ <BVID>_transcript.json
├─ <BVID>_transcript.md
├─ <BVID>_summary_prompt.md
├─ <BVID>_summary_<language>.md
└─ pipeline-<timestamp>.log
```

- Transcript JSON: structured metadata, source provider, language, and timestamped segments.
- Transcript Markdown: human- and Agent-readable timestamped transcript.
- `fetch_manifest.json`: canonical video identity plus paths to metadata, audio, and subtitles.
- `metadata.json`: compact metadata used by later stages.
- `metadata.raw.json`: sanitized full metadata returned by yt-dlp.
- Pipeline log: complete processing details and traceback data. It starts in `.cache/logs/` and moves into the result directory after BVID resolution.
- Summary prompt: output path, summary instructions, selected language template, and transcript content.
- Final summary: written by the Agent to the path declared in the summary prompt.

Setup logs remain at `.cache/logs/setup-<timestamp>.log`. Standalone fetch, subtitle, or transcription logs start in `.cache/logs/` and move to the relevant result directory once that location is known.
