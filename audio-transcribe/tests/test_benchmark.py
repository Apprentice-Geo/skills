from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark import runner as benchmark
from benchmark import worker as benchmark_worker
from benchmark.metrics import (
    COMPARISON_POLICY,
    compare_reference,
    compare_text,
    edit_distance,
)
from benchmark.prepare_audio import safe_cut
from benchmark.report import summarize
from benchmark.runner import build_matrix
from benchmark.worker import worker

pytestmark = pytest.mark.usefixtures("installed_models")


def test_prepare_audio_module_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "benchmark.prepare_audio", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", ["benchmark", "benchmark.worker"])
def test_benchmark_module_help(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_safe_cut_uses_target_in_silence_and_speech_end_in_speech() -> None:
    intervals = [(100, 200), (300, 400)]

    assert safe_cut(250, intervals, 500) == 250
    assert safe_cut(150, intervals, 500) == 200
    assert safe_cut(200, intervals, 500) == 200
    with pytest.raises(ValueError, match="lookahead"):
        safe_cut(350, intervals, 375)


def test_matrix_alternates_modes_and_keeps_repetitions() -> None:
    matrix = build_matrix(["faster-whisper"], ["zh"], [8], repetitions=3)

    assert [(item["repetition"], item["mode"]) for item in matrix] == [
        (1, "project-slicing"),
        (1, "provider-native"),
        (2, "provider-native"),
        (2, "project-slicing"),
        (3, "project-slicing"),
        (3, "provider-native"),
    ]


def test_worker_directories_do_not_reuse_prior_workspaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(benchmark_worker, "TMP_DIR", tmp_path)

    first = benchmark_worker.fresh_worker_directory("same-run")
    second = benchmark_worker.fresh_worker_directory("same-run")

    assert first.parent == tmp_path
    assert second.parent == tmp_path
    assert first != second


def test_native_whisper_uses_one_worker_and_entire_cpu_budget_for_long_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.asr.execution import WhisperCpuPolicy
    from scripts.runtime_options import TranscribeOptions

    monkeypatch.setattr("scripts.asr.execution.whisper_cpu.os.cpu_count", lambda: 8)
    sample_count = 16_000 * 8 * 60
    expected_project = WhisperCpuPolicy(
        TranscribeOptions(language="zh")
    ).execution_identity(sample_count)

    _adapter, _policy, project, project_configuration = (
        benchmark_worker.provider_runtime(
            "faster-whisper", "zh", sample_count, "project-slicing"
        )
    )
    _adapter, _policy, native, native_configuration = benchmark_worker.provider_runtime(
        "faster-whisper", "zh", sample_count, "provider-native"
    )

    assert project == expected_project
    assert project_configuration["num_workers"] == project["num_workers"]
    assert project_configuration["cpu_threads"] == project["cpu_threads"]
    assert native["cpu_budget"] == project["cpu_budget"] == 6
    assert native["num_workers"] == native_configuration["num_workers"] == 1
    assert native["cpu_threads"] == native_configuration["cpu_threads"] == 6


def test_gpu_identity_is_sorted_and_uses_total_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        benchmark.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="1, GPU B, 24576\n0, GPU A, 8192"
        ),
    )

    assert benchmark._gpus() == [
        {"index": 0, "name": "GPU A", "memory_total_bytes": 8192 * 1024**2},
        {"index": 1, "name": "GPU B", "memory_total_bytes": 24576 * 1024**2},
    ]


def test_gpu_identity_is_empty_without_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(benchmark.subprocess, "run", missing)

    assert benchmark._gpus() == []


def test_persistent_worker_reuses_model_by_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prepared_models: list[object] = []
    used_models: list[object] = []

    class Adapter:
        def prepare(self, _identity):
            model = object()
            prepared_models.append(model)
            return model

    monkeypatch.setattr(
        "scripts.asr.chunking.decode_normalized_audio",
        lambda path: SimpleNamespace(sample_count=1 if "short" in str(path) else 2),
    )
    monkeypatch.setattr(
        benchmark_worker,
        "provider_runtime",
        lambda _provider, _language, sample_count, mode: (
            Adapter(),
            object(),
            {"policy": "test"},
            {"model": "test", "workers": sample_count, "mode": mode},
        ),
    )

    def fake_worker(*_args, prepared_model=None, **_kwargs):
        used_models.append(prepared_model)
        return {
            "status": "succeeded",
            "text": "text",
            "execution_identity": {"policy": "test"},
            "provider_identity": {"provider": "faster-whisper"},
            "provider_stage_seconds": 0.1,
        }

    monkeypatch.setattr(benchmark_worker, "worker", fake_worker)
    requests = [
        {
            "action": action,
            "run": {
                "provider": "faster-whisper",
                "language": language,
                "minutes": 8,
                "mode": mode,
                "repetition": repetition,
            },
            "audio": str(tmp_path / audio),
            "results_dir": str(tmp_path / f"results-{index}"),
        }
        for index, (action, language, repetition, audio, mode) in enumerate(
            [
                ("warmup", "zh", 0, "short.wav", "project-slicing"),
                ("warmup", "en", 0, "short.wav", "project-slicing"),
                ("run", "en", 1, "short.wav", "project-slicing"),
                ("warmup", "en", 0, "short.wav", "provider-native"),
                ("run", "en", 1, "short.wav", "provider-native"),
                ("warmup", "en", 0, "long.wav", "project-slicing"),
                ("run", "en", 1, "long.wav", "project-slicing"),
            ]
        )
    ]
    input_stream = io.StringIO(
        "".join(json.dumps(item) + "\n" for item in requests)
        + json.dumps({"action": "shutdown"})
        + "\n"
    )
    output_stream = io.StringIO()

    assert (
        benchmark_worker.persistent_worker(
            "faster-whisper", input_stream, output_stream
        )
        == 0
    )

    responses = [
        json.loads(line.removeprefix(benchmark_worker.PROTOCOL_PREFIX))
        for line in output_stream.getvalue().splitlines()
    ]
    assert len(prepared_models) == 3
    assert used_models == [
        prepared_models[0],
        prepared_models[0],
        prepared_models[1],
        prepared_models[1],
        prepared_models[2],
        prepared_models[2],
    ]
    assert responses[2]["already_prepared"] is True
    assert all(item["session_id"] == responses[0]["session_id"] for item in responses)


def test_project_slicing_worker_uses_in_memory_pipeline_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path = tmp_path / "manifest.json"
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(
        json.dumps({"segments": [{"text": "transcribed"}]}), encoding="utf-8"
    )
    manifest_path.write_text(
        json.dumps(
            {
                "request": {
                    "execution_policy": {"policy": "test"},
                    "provider_identity": {"provider": "test"},
                },
                "artifacts": {"transcript": transcript_path.name},
            }
        ),
        encoding="utf-8",
    )
    metrics = SimpleNamespace(
        provider_stage_seconds=1.25,
        chunk_count=2,
        batch_count=1,
        hard_cut_count=0,
        max_estimated_speech_duration=3.5,
        speech_load_msre=0.125,
    )
    monkeypatch.setattr(
        "scripts.transcribe.run_transcribe",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest_path=manifest_path,
            pipeline_outcome=SimpleNamespace(metrics=metrics),
        ),
    )

    result = worker(
        tmp_path / "audio.wav",
        "faster-whisper",
        "zh",
        "project-slicing",
        tmp_path,
    )

    assert result["text"] == "transcribed"
    assert result["provider_stage_seconds"] == 1.25
    assert result["chunk_count"] == 2
    assert result["speech_load_msre"] == 0.125


def test_project_slicing_worker_rejects_missing_pipeline_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "scripts.transcribe.run_transcribe",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest_path=tmp_path / "manifest.json",
            pipeline_outcome=None,
        ),
    )

    with pytest.raises(RuntimeError, match="no pipeline diagnostics"):
        worker(
            tmp_path / "audio.wav",
            "faster-whisper",
            "zh",
            "project-slicing",
            tmp_path,
        )


def test_each_provider_warms_immediately_before_its_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, int]] = []

    class FakeSession:
        count = 0

        def __init__(self, provider: str) -> None:
            type(self).count += 1
            self.provider = provider
            self.session_id = f"{self.count:032x}"
            self.alive = True

        def _result(self, run: dict[str, object]) -> dict[str, object]:
            calls.append((str(run["provider"]), int(run["repetition"])))
            model = {
                "repo": "test/repo",
                "revision": "revision",
                "logical_id": "model",
            }
            configuration = (
                {
                    "model": model,
                    "device": "cpu",
                    "compute_type": "float32",
                    "cpu_threads": 1,
                    "num_workers": 1,
                }
                if self.provider == "faster-whisper"
                else {
                    "model": model,
                    "aligner": {
                        "repo": "test/aligner",
                        "revision": "revision",
                        "logical_id": "aligner",
                    },
                    "device": "cuda:0",
                    "dtype": "bfloat16",
                    "batch_size": 1,
                    "max_new_tokens": 1024,
                }
            )
            return {
                **run,
                "status": "succeeded",
                "text": "",
                "execution_identity": {"policy": "test"},
                "provider_identity": {"provider": run["provider"]},
                "model_configuration": configuration,
                "run_id": benchmark.run_id(run),
                "session_id": self.session_id,
                "wall_seconds": 1.0,
                "rtf": 0.1,
                "provider_stage_seconds": 0.5,
            }

        def ensure_warmup(self, run, _sample, _directory):
            return {
                **self._result({**run, "repetition": 0}),
                "already_prepared": False,
            }

        def run(self, run, _sample, _directory):
            return self._result(run)

        def close(self) -> None:
            self.alive = False

    monkeypatch.setattr(benchmark, "WorkerSession", FakeSession)
    monkeypatch.setattr(
        benchmark,
        "load_reference_manifest",
        lambda _path: {
            "manifest_sha256": "0" * 64,
        },
    )
    monkeypatch.setattr(
        benchmark,
        "load_reference_samples",
        lambda *_args, **_kwargs: {
            ("zh", 8): {
                "language": "zh",
                "minutes": 8,
                "text": "参考",
                "units": ["参", "考"],
                "audio_sha256": "1" * 64,
                "reference_sha256": benchmark.unit_digest(["参", "考"]),
            }
        },
    )
    monkeypatch.setattr(
        benchmark,
        "environment",
        lambda: {
            "hardware": {
                "cpu_model": "test",
                "logical_cpu_count": 1,
                "physical_memory_bytes": 1,
                "gpus": [],
            },
            "audit": {
                "platform": "test",
                "python": "test",
                "commit": None,
                "dependencies": None,
                "model_revisions": {},
            },
        },
    )
    monkeypatch.setattr(benchmark, "DATA_DIR", tmp_path / "data")
    args = argparse.Namespace(
        report=tmp_path / "report.json",
        provider=["faster-whisper", "qwen3-asr"],
        language=["zh"],
        minutes=[8],
        mode=["project-slicing"],
        repetitions=1,
    )

    benchmark.run_benchmark(args)

    assert calls == [
        ("faster-whisper", 0),
        ("faster-whisper", 1),
        ("qwen3-asr", 0),
        ("qwen3-asr", 1),
    ]


def test_linear_memory_distance_and_language_normalization() -> None:
    assert edit_distance(list("kitten"), list("sitting")) == 3
    assert compare_text("你 好", "你好", "zh")["difference_rate"] == 0
    assert compare_text("Hello, WORLD!", "hello world", "en")["difference_rate"] == 0
    assert compare_text("", "", "en")["difference_rate"] is None
    comparison = compare_text("Ａ，臺灣！", "A 台湾", "zh")
    assert comparison["difference_rate"] == 0
    assert comparison["project_punctuation"] == 2
    assert comparison["native_punctuation"] == 0


def test_reference_comparison_uses_reference_denominator_and_symmetric_t2s() -> None:
    comparison = compare_reference("甲乙丙", "甲乙", "zh")

    assert comparison["edit_distance"] == 1
    assert comparison["reference_units"] == 2
    assert comparison["error_rate"] == 0.5
    assert compare_reference("臺灣", "台湾", "zh")["error_rate"] == 0
    assert compare_reference("台湾", "臺灣", "zh")["error_rate"] == 0
    english = compare_reference("It's O’Brien", "IT'S O’Brien", "en")
    assert english["error_rate"] == 0
    assert english["reference_units"] == 2
    with pytest.raises(ValueError, match="empty"):
        compare_reference("anything", "...", "en")


def test_summary_pairs_repetitions_and_uses_comparison_median() -> None:
    runs = []
    for repetition, project, native in [
        (1, "甲", "甲"),
        (2, "乙", "甲"),
        (3, "丙。", "甲!"),
    ]:
        comparison = compare_text(project, native, "zh")
        for mode, text in [("project-slicing", project), ("provider-native", native)]:
            run = {
                "status": "succeeded",
                "provider": "faster-whisper",
                "language": "zh",
                "minutes": 8,
                "mode": mode,
                "repetition": repetition,
                "text": text,
                "wall_seconds": 1.0,
                "rtf": 0.1,
                "provider_stage_seconds": 0.5,
                "reference_comparison": compare_reference(text, "甲", "zh"),
            }
            if mode == "project-slicing":
                run["output_comparison"] = comparison
            runs.append(run)

    markdown = summarize(
        {
            "comparison_policy": COMPARISON_POLICY,
            "reference_set": {
                "manifest_sha256": "0" * 64,
                "samples": [
                    {
                        "language": "zh",
                        "minutes": 8,
                        "audio_sha256": "1" * 64,
                        "reference_sha256": compare_reference("甲", "甲", "zh")[
                            "reference_sha256"
                        ],
                    }
                ],
            },
            "environment": {
                "hardware": {
                    "cpu_model": "test cpu",
                    "logical_cpu_count": 8,
                    "physical_memory_bytes": 16 * 1024**3,
                    "gpus": [
                        {
                            "index": 0,
                            "name": "test gpu",
                            "memory_total_bytes": 8 * 1024**3,
                        }
                    ],
                },
                "audit": {
                    "platform": "test",
                    "python": "test",
                    "commit": None,
                    "dependencies": None,
                    "model_revisions": {},
                },
            },
            "runs": runs,
        }
    )

    assert "| zh | 8 | project-slicing" in markdown
    assert "Reference CER/WER" in markdown
    assert "Mode difference" in markdown
    assert "Punctuation (hypothesis/reference)" in markdown
    assert "## Test device" in markdown
    assert "test cpu (8 logical cores)" in markdown
    assert "GPU 0: test gpu (8.00 GiB total)" in markdown
    assert "Memory and GPU-memory usage are not measured or compared" in markdown
    assert "cannot be attributed to the chunk optimizer alone" in markdown
    assert "not an absolute accuracy measure" not in markdown
    assert "100.000%" in markdown
    assert "| zh | 8 | provider-native" in markdown
    assert markdown.count("100.000%") == 2


def test_summary_rejects_success_without_reference_comparison() -> None:
    report = {
        "reference_set": {
            "manifest_sha256": "0" * 64,
            "samples": [
                {
                    "language": "en",
                    "minutes": 8,
                    "audio_sha256": "1" * 64,
                    "reference_sha256": "2" * 64,
                }
            ],
        },
        "runs": [
            {
                "status": "succeeded",
                "provider": "faster-whisper",
                "language": "en",
                "minutes": 8,
                "mode": "provider-native",
            }
        ],
    }

    with pytest.raises(ValueError, match="reference comparison"):
        summarize(report)
