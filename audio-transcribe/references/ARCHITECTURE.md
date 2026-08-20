# Architecture

`audio-transcribe` turns one local audio file into a validated, reusable public result. This is a maintainer map of the stable boundaries; implementation details belong in the modules and tests.

## Bird's-eye view

```text
local audio
  -> identity and runtime resolution
  -> provider pipeline and workspace result
  -> public artifact publication
  -> validated result_manifest.json
```

The command prints a manifest path only after the complete public result validates. A result is reusable only through that manifest and the `audio-transcribe-contract` loader.

## Codemap

| Area | Responsibility |
| --- | --- |
| `scripts/transcribe.py` | CLI orchestration, audio identity, provider selection, variant identity, locking, cache, and reporting |
| `scripts/asr/` | audio preparation, VAD, chunk planning, provider execution, alignment, merge, and workspace output |
| `scripts/artifacts.py` | manifest-last publication, public artifact recovery, locking, and self-validation |
| `scripts/model_artifacts.py` | conservative local model readiness checks, including indexed safetensors |
| `scripts/setup/` | Windows environment and fixed-revision model installation |
| `packages/audio-transcribe-contract/` | strict read-only validation of public manifests and artifacts for consumers |
| `tests/` | behavior and contract regression coverage |

## System boundaries

- Input is a local audio file; this Skill does not download media or own Bilibili workflow.
- `workspace/` is private recovery state. `result_manifest.json`, `transcript.json`, and `raw_timestamps.json` are public artifacts.
- Consumers read the public result through `audio-transcribe-contract`; they do not import this Skill's source or inspect `workspace/`.
- Model setup and readiness are local concerns. A malformed model index reports an unavailable model rather than escaping an exception into setup or dependency checks.

## Stable invariants

- `audio_id` is the SHA-256 of audio bytes and is independent of its path.
- `variant_id` identifies every resolved behavior that can change transcript bytes or timestamps.
- A complete manifest is published last and is the only success marker.
- Public artifact paths stay inside the result directory; indexed model shard paths stay inside the model directory.
- Recovery restores the original complete manifest byte-for-byte only when rebuilt public artifacts reproduce its recorded digests. If recovery fails, the original complete manifest remains available for a later retry.
- Public schemas, successful result structure, and the consumer API shape are unchanged by validation hardening; invalid input is rejected at the boundary.

## Cross-cutting concerns

- Atomic writes and the variant lock protect publication and recovery from partial or concurrent updates.
- Contract validation checks schema, identity, path containment, digests, timing, and file types without repairing files.
- Logs contain operational diagnostics but not transcript text, cookies, or model objects; job-facing errors remain concise.
- Provider selection is resolved before inference and is not silently changed after a failure.

## Public result shape

```text
results/<audio_id>/<provider>-<language>-<variant_id>/
├─ result_manifest.json
├─ transcript.json
├─ raw_timestamps.json
├─ transcribe.log
└─ workspace/result.json
```

The manifest records audio identity, resolved request identity, contained artifact paths, and public artifact digests. Successful consumers receive validated manifest, transcript, and timestamp snapshots from `load_result`; no partial result is returned.

`workspace/result.json` is the pipeline's sole merged result and the only private
recovery snapshot used for publication. It contains exactly `schema_version`,
`text`, `items`, `duration`, `provider`, and `language`. The plan and per-chunk
Provider results remain separate workspace caches. When all chunk caches are
valid, the pipeline always reruns merge, timestamp offset, alignment validation,
and sentence segmentation before atomically replacing `result.json`; it does not
load the Provider. Legacy merged results containing `plan`, `words`, or `segments`
are not read or migrated.
