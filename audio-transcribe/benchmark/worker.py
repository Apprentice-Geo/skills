from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path
from typing import Any, TextIO

from benchmark import PROVIDERS, ROOT, TMP_DIR
from benchmark.report import run_id
from scripts.io_utils import read_json

PROTOCOL_PREFIX = "@@benchmark-worker@@"


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16_000:
            raise ValueError(f"Benchmark sample must be mono 16 kHz PCM WAV: {path}")
        return stream.getnframes() / stream.getframerate()


def fresh_worker_directory(label: str) -> Path:
    return TMP_DIR / f"{label}-{uuid.uuid4().hex}"


def transcript_text(manifest_path: Path) -> str:
    manifest = read_json(manifest_path)
    transcript = read_json(manifest_path.parent / manifest["artifacts"]["transcript"])
    return "".join(str(segment["text"]) for segment in transcript["segments"])


def provider_runtime(
    provider: str, language: str, sample_count: int, mode: str
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    from scripts.asr.execution import Qwen3AsrCudaPolicy, WhisperCpuPolicy
    from scripts.asr.providers import Qwen3AsrProvider, WhisperProvider
    from scripts.runtime_options import TranscribeOptions

    if provider == "faster-whisper":
        options = TranscribeOptions(language=language)
        adapter = WhisperProvider(options)
        policy = WhisperCpuPolicy(options)
        identity = policy.execution_identity(sample_count)
        if mode == "provider-native":
            identity = {
                **identity,
                "num_workers": 1,
                "cpu_threads": identity["cpu_budget"],
                "group_size": 1,
            }
        request = adapter.request_identity()
        configuration = {
            "model": request["model"],
            "device": request["device"],
            "compute_type": request["compute_type"],
            "cpu_threads": identity["cpu_threads"],
            "num_workers": identity["num_workers"],
        }
    else:
        adapter = Qwen3AsrProvider(language)
        policy = Qwen3AsrCudaPolicy()
        identity = policy.execution_identity(sample_count)
        request = adapter.request_identity()
        model = request["model"]
        configuration = {
            "model": {key: model[key] for key in ("repo", "revision", "logical_id")},
            "aligner": {
                "repo": model["aligner_repo"],
                "revision": model["aligner_revision"],
                "logical_id": model["aligner_logical_id"],
            },
            "device": request["device"],
            "dtype": request["compute_type"],
            "batch_size": identity["batch_size"],
        }
    return adapter, policy, identity, configuration


def _configuration_key(configuration: dict[str, Any]) -> str:
    return json.dumps(
        configuration, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def worker(
    audio: Path,
    provider: str,
    language: str,
    mode: str,
    results_dir: Path,
    *,
    prepared_model: Any | None = None,
) -> dict[str, Any]:
    from scripts.asr.chunking import ChunkLayout, decode_normalized_audio
    from scripts.transcribe import run_transcribe

    audio = audio.resolve()
    started = time.perf_counter()
    if mode == "project-slicing":
        outcome = run_transcribe(
            audio,
            language=language,
            provider=provider,
            results_dir=results_dir,
            prepared_model=prepared_model,
        )
        manifest = outcome.manifest_path
        if outcome.pipeline_outcome is None:
            raise RuntimeError(
                "Fresh project-slicing benchmark run produced no pipeline diagnostics"
            )
        metrics = outcome.pipeline_outcome.metrics
        request = read_json(manifest)["request"]
        text = transcript_text(manifest)
        identity = request["execution_policy"]
        provider_identity = request["provider_identity"]
        details = {
            key: getattr(metrics, key)
            for key in (
                "provider_stage_seconds",
                "chunk_count",
                "batch_count",
                "hard_cut_count",
                "max_estimated_speech_duration",
                "speech_load_msre",
            )
        }
    else:
        samples = decode_normalized_audio(audio)
        adapter, _policy, identity, _configuration = provider_runtime(
            provider, language, samples.sample_count, mode
        )
        provider_identity = adapter.request_identity()
        layout = ChunkLayout(0, 0, samples.sample_count, "source", samples.sample_count)
        provider_started = time.perf_counter()
        model = (
            prepared_model if prepared_model is not None else adapter.prepare(identity)
        )
        transcript = adapter.transcribe_one(model, samples.samples, layout)
        text = transcript.text
        details = {
            "provider_stage_seconds": time.perf_counter() - provider_started,
            "chunk_count": None,
            "batch_count": None,
            "hard_cut_count": None,
            "provider_native_internal_chunking": "qwen-asr 0.0.6 timestamps: up to 180 seconds"
            if provider == "qwen3-asr"
            else "single provider input with faster-whisper native VAD",
        }
    return {
        "status": "succeeded",
        "worker_seconds": time.perf_counter() - started,
        "text": text,
        "execution_identity": identity,
        "provider_identity": provider_identity,
        **details,
    }


class WorkerSession:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self._protocol_failed = False
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "benchmark.worker",
                "--persistent-provider",
                provider,
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        if self.process.stdout is None:
            raise RuntimeError("Benchmark worker stdout is unavailable")
        try:
            message = self._read_protocol_message()
            self.session_id = str(message["session_id"])
        except (KeyError, OSError, TypeError, ValueError) as exc:
            self.close()
            raise RuntimeError("Benchmark worker failed to start") from exc

    @property
    def alive(self) -> bool:
        return not self._protocol_failed and self.process.poll() is None

    def _read_protocol_message(self) -> dict[str, Any]:
        if self.process.stdout is None:
            raise BrokenPipeError("worker stdout is unavailable")
        for line in self.process.stdout:
            if line.startswith(PROTOCOL_PREFIX):
                candidate = json.loads(line[len(PROTOCOL_PREFIX) :])
                if not isinstance(candidate, dict):
                    raise ValueError("worker response is not an object")
                return candidate
            print(line, end="", file=sys.stderr)
        raise BrokenPipeError("worker exited without a response")

    def _request(
        self,
        action: str,
        run: dict[str, Any],
        sample: Path,
        directory: Path,
    ) -> dict[str, Any]:
        directory.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        request = {
            "action": action,
            "run": run,
            "audio": str(sample.resolve()),
            "results_dir": str((directory / "results").resolve()),
        }
        try:
            if self.process.stdin is None or self.process.stdout is None:
                raise BrokenPipeError("worker pipes are unavailable")
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            response = self._read_protocol_message()
        except (BrokenPipeError, OSError, json.JSONDecodeError, ValueError) as exc:
            self._protocol_failed = True
            response = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        wall_seconds = time.perf_counter() - started
        duration = wav_duration(sample)
        return {
            **run,
            **response,
            "run_id": run_id(run),
            "session_id": self.session_id,
            "wall_seconds": wall_seconds,
            "audio_seconds": duration,
            "rtf": wall_seconds / duration,
        }

    def ensure_warmup(
        self, run: dict[str, Any], sample: Path, directory: Path
    ) -> dict[str, Any]:
        return self._request("warmup", {**run, "repetition": 0}, sample, directory)

    def run(self, run: dict[str, Any], sample: Path, directory: Path) -> dict[str, Any]:
        return self._request("run", run, sample, directory)

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                if self.process.stdin is not None:
                    self.process.stdin.write('{"action":"shutdown"}\n')
                    self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)

    def __enter__(self) -> WorkerSession:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _write_protocol(stream: TextIO, value: dict[str, Any]) -> None:
    stream.write(PROTOCOL_PREFIX + json.dumps(value, ensure_ascii=False) + "\n")
    stream.flush()


def persistent_worker(
    provider: str, input_stream: TextIO, output_stream: TextIO
) -> int:
    from scripts.asr.chunking import decode_normalized_audio

    session_id = uuid.uuid4().hex
    models: dict[str, Any] = {}
    _write_protocol(output_stream, {"session_id": session_id})
    for line in input_stream:
        try:
            request = json.loads(line)
            if request.get("action") == "shutdown":
                return 0
            run = request["run"]
            if run["provider"] != provider:
                raise ValueError("Worker request provider does not match its session")
            audio = Path(request["audio"])
            results_dir = Path(request["results_dir"])
            samples = decode_normalized_audio(audio)
            adapter, _policy, identity, configuration = provider_runtime(
                provider, run["language"], samples.sample_count, run["mode"]
            )
            key = _configuration_key(configuration)
            action = request.get("action")
            if action == "warmup" and key in models:
                result = {
                    "status": "succeeded",
                    "already_prepared": True,
                    "model_configuration": configuration,
                }
            elif action == "warmup":
                prepared = adapter.prepare(identity)
                result = worker(
                    audio,
                    provider,
                    run["language"],
                    run["mode"],
                    results_dir,
                    prepared_model=prepared,
                )
                models[key] = prepared
                result["already_prepared"] = False
                result["model_configuration"] = configuration
            elif action == "run":
                if key not in models:
                    raise RuntimeError("Benchmark model configuration was not warmed")
                result = worker(
                    audio,
                    provider,
                    run["language"],
                    run["mode"],
                    results_dir,
                    prepared_model=models[key],
                )
                result["model_configuration"] = configuration
            else:
                raise ValueError("Unknown benchmark worker action")
        except Exception as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        result["session_id"] = session_id
        _write_protocol(output_stream, result)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a persistent benchmark worker.")
    parser.add_argument("--persistent-provider", choices=PROVIDERS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.persistent_provider is None:
        return 0
    return persistent_worker(args.persistent_provider, sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
