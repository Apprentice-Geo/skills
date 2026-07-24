# Architecture

This document describes the Bilibili resource, summary-job, external-artifact, and completion contracts for maintainers. Local ASR internals belong to the separate `audio-transcribe` Skill.

## Workflow

```text
Bilibili URL
  -> prepare
     -> fetch canonical metadata, target-language subtitles, and audio
     -> write summary_job.json as preparing
     -> usable native SRT and not explicitly skipped
        -> build the Bilibili transcript JSON and Markdown
        -> build the native-subtitle summary prompt
        -> atomically publish prompt_ready
     -> otherwise
        -> atomically publish needs_transcription
  -> inspect summary_job.json
  -> only for needs_transcription
     -> Agent invokes audio-transcribe with resources.audio
     -> Agent passes the absolute result_manifest.json path to continue_summary
     -> validate manifest, digest, transcript, and audio identity
     -> build the external-transcript summary prompt
     -> atomically publish prompt_ready
  -> Agent writes the expected summary
  -> complete_summary revalidates the source and summary
  -> atomically publish complete
```

The workflow never inspects video frames. Native subtitle text and external transcript fields are untrusted source data; they cannot override the summary task, embedded instructions, output template, or final output path.

`bili-audiosummary` and `audio-transcribe` do not import each other's Python modules or share virtual environments. Their boundary consists of a local audio path passed to transcription and an absolute `result_manifest.json` path passed back to the Bilibili job.

## Script Responsibilities

### Entry Points

- `scripts/run_pipeline.py`: prepares Bilibili resources, creates the job, chooses the native-subtitle or transcription-required branch, and prints the absolute job path. It does not load or invoke an ASR model.
- `scripts/continue_summary.py`: accepts an absolute external transcription manifest, validates the complete public artifact chain and matching audio identity, builds the ASR summary prompt, and changes `needs_transcription` to `prompt_ready`.
- `scripts/complete_summary.py`: validates the source artifacts and expected summary, then changes `prompt_ready` to `complete`.
- `scripts/fetch_audio.py`: extracts metadata, canonicalizes the Bilibili URL, reuses or downloads target-language subtitles and audio, and writes the fetch manifest.
- `scripts/subtitle_transcript.py`: parses a valid SRT and writes this Skill's native-subtitle transcript JSON and Markdown.
- `scripts/validate_summary.py`: checks that the expected summary exists, is valid UTF-8, and contains no unresolved template placeholders or comments.

### Helpers

- The summary-job module owns schema validation, state invariants, job-relative path resolution, unique same-directory temporary files, and atomic replacement.
- The external-transcription validation module owns manifest-relative path containment, schema/status checks, digests, transcript validation, and cross-file identity checks.
- `scripts/transcript_output.py` renders only Bilibili native-subtitle transcript Markdown. External ASR transcripts remain JSON in the transcription result directory.
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
- `needs_transcription`: resources are ready, but no valid transcript is attached.
- `prompt_ready`: the transcript source and prompt are valid, and the Agent may write the summary.
- `complete`: source artifacts and final summary passed validation.
- `failed`: a fatal prepare failure after `preparing` was published.

`failed` records only `error.stage`, `error.type`, and `error.message`. It must not store a traceback, cookie, transcript text, or other sensitive payload. Continue failures remain or return to `needs_transcription`; summary validation failures remain `prompt_ready`.

Every published state validates its required fields and artifacts. Job writes use a unique temporary file in the destination directory followed by atomic replace.

## Native Subtitle Branch

The preparation command requests only the selected Bilibili subtitle language group. A cached or downloaded `.srt` is reused only when it parses into non-empty segments. If subtitles were explicitly skipped, no native subtitle is selected even if a valid cached file exists.

A successful native branch writes:

- `transcript.source: "bilibili_subtitle"` and a job-relative transcript JSON path;
- the existing Bilibili transcript Markdown rendering;
- a prompt that links the local Markdown transcript and embeds the summary instructions and selected template;
- `transcription_manifest: null`;
- `status: "prompt_ready"`.

Native subtitle and external ASR transcripts deliberately keep different contracts. The native branch owns Bilibili metadata and Markdown; it is not rewritten to imitate `audio-transcribe`.

## External Transcription Branch

The Bilibili `--language` option is not passed to `audio-transcribe`, and this Skill exposes no transcription-model options. The Agent supplies only the resolved local audio path.

`continue_summary` accepts only an absolute manifest path. Before publishing `prompt_ready`, it verifies:

1. `result_manifest.json` is a schema-1 object with `status: "complete"`.
2. The transcript artifact path is relative, contains no `..` escape, and resolves inside the manifest directory.
3. The recorded transcript SHA-256 matches the file.
4. `transcript.json` has the expected schema and identity, a non-empty segments array, continuous IDs, non-empty text, and finite non-negative monotonic times.
5. Manifest and transcript audio/variant/provider/language identities agree.
6. The transcribed audio SHA-256 and byte size match the job audio.

The ASR prompt references the absolute job, manifest, and transcript paths. It tells the summary writer to read segments in order, combine adjacent short segments only for comprehension, and never rewrite the transcript. It does not use `raw_timestamps.json` or any internal workspace file.

The Bilibili result stores only the manifest reference and its own derived prompt. It never copies, updates, or deletes files in the external transcription directory.

## Completion and Idempotency

Calling continue again with the same manifest revalidates it and returns the existing prompt-ready result. A different manifest cannot replace an already bound valid manifest. Preparation cannot silently overwrite an existing `prompt_ready` or `complete` job.

`complete_summary` reads the expected summary path from the job. For the external branch, it revalidates the manifest, digest, transcript, and audio identity before summary validation. Summary-content validation failure leaves the job `prompt_ready`; invalid external source artifacts trigger the recovery flow below and return it to `needs_transcription`. Success atomically publishes `complete`. Repeating completion on a valid complete job succeeds without rewriting the job.

A complete or prompt-ready job must not be trusted from the status string alone. Callers validate its state invariants and referenced artifacts.

## External Artifact Recovery

If an external manifest or transcript becomes missing, malformed, escaped, digest-mismatched, or identity-incompatible after `prompt_ready`, the Bilibili workflow:

1. hides the invalid success path;
2. deletes only the internal prompt derived from the old external reference;
3. clears `transcript`, `transcription_manifest`, and `prompt`;
4. atomically returns the job to `needs_transcription`.

It preserves any summary the user already wrote and never changes the external transcription directory. The Agent must obtain a new valid transcription result and call continue again before generating or completing a summary.

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
├─ <BVID>_transcript.md         # native subtitle branch only
├─ <BVID>_summary_prompt.md
├─ <BVID>_summary_<language>.md
└─ pipeline-<timestamp>.log
```

External `result_manifest.json`, `transcript.json`, `raw_timestamps.json`, logs, and workspace remain owned by `audio-transcribe` under its own result directory.

## Setup and Logging

The Windows setup launcher prepares Python 3.12, this Skill's dependencies, and packaged `ffmpeg`/`ffprobe`. It does not install transcription models. Setup logs remain under `.cache/logs/`.

Fetch and pipeline logs start under `.cache/logs/` and move into the BVID result directory once known. Logs may contain commands, paths, BVID, state transitions, validation reasons, and tracebacks. They must not contain cookie contents, transcript text, or external model internals. Job `error` fields contain only concise non-traceback summaries.
