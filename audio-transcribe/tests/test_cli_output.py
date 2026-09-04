from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts import benchmark, transcribe
from scripts.process_logging import filtered_log_messages


@pytest.mark.parametrize(
    ("logger_name", "message"),
    [
        (
            "speechbrain.dataio.encoder",
            "CategoricalEncoder.expect_len was never called: continuing",
        ),
        (
            "transformers.generation.utils",
            "Setting `pad_token_id` to `eos_token_id`:123 for open-end generation.",
        ),
    ],
)
def test_filtered_log_messages_suppresses_only_target_prefix(
    logger_name: str,
    message: str,
) -> None:
    logger = logging.getLogger(logger_name)
    records: list[logging.LogRecord] = []

    class CollectingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = CollectingHandler()
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    try:
        logger.handlers[:] = [handler]
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        with filtered_log_messages():
            logger.warning(message)
            logger.warning("A different warning")
        logger.warning(message)
    finally:
        logger.handlers[:] = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    assert [record.getMessage() for record in records] == [
        "A different warning",
        message,
    ]


@pytest.mark.parametrize(
    ("elapsed", "formatted"),
    [
        (12.34, "12.34s"),
        (123.45, "2m 03.45s"),
        (3723.45, "1h 2m 03.45s"),
    ],
)
def test_main_reports_elapsed_time_before_manifest(
    elapsed: float,
    formatted: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = Path("result_manifest.json")
    times = iter((10.0, 10.0 + elapsed))
    monkeypatch.setattr(transcribe.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(
        transcribe,
        "run_transcribe",
        lambda *_args, **_kwargs: transcribe.TranscribeOutcome(manifest, None),
    )

    assert transcribe.main(["audio.wav"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"[Stage] Transcribe completed in {formatted}",
        f"result_manifest: {manifest}",
    ]


def test_main_does_not_report_completion_when_transcription_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(transcribe.time, "perf_counter", lambda: 10.0)

    def fail(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(transcribe, "run_transcribe", fail)

    assert transcribe.main(["audio.wav"]) == 1

    captured = capsys.readouterr()
    assert "Transcribe completed" not in captured.out
    assert "Transcription failed: boom" in captured.err


def test_transcribe_parse_args_uses_provider() -> None:
    args = transcribe.parse_args(["audio.wav", "--provider", "qwen3-asr"])

    assert args.provider == "qwen3-asr"
    assert not hasattr(args, "model")


def test_transcribe_parse_args_rejects_old_qwen_provider() -> None:
    with pytest.raises(SystemExit):
        transcribe.parse_args(["audio.wav", "--provider", "qwen3"])


def test_transcribe_parse_args_rejects_model_provider_alias() -> None:
    with pytest.raises(SystemExit):
        transcribe.parse_args(["audio.wav", "--model", "qwen3"])


def test_benchmark_parse_args_uses_provider() -> None:
    args = benchmark.parse_args(["--provider", "faster-whisper"])

    assert args.provider == ["faster-whisper"]
    assert not hasattr(args, "model")


def test_benchmark_parse_args_rejects_model_provider_alias() -> None:
    with pytest.raises(SystemExit):
        benchmark.parse_args(["audio.wav", "--model", "qwen3"])


def test_benchmark_defaults_to_three_repetitions() -> None:
    args = benchmark.parse_args([])

    assert args.repetitions == 3
