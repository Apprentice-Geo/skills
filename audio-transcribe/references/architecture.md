# Architecture

This document describes the current transcription pipeline, result identity, cache, and public artifact contracts for maintainers.

## Workflow

```text
local audio path
  -> resolve the input path and hash audio bytes
  -> decode audio to 16 kHz samples
  -> run VAD over normalized audio
  -> detect language from up to 30 seconds of VAD-ordered speech unless language is specified
  -> resolve the requested or ready Provider
  -> resolve model, execution, VAD, planning, and segmentation identity
  -> compute variant_id from canonical resolved request JSON
  -> enter results/<audio_id>/<provider>-<language>-<variant_id>/
  -> hold the variant lock
     -> validate or recover an existing complete result when possible
     -> otherwise run the ASR pipeline into workspace/result.json
     -> publish transcript.json and raw_timestamps.json
     -> publish result_manifest.json last
```

The command prints the absolute `result_manifest.json` path only after a complete manifest validates successfully.

## Script Responsibilities

### Entry Points

- `scripts/transcribe.py`: owns CLI parsing, local audio identity, language resolution, Provider selection, execution identity, variant directory selection, locking, cache reuse, and final manifest reporting.
- `scripts/benchmark.py`: runs local benchmark scenarios against the transcription command and records timing-oriented results.

### Pipeline

- `scripts/asr/pipeline.py`: coordinates prepared audio, VAD, chunk planning, Provider execution, chunk result validation, merge, and workspace publication.
- `scripts/asr/pipeline_types.py`: defines source identity, pipeline plans, and chunk transcript contracts.
- `scripts/asr/providers/base.py`: defines the Provider interface used by the pipeline.
- `scripts/asr/providers/whisper.py`: adapts faster-whisper output into the internal chunk transcript contract.
- `scripts/asr/providers/qwen3.py`: adapts Qwen3 ASR and forced-aligner output into the internal chunk transcript contract.
- `scripts/asr/execution/whisper_cpu.py`: owns faster-whisper CPU execution identity and worker/thread planning.
- `scripts/asr/execution/qwen3_cuda.py`: owns Qwen3 CUDA execution identity and batch policy.
- `scripts/asr/chunking/*`: owns normalized audio, VAD parameters, speech timelines, chunk layout validation, planning, and chunk audio extraction.
- `scripts/asr/alignment.py`: validates Provider text and timestamp alignment at chunk level.
- `scripts/asr/merge.py`: merges ordered chunk transcripts into one workspace result.

### Public Artifacts and Helpers

- `packages/audio-transcribe-contract/`: owns strict public result reading. Its `_types.py`, `_validation.py`, and `_loader.py` modules define contract types, validate schema and identity including path containment and SHA-256 checks, and orchestrate loading, respectively.
- `scripts/artifacts.py`: owns variant locking, manifest-last publication, and digest-preserving recovery, then uses the contract package for self-validation.
- `scripts/alignment.py`: builds public sentence segments from normalized alignment items. Punctuation boundaries are `，,；;。.!！？?`.
- `scripts/io_utils.py`: provides canonical JSON, atomic file writes, JSON reads, and SHA-256 helpers.
- `scripts/model_identity.py`: stores fixed upstream model revision identity used in `variant_id`.
- `scripts/model_artifacts.py`: checks local model artifacts before setup and transcription workflows use them.
- `scripts/process_logging.py`: provides file logging, concise terminal filtering, and known noisy-warning suppression.
- `scripts/runtime_options.py`: defines transcription runtime options.
- `scripts/config.py`: contains local path and result-directory configuration.
- `scripts/setup/*`: prepares the Windows environment and installs fixed-revision transcription models.

## Result Identity

`audio_id` is the SHA-256 digest of the input audio bytes. It is independent of the input filename and path, so renaming or moving the same audio reuses the same audio result root.

`variant_id` is the SHA-256 digest of the canonical resolved request JSON. The request includes:

- Provider value: `faster-whisper` or `qwen3-asr`;
- resolved language;
- fixed Provider model identity and upstream revision;
- execution policy;
- VAD parameters;
- planning parameters;
- segmentation schema version.

The request excludes invocation-only data such as input path, output path, and log level. Any change that can affect transcript bytes, timestamps, or segmentation must be represented in the request identity before inference starts.

## Result Directory

A complete variant has this shape:

```text
results/<audio_id>/
└─ <provider>-<language>-<variant_id>/
   ├─ result_manifest.json
   ├─ transcript.json
   ├─ raw_timestamps.json
   ├─ transcribe.log
   └─ workspace/
      └─ result.json
```

The `workspace/` directory is internal cache and recovery state. `result_manifest.json` is the public entrypoint. Consumers use `audio_transcribe_contract.load_result` before reading transcript data.

## Public Artifact Contract

The zero-runtime-dependency `audio-transcribe-contract` package exports `ResultManifest`, `Transcript`, `RawTimestamps`, `ResultValidationError`, the frozen `TranscriptionResult`, and `load_result(path)`. A successful load returns validated in-memory manifest, transcript, and raw timestamp snapshots plus their absolute public paths. The log and workspace are checked for existence but are not exposed as readable result fields.

`result_manifest.json` uses schema version 1 and must have `status: "complete"`. It records:

- `audio`: `id`, byte `size`, `sample_count`, `sample_rate`, and `duration`;
- `request`: the resolved request plus `variant_id`;
- `artifacts`: manifest-relative paths for transcript, raw timestamps, log, and workspace;
- `artifact_sha256`: SHA-256 digests for public transcript artifacts.

`transcript.json` uses schema version 1 and contains ordered sentence segments. Segment IDs are continuous from zero. Segment text is non-empty. Segments do not overlap, and each segment satisfies `0 <= start < end <= duration`.

`raw_timestamps.json` uses schema version 1 and contains standardized alignment items with exactly `text`, `start`, `end`, and `probability`. Item text is non-empty. Item times are finite, non-negative, and monotonic. Qwen3 items always use `probability: null`.

Provider text is preserved in public artifacts. The publication path does not run OpenCC, simplification, rewriting, or other text normalization over transcript text.

`load_result` rejects non-object or non-strict JSON, duplicate keys, non-finite numbers, invalid lowercase SHA-256 identities, non-canonical `variant_id`, path and symlink escapes, wrong file types, digest mismatches, cross-file identity mismatches, and invalid segment or timestamp ordering. It never repairs or modifies artifacts, returns no partial result, and does not include transcript text in validation errors.

## Cache and Publication

Each variant directory is protected by `.variant.lock` while checking, recovering, running inference, and publishing.

When `result_manifest.json` already exists, the command validates the manifest and its public artifacts. A valid cache hit returns the existing manifest path and does not load an ASR model, rerun inference, merge chunks, or rewrite the first successful `transcribe.log`.

If the complete manifest exists but a public artifact is missing or invalid, recovery hides the manifest, rebuilds public artifacts from `workspace/result.json`, and restores the original manifest byte-for-byte only when the rebuilt SHA-256 digests exactly match the published digests. If recovery cannot reproduce the published bytes, no complete entry remains.

For a new result, `transcript.json` and `raw_timestamps.json` are written first. `result_manifest.json` is written last and is the only success marker.

## Provider Resolution

If the user passes `--provider`, that Provider must be supported and ready for the resolved language and runtime environment. If the user omits `--provider`, Qwen3 is selected only when the resolved language is supported and the CUDA model environment is ready; otherwise faster-whisper is selected when ready.

Once a Provider is resolved, later loading, inference, alignment, merge, artifact, or publication failures stop the run. The command does not silently switch Providers after resolution, because doing so would change result identity and reproducibility.

The public Qwen3 identifier changed incompatibly from `qwen3` to `qwen3-asr`. Since Provider identity contributes to `variant_id` and the result directory name, new runs intentionally do not reuse old `qwen3` cache entries. The `qwen3` extra, model installation argument, model directories, internal module names, and `qwen3-cuda` execution policy remain unchanged.

## Logging

The first successful run writes `transcribe.log` in the variant directory. Cache hits keep that log unchanged. Failed attempts may leave diagnostic logs, but the first successful publication replaces prior failed-attempt content for that variant.

Terminal output is concise. Known noisy third-party warnings may be suppressed only by exact logger/message-prefix filters. Real exceptions remain visible through the failed command output and log traceback.
