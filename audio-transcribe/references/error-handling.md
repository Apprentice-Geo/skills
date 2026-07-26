# Error Handling

Use this reference when local audio transcription, Provider execution, cache reuse, or public artifact validation fails.

## Quick Index

- [Setup and Dependencies](#setup-and-dependencies)
- [Model Installation](#model-installation)
- [Input Audio and Decode](#input-audio-and-decode)
- [VAD and Language Detection](#vad-and-language-detection)
- [Provider Selection](#provider-selection)
- [Execution Policy](#execution-policy)
- [Model Loading and Inference](#model-loading-and-inference)
- [Alignment and Empty Results](#alignment-and-empty-results)
- [Public Artifact Validation](#public-artifact-validation)
- [Cache Recovery](#cache-recovery)
- [Logs](#logs)
- [Stop Conditions](#stop-conditions)

## Setup and Dependencies

- Run setup from this Skill directory with `.\scripts\setup\setup_windows.bat`.
- Use Python 3.12 and `uv`; do not repair or replace an existing `.venv` without explicit approval.
- Use `uv run --no-sync python` for transcription and setup subcommands after dependencies are installed.
- If dependency sync fails, inspect the setup log, `pyproject.toml`, and `uv.lock` before retrying.
- Packaged ffmpeg support comes through the project setup. If decode fails because ffmpeg cannot be found or imported dependencies are missing, rerun or repair setup rather than relying on an unrelated system install.

## Model Installation

Install at least one local transcription model before transcription:

```powershell
uv run --no-sync python -m scripts.setup.install_model --model faster-whisper
```

For Qwen3, first install optional dependencies and ensure CUDA is available:

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

If fixed-revision model artifacts are missing, incomplete, or from the wrong revision, reinstall the requested model. Do not invent revision values or treat a partially downloaded model directory as ready.

The language-identification model is required when `--language` is omitted. If it is missing, either install the model through setup or rerun with an explicit `--language`.

## Input Audio and Decode

- If the input path does not exist or is not a file, stop and ask for a valid local audio path.
- Do not replace a missing local file with a downloaded copy or another media source.
- If decode returns no samples, stop. Empty decoded audio must not publish a complete manifest.
- If decode fails, inspect whether faster-whisper audio decoding dependencies and packaged ffmpeg are available.
- Audio identity is based on file bytes. Do not assume two paths are the same input from filename, title, or directory alone.

## VAD and Language Detection

- VAD must produce usable speech before automatic language detection can run.
- Automatic language detection uses up to 30 seconds of VAD-ordered speech. Do not run language detection over arbitrary silence or unrelated audio.
- If language detection returns an empty or invalid language, stop.
- If language confidence is low, the command may continue with the highest-scoring language and should log the warning.
- If the user supplied `--language`, use that resolved language instead of guessing or correcting it.

## Provider Selection

- Supported public Providers are `faster-whisper` and `qwen3`.
- If `--provider` names an unsupported Provider, stop.
- If `--provider qwen3` is used with an unsupported language, stop and report the supported language set.
- If no Provider is specified, select only from Providers that are ready in the current environment.
- If no Provider is ready, stop and ask the user to install Qwen3 or faster-whisper.
- Once a Provider is resolved, do not silently switch Providers after a loading, inference, alignment, or artifact failure.

## Execution Policy

- faster-whisper runs under the CPU policy. `--num-workers` and `--cpu-threads` must be positive integers when provided.
- The faster-whisper worker/thread product must fit within the computed CPU budget. If it exceeds the budget, reduce workers or threads and rerun.
- Qwen3 runs under the CUDA policy and requires available CUDA plus local ASR and forced-aligner model artifacts.
- Execution policy values are part of `variant_id`. Do not edit a manifest to pretend that a failed run used a different policy.

## Model Loading and Inference

- If faster-whisper is not installed or its local `model.bin` is missing, install or repair faster-whisper before retrying.
- If Qwen3 dependencies, CUDA, ASR weights, or forced-aligner weights are missing, install or repair Qwen3 before retrying.
- If a Provider returns an unexpected result shape, inspect the pinned dependency version and the adapter before changing the public artifact schema.
- Do not store third-party model objects, raw Provider responses, or large internal metadata in public artifacts.
- Do not rewrite Provider text during inference recovery. Public transcript text remains Provider output.

## Alignment and Empty Results

- A complete transcription must contain non-empty text and timestamp items.
- Reject timestamps with negative, non-finite, overlapping, or decreasing times.
- Reject empty timestamp item text.
- Reject Qwen3 timestamp items with non-null probability.
- Preserve punctuation-driven segmentation behavior. If sentence output looks wrong, inspect alignment items and segmentation rules rather than editing published `transcript.json` manually.
- Do not publish `result_manifest.json` when alignment validation fails.

## Public Artifact Validation

Before using a result, validate `result_manifest.json`:

1. schema version is 1 and status is `complete`;
2. `audio.id` and `request.variant_id` are 64-character SHA-256 values;
3. `variant_id` matches the canonical request JSON excluding `variant_id`;
4. artifact paths are relative to the manifest directory and cannot escape it;
5. transcript and raw timestamp files exist and match manifest SHA-256 digests;
6. transcript and raw timestamp schema, identity, Provider, language, duration, and timing contracts are valid;
7. log exists and workspace directory exists.

Relative artifact paths, absolute artifact paths, `..` escapes, wrong schema, non-complete status, digest mismatch, invalid identities, missing logs, or missing workspace make the result unsafe.

Do not manually repair public JSON. Rerun the command and let the artifact layer validate or recover the result.

## Cache Recovery

If a complete manifest exists, the command first validates it. A valid cache hit returns the existing manifest path and does not rerun inference.

If public artifacts are missing or corrupted, recovery is allowed only when `workspace/result.json` can rebuild `transcript.json` and `raw_timestamps.json` with exactly the SHA-256 digests recorded in the hidden manifest. Successful recovery restores the original manifest byte-for-byte.

If workspace reconstruction fails or produces different digests:

- no complete manifest remains;
- treat the variant as incomplete;
- rerun transcription after fixing the underlying cause;
- do not copy files from another variant or edit digests to match new bytes.

The variant lock protects validation, recovery, inference, and publication from concurrent writers. If a process appears stuck on the lock, inspect running transcription processes before deleting any lock-related file.

## Logs

- A successful CLI prints elapsed time and the absolute `result_manifest.json` path.
- Do not claim success from partial files, workspace files, or a result directory name.
- On failure, the CLI prints `Transcription failed: ...` and exits non-zero.
- When reporting a failure, include the exact command, concise error, result or log path if available, and whether a complete manifest exists.
- Do not include transcript text, cookie contents from other workflows, raw model objects, or unnecessary sensitive local paths in reports.
- Known noisy third-party warnings may be filtered by exact logger/message-prefix filters. Do not suppress unknown warnings or exceptions.
- Cache hits must not rewrite the first successful `transcribe.log`.

## Stop Conditions

Stop without producing or using a transcript when:

- the input local audio file is missing;
- decode fails or decoded audio is empty;
- automatic language detection is required but the language-id model or usable speech is unavailable;
- the resolved language is empty or invalid;
- the requested Provider is unsupported or not ready;
- Qwen3 is requested without CUDA or required local model artifacts;
- faster-whisper is requested without required dependencies or local model artifacts;
- CPU worker/thread options are invalid or exceed budget;
- Provider inference fails;
- alignment validation fails;
- text or timestamp items are empty;
- public artifact validation fails and recovery cannot reproduce the published digests;
- no complete `result_manifest.json` validates successfully.

Never bypass a stop condition by editing `result_manifest.json`, `transcript.json`, `raw_timestamps.json`, or files under `workspace/`.
