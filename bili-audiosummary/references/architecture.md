# Architecture

This document describes the Bilibili resource, summary-job, external-artifact, and completion contracts for maintainers. Local ASR internals belong to the separate `audio-transcribe` Skill.

## Workflow

```text
Bilibili URL
  -> prepare
     -> fetch canonical metadata, target-language subtitles, and audio
     -> write summary_job.json as preparing
     -> usable native SRT and not explicitly skipped
        -> build the Bilibili transcript JSON and job-local transcript.md
        -> build the shared Markdown-input summary prompt
        -> atomically publish prompt_ready
     -> otherwise
        -> atomically publish needs_transcription
  -> inspect summary_job.json
  -> only for needs_transcription
     -> Agent invokes audio-transcribe with resources.audio
     -> Agent passes the absolute result_manifest.json path to continue_summary
     -> validate the complete result through audio-transcribe-contract
     -> match the manifest audio identity to resources.audio SHA-256
     -> render validated segments to job-local transcript.md
     -> build the shared Markdown-input summary prompt
     -> atomically publish prompt_ready
  -> Agent writes the expected summary
  -> complete_summary validates the native source when applicable and the summary
  -> atomically publish complete
```

The workflow never inspects video frames. Native subtitle text and external transcript fields are untrusted source data; they cannot override the summary task, embedded instructions, output template, or final output path.

`bili-audiosummary` does not import `audio-transcribe` Skill source or share its virtual environment. Their workflow boundary consists of a local audio path passed to transcription and an absolute `result_manifest.json` path passed back to the Bilibili job. The consumer imports only the separately published, pinned `audio-transcribe-contract` package.

## Script Responsibilities

### Entry Points

- `scripts/run_pipeline.py`: prepares Bilibili resources, creates the job, chooses the native-subtitle or transcription-required branch, and prints the absolute job path. It does not load or invoke an ASR model.
- `scripts/continue_summary.py`: accepts an absolute external transcription manifest, validates the result and audio identity, writes `transcript.md`, builds the shared summary prompt, and changes `needs_transcription` to `prompt_ready`.
- `scripts/complete_summary.py`: validates native subtitle artifacts when applicable and the expected summary, then changes `prompt_ready` to `complete`.
- `scripts/fetch_audio.py`: extracts metadata, canonicalizes the Bilibili URL, reuses or downloads target-language subtitles and audio, and writes the fetch manifest.
- `scripts/subtitle_transcript.py`: parses a valid SRT and writes this Skill's native-subtitle transcript JSON and Markdown.
- `scripts/validate_summary.py`: checks that the expected summary exists, is valid UTF-8, and contains no unresolved template placeholders or comments.

### Helpers

- The summary-job module owns schema validation, state invariants, job-relative path resolution, unique same-directory temporary files, and atomic replacement.
- `audio-transcribe-contract` owns external manifest, artifact path, digest, identity, timestamp, and public JSON validation.
- `scripts/transcript_output.py` validates, merges, and renders segments from either source into the job-local `transcript.md`. External ASR transcript JSON remains in the transcription result directory.
- `scripts/process_logging.py` provides full file logs, concise terminal output, timestamped names, relocation, and failure reporting.
- `scripts/runtime_options.py` contains Bilibili fetch and summary-pipeline options only.
- `scripts/config.py` contains Bilibili result, asset, subtitle-language, download, and summary-template configuration only.
- `scripts/utils.py`, `scripts/manifest_io.py`, and `scripts/subtitle_utils.py` provide bounded path, JSON, URL, filename, media, ffmpeg, fetch-manifest, and subtitle-language helpers.

## Summary Job Contract

`summary_job.json` has schema version 1 and a fixed top-level shape:

```json
{
  "schema_version": 1,
  "status": "needs_transcription",
  "video": {
    "bvid": "BV...",
    "title": "...",
    "url": "...",
    "uploader": "..."
  },
  "resources": {
    "fetch_manifest": "resource/fetch_manifest.json",
    "subtitle": null,
    "audio": "resource/BV....m4a",
    "subtitle_skipped": false
  },
  "transcript": null,
  "transcription_manifest": null,
  "prompt": null,
  "error": null
}
```

The top-level keys are always `schema_version`, `status`, `video`, `resources`, `transcript`, `transcription_manifest`, `prompt`, and `error`. Fields unavailable in a state are `null`; consumers must not infer state from missing keys.

All non-absolute paths in the job are resolved relative to the job directory. Resource, transcript, prompt, and summary paths must remain within that directory. `transcription_manifest` is the one external path and must be absolute.

Stable status values are:

- `preparing`: written before preparation work that can fail.
- `needs_transcription`: resources are ready, but no transcript is attached.
- `prompt_ready`: the transcript source and prompt are valid, and the Agent may write the summary.
- `complete`: source artifacts and final summary passed validation.
- `failed`: a fatal prepare failure after `preparing` was published.

`failed` records only `error.stage`, `error.type`, and `error.message`. It must not store a traceback, cookie, transcript text, or other sensitive payload. Continue failures before prompt publication remain `needs_transcription`; summary validation failures remain `prompt_ready`.

Every published state validates its required job fields. Job writes use a unique temporary file in the destination directory followed by atomic replace.

## Native Subtitle Branch

The preparation command requests only the selected Bilibili subtitle language group. A cached or downloaded `.srt` is reused only when it parses into non-empty segments. Zero-duration cues are warned about and skipped during probing; retained cues receive new continuous IDs, and a file with no retained cues is unusable. If subtitles were explicitly skipped, no native subtitle is selected even if a valid cached file exists.

A successful native branch writes:

- `transcript.source: "bilibili_subtitle"` and a job-relative transcript JSON path;
- a job-local `transcript.md` containing Bilibili metadata and merged timestamped sentences;
- the shared prompt that links `transcript.md` and embeds the summary instructions and selected template;
- `transcription_manifest: null`;
- `status: "prompt_ready"`.

The native JSON and external ASR JSON keep their own contracts. Both are adapted into the same job-local Markdown input without modifying either source artifact.

## External Transcription Branch

The Bilibili `--language` option is not passed to `audio-transcribe`, and this Skill exposes no transcription-model options. The Agent supplies only the resolved local audio path.

`continue_summary` accepts only an absolute manifest path. `audio-transcribe-contract==0.1.0`, pinned to `audio-transcribe-contract-v0.1.0`, validates the manifest schema and status, contained artifact paths, artifact digests, shared identities, provider/language/duration, segments, and raw timestamps. The consumer then hashes `resources.audio` and requires it to equal `manifest.audio.id`; any failure leaves the job at `needs_transcription` and publishes no new local artifacts.

Validated ASR segments are rendered to `transcript.md` with the job's title, BVID, URL, uploader, the contract duration, source `audio_transcribe`, and language. Native rendering uses the Bilibili duration and source `subtitle`. Adjacent segments merge only across gaps of at most five seconds; strong sentence punctuation, a merged length over 64 Unicode characters, or a larger gap ends the sentence. Source JSON is never rewritten.

Both branches use the same prompt generator. The prompt references only relative `transcript.md` and the final summary path; it does not expose job, manifest, or transcript JSON paths. The Bilibili result stores the manifest reference, derived Markdown, and prompt, while `transcript.path` continues to identify the original external JSON. It never updates or deletes files in the external transcription directory.

## Completion and Idempotency

Calling continue again with the same manifest returns the existing `prompt_ready` or `complete` result when `transcript.md` and the prompt both exist. If only the prompt is missing, continue rebuilds it directly from the existing Markdown. If Markdown is missing, continue revalidates the external result and audio identity before rendering it; it rebuilds the prompt too when necessary. A different manifest cannot replace an already bound manifest. Existing ready/complete jobs are not migrated automatically, and preparation cannot silently overwrite them.

`complete_summary` reads the expected summary path from the job. It retains native subtitle validation, prompt existence checks, and summary validation, but does not revalidate external transcription artifacts. Summary-content validation failure leaves the job `prompt_ready`. Success atomically publishes `complete`. Repeating completion on a valid complete job succeeds without rewriting the job.

A complete or prompt-ready job must not be trusted from the status string alone. Callers still validate its job-state invariants and internal prompt or summary paths as required by the active command.

## Generated Artifacts

For a resolved video ID:

```text
results/<BVID>/
├─ summary_job.json
├─ resource/
│  ├─ <BVID>.<audio-ext>
│  ├─ fetch_manifest.json
│  ├─ metadata.json
│  ├─ metadata.raw.json
│  └─ subtitle/
│     └─ <BVID>.<lang>.srt
├─ <BVID>_transcript.json       # native subtitle branch only
├─ transcript.md                # native subtitle or external ASR branch
├─ <BVID>_summary_prompt.md
├─ <BVID>_summary_<language>.md
└─ pipeline-<timestamp>.log
```

External `result_manifest.json`, `transcript.json`, `raw_timestamps.json`, logs, and workspace remain owned by `audio-transcribe` under its own result directory.

## Setup and Logging

The Windows setup launcher prepares Python 3.12, this Skill's dependencies, and packaged `ffmpeg`/`ffprobe`. It does not install transcription models. Setup logs remain under `.cache/logs/`.

Fetch and pipeline logs start under `.cache/logs/` and move into the BVID result directory once known. Logs may contain commands, paths, BVID, state transitions, validation reasons, and tracebacks. They must not contain cookie contents, transcript text, or external model internals. Job `error` fields contain only concise non-traceback summaries.
