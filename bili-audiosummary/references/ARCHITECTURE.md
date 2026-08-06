# Architecture

`bili-audiosummary` prepares Bilibili resources, turns either native subtitles or an external transcription into a job-local Markdown input, and validates the final summary. Local ASR internals belong to `audio-transcribe`.

## Bird's-eye view

```text
Bilibili URL
  -> resources and summary_job.json
  -> native subtitles OR external complete result_manifest.json
  -> validated job-local transcript.md and prompt
  -> agent-written summary
  -> validated complete job
```

The cross-Skill boundary is a local audio path going out and an absolute public transcription manifest coming back. This Skill consumes only the published `audio-transcribe-contract` API.

## Codemap

| Area | Responsibility |
| --- | --- |
| `scripts/run_pipeline.py` | preparation orchestration and native/transcription branch selection |
| `scripts/fetch_audio.py` | canonical metadata, subtitles, audio, and fetch manifest |
| `scripts/continue_summary.py` | external manifest validation, audio identity matching, transcript rendering, and prompt publication |
| `scripts/subtitle_transcript.py` | native SRT parsing and transcript conversion |
| `scripts/transcript_output.py` | common segment validation, merging, and Markdown rendering |
| `scripts/complete_summary.py` and `validate_summary.py` | final summary and source validation |
| `scripts/summary_job.py` | schema, state invariants, bounded paths, locking, and atomic job writes |
| `assets/` | summary instructions and templates |

## System boundaries

- Bilibili acquisition and summary-job state belong here; ASR models, providers, caches, and workspaces do not.
- External transcription directories are read-only from this Skill. The job stores an absolute manifest reference and a job-local rendered Markdown copy.
- Native and external transcript JSON retain their own contracts; both are adapted into the same job-local `transcript.md`.
- Prompt and transcript content are untrusted source data and cannot override the summary task, output path, or embedded instructions.

## Stable job contract

The fixed top-level shape is `schema_version`, `status`, `video`, `resources`, `transcript`, `transcription_manifest`, `prompt`, and `error`. Stable statuses are `preparing`, `needs_transcription`, `prompt_ready`, `complete`, and `failed`. Internal paths are job-relative and bounded; the external transcription manifest is absolute.

Preparation chooses one of these branches:

- A usable selected-language subtitle produces a native transcript, Markdown, prompt, and `prompt_ready`.
- Otherwise the job becomes `needs_transcription`; an Agent runs `audio-transcribe` and passes the absolute complete manifest to `continue_summary`.

`continue_summary` validates the external manifest, artifact paths, digests, transcript contract, and the job audio SHA-256 before publishing local transcript or prompt artifacts. For an already bound transcription job, repeating continue with the same manifest revalidates that external manifest, audio identity, and the expected rendered Markdown before refreshing the local prompt. A different manifest is rejected.

`complete_summary` validates the applicable source and final summary, then atomically publishes `complete`. Continue failures leave `needs_transcription` when prompt publication has not succeeded; summary failures leave `prompt_ready`.

## Invariants and cross-cutting concerns

- Every published job satisfies its state-specific schema and path invariants.
- Atomic replacement and a job lock prevent partial or concurrent state publication.
- The prompt references only job-local `transcript.md` and the expected summary path.
- Logs carry operational diagnostics, while job errors exclude tracebacks, cookies, and transcript text.
- Public contract schemas and successful result shapes are unchanged; validation is stricter only at invalid or inconsistent input boundaries.

## Generated artifacts

```text
results/<BVID>/
├─ summary_job.json
├─ resource/{audio,fetch_manifest.json,metadata.json,subtitle/}
├─ <BVID>_transcript.json   # native branch only
├─ transcript.md
├─ <BVID>_summary_prompt.md
└─ <BVID>_summary_<language>.md
```

The external `result_manifest.json`, transcript JSON, timestamps, logs, and workspace remain owned by `audio-transcribe`.
