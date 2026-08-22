# Architecture

`audio-transcribe` turns one local audio file into a validated, reusable public result. This is a maintainer map of the stable boundaries; implementation details belong in the modules and tests.

## Bird's-eye view

```text
local audio
  -> identity and runtime resolution
  -> Provider candidates and alignment acceptance
  -> merged and normalized workspace result
  -> segmentation and manifest-last publication
  -> validated result_manifest.json
```

The command prints a manifest path only after the complete public result validates. A result is reusable only through that manifest and the `audio-transcribe-contract` loader.

## Codemap

| Area | Responsibility |
| --- | --- |
| `scripts/transcribe.py` | CLI orchestration, audio identity, provider selection, variant identity, locking, cache, and reporting |
| `scripts/asr/alignment.py` | sole alignment core: `AlignmentItem`, `AlignedTranscript`, `CleanupReport`, fixed `ALIGNMENT_POLICY`, acceptance, projection, offset, and validation |
| `scripts/asr/segmentation.py` | sentence segmentation of an already validated global alignment |
| Other `scripts/asr/` modules | audio preparation, VAD, chunk planning, Provider execution, cache, merge, and workspace output |
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
- `variant_id` identifies every resolved behavior that can change transcript bytes or timestamps. The canonical request includes both text-normalization policy and the exact fixed `alignment_policy`.
- `ALIGNMENT_POLICY` is schema v1 with 1 ms timestamp resolution, `drop_item_and_owned_text` zero-duration handling, and strict ordering. A policy change produces a new `variant_id` and ASR plan identity.
- All languages use NFKC and only `zh` additionally uses OpenCC `t2s`, so normalization-policy changes also produce a new `variant_id`.
- A complete manifest is published last and is the only success marker.
- Public artifact paths stay inside the result directory; indexed model shard paths stay inside the model directory.
- Recovery restores the original complete manifest byte-for-byte only when rebuilt public artifacts reproduce its recorded digests. If recovery fails, the original complete manifest remains available for a later retry.
- Public manifest and artifact schemas remain v1, while `request.alignment_policy` is required. Older manifests and private caches that predate the policy/cache-v2 identity are intentionally invalid and are regenerated rather than migrated.

## Cross-cutting concerns

- Atomic writes and the variant lock protect publication and recovery from partial or concurrent updates.
- Contract validation checks schema, identity, path containment, digests, timing, and file types without repairing files.
- Logs contain operational diagnostics but not transcript text, cookies, or model objects; job-facing errors remain concise.
- Provider selection is resolved before inference and is not silently changed after a failure.

## Alignment and validation flow

Provider adapters only map third-party fields into an `AlignedTranscript` candidate. Every candidate then crosses the same `accept_provider_transcript` boundary:

1. reject negative, non-finite, reversed, overlapping, out-of-range, or invalid-probability values;
2. quantize timestamps to three decimal places (1 ms);
3. map source characters to alignment-item owners in source order, allowing unowned punctuation and whitespace without using global text replacement;
4. remove only items whose quantized `start == end`, together with exactly the source characters owned by those items;
5. strictly revalidate character alignment, `0 <= start < end <= duration`, non-overlap, and probability.

One accepted chunk may become empty when only removable items and unowned punctuation or whitespace remain. Merge ignores empty chunks, but a transcription whose merged result is entirely empty fails.

Merge produces only one global `AlignedTranscript`: it applies each chunk offset, combines non-empty accepted chunks, and validates the global result. Text normalization is projected back through item ownership and followed by another strict validation before `workspace/result.json` is written. Publication strictly reads and revalidates the workspace shape and alignment; it neither coerces field types nor clips timestamps to the audio duration. Sentence segmentation runs exactly once during public conversion and consumes the already validated global alignment.

For Qwen3-ASR, alignment-item probability remains `null`. For other accepted probabilities, values must be finite and within `[0, 1]`.

## Private cache v2

The ASR plan and chunk cache use private schema v2 and include the fixed alignment policy in their identity. A chunk is written only after acceptance succeeds. Cache writes do not repeat validation, while every cache read strictly reconstructs and revalidates the accepted transcript. Corrupt, old-schema, or wrong-policy entries are not reused.

Each chunk payload persists a `CleanupReport` containing only the number of dropped zero-duration items and the earliest dropped start/latest dropped end. It never stores removed transcript text. New inference emits one aggregated `WARNING` for each affected chunk. During a partial resume, each affected cached chunk replays that warning once for the new attempt; a complete manifest cache hit, or an all-chunk cache hit, does not emit a new cleanup warning.

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
valid, the pipeline reruns merge, timestamp offset, alignment validation, text and
item normalization, and alignment revalidation before atomically replacing
`result.json`; it does not load the Provider. Segments are not stored in the
workspace. Legacy merged results containing `plan`, `words`, or `segments` are not
read or migrated.

Private `chunk_results/` preserve Provider text. `workspace/result.json` and both public JSON artifacts contain normalized text only. Normalization or post-normalization alignment failure stops publication; recovery deterministically rebuilds from the already-normalized workspace snapshot.

## Public contract and publication

`audio-transcribe-contract` 0.1.2 is deliberately independent of internal alignment modules. Its `load_result()` API is unchanged, but it contains its own copy of the fixed alignment policy and rejects a missing or modified policy before accepting identity. Raw items must have the exact item shape, non-empty text, valid Provider probability, and timing satisfying `0 <= start < end <= duration` and `start >= previous_end`. Identity, canonical request digest, artifact digest, Provider, language, duration, and path containment must agree across the manifest and artifacts.

Publication is manifest-last. It first writes `transcript.json` and `raw_timestamps.json`, then writes `.result_manifest.json.incomplete` and validates that candidate with `load_result()`. Only successful validation permits `os.replace()` to atomically create `result_manifest.json`; candidate failure removes the incomplete file and leaves the formal success marker absent. First publication refuses to overwrite an existing formal manifest.

Recovery temporarily uses `.result_manifest.json.recovery`, distinct from the publication candidate. It rebuilds public artifacts from the strictly validated normalized workspace, requires their digests to match the recorded complete result, and restores the original formal manifest bytes. This naming separation does not change the existing multi-file recovery protocol.
