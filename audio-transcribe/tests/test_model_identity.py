from __future__ import annotations

import json
from copy import deepcopy

import pytest
from result_fixtures import resolved_request

from scripts import transcribe
from scripts.asr.prepared_model import PreparedModel, validate_prepared_model
from scripts.asr.providers import Qwen3AsrProvider, WhisperProvider
from scripts.model_identity import MODEL_REVISIONS, validate_model
from scripts.runtime_options import TranscribeOptions


@pytest.mark.parametrize("provider", ["faster-whisper", "qwen3-asr"])
@pytest.mark.parametrize(
    "marker",
    [
        None,
        "{",
        "[]",
        '{"repo": 1, "revision": true}',
        '{"repo":"wrong","revision":"wrong"}',
        "extra",
        "duplicate",
    ],
)
def test_identity_and_prepare_reject_invalid_installation(
    installed_models, provider, marker
):
    directory = installed_models / MODEL_REVISIONS[provider]["logical_id"]
    path = directory / ".model_identity.json"
    if marker is None:
        path.unlink()
    elif marker == "extra":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["extra"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif marker == "duplicate":
        path.write_text(
            path.read_text(encoding="utf-8").replace("{", '{"repo":"old",', 1),
            encoding="utf-8",
        )
    else:
        path.write_text(marker, encoding="utf-8")
    adapter = (
        WhisperProvider(TranscribeOptions(language="en"))
        if provider == "faster-whisper"
        else Qwen3AsrProvider("en")
    )
    with pytest.raises(RuntimeError, match="marker"):
        adapter.request_identity()
    with pytest.raises(RuntimeError, match="marker"):
        adapter.prepare({})


def test_aligner_checked_independently(installed_models):
    (installed_models / "qwen3-forcedaligner-0.6b" / ".model_identity.json").unlink()
    with pytest.raises(RuntimeError, match="ForcedAligner"):
        Qwen3AsrProvider("en").request_identity()


def test_custom_path_requires_matching_installation(installed_models, tmp_path):
    good = installed_models / "faster-whisper-small"
    adapter = WhisperProvider(TranscribeOptions(language="en", model_path=str(good)))
    assert adapter.request_identity()["model"] == MODEL_REVISIONS["faster-whisper"]
    wrong = WhisperProvider(TranscribeOptions(language="en", model_path=str(tmp_path)))
    with pytest.raises(RuntimeError, match="marker"):
        wrong.request_identity()


def test_empty_weight_is_not_ready(installed_models):
    directory = installed_models / "faster-whisper-small"
    (directory / "model.bin").write_bytes(b"")
    with pytest.raises(RuntimeError, match="empty"):
        validate_model("faster-whisper", directory)


def test_auto_selection_excludes_invalid_candidate(installed_models, monkeypatch):
    monkeypatch.setattr(transcribe.importlib.util, "find_spec", lambda _name: object())
    (installed_models / "qwen3-asr-0.6b" / ".model_identity.json").unlink()
    assert transcribe._select_provider(None, "en") == "faster-whisper"
    (installed_models / "faster-whisper-small" / ".model_identity.json").unlink()
    with pytest.raises(RuntimeError, match="No transcription Provider"):
        transcribe._select_provider(None, "en")


def test_language_model_checked_before_import(installed_models):
    (installed_models / "lang-id-voxlingua107-ecapa" / ".model_identity.json").unlink()
    with pytest.raises(RuntimeError, match="marker"):
        transcribe._detect_language([])


def test_invalid_installation_cannot_reuse_public_cache(installed_models, tmp_path):
    from scripts.asr.alignment import AlignedTranscript, AlignmentItem

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    calls = []

    def engine(*_args):
        calls.append(True)
        return AlignedTranscript("ok", (AlignmentItem("ok", 0, 0.5, None),))

    options = dict(
        language="en",
        provider="faster-whisper",
        decoder=lambda _: [0.0] * 16000,
        engine=engine,
        results_dir=tmp_path / "results",
    )
    result = transcribe.run_transcribe(audio, **options)
    before = result.manifest_path.read_bytes()
    (installed_models / "faster-whisper-small" / ".model_identity.json").unlink()
    with pytest.raises(RuntimeError, match="marker"):
        transcribe.run_transcribe(audio, **options)
    assert calls == [True]
    assert result.manifest_path.read_bytes() == before


@pytest.mark.parametrize("provider", ["faster-whisper", "qwen3-asr"])
def test_prepared_model_binds_loading_identity(provider):
    request = resolved_request(provider)
    identity, execution = request["provider_identity"], request["execution_policy"]
    prepared = PreparedModel.bind(object(), identity, execution)
    identity["language"] = "en"
    validate_prepared_model(prepared, identity, execution)
    changed = deepcopy(identity)
    changed["model"]["revision"] = "c" * 40
    with pytest.raises(ValueError, match="does not match"):
        validate_prepared_model(prepared, changed, execution)
    changed_execution = dict(execution)
    changed_execution[
        "cpu_threads" if provider == "faster-whisper" else "batch_size"
    ] += 1
    with pytest.raises(ValueError, match="does not match"):
        validate_prepared_model(prepared, identity, changed_execution)
    with pytest.raises(ValueError, match="no verified"):
        validate_prepared_model(object(), identity, execution)
    if provider == "qwen3-asr":
        changed = deepcopy(identity)
        changed["max_new_tokens"] += 1
        with pytest.raises(ValueError, match="does not match"):
            validate_prepared_model(prepared, changed, execution)


def test_native_benchmark_rejects_unbound_model(
    installed_models, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from benchmark.worker import worker

    monkeypatch.setattr(
        "scripts.asr.chunking.decode_normalized_audio",
        lambda _path: SimpleNamespace(sample_count=16000, samples=[]),
    )
    with pytest.raises(ValueError, match="no verified"):
        worker(
            tmp_path / "audio.wav",
            "faster-whisper",
            "en",
            "provider-native",
            tmp_path,
            prepared_model=object(),
        )


def test_production_rejects_unbound_model_before_inference(installed_models, tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    with pytest.raises(ValueError, match="no verified"):
        transcribe.run_transcribe(
            audio,
            language="en",
            provider="faster-whisper",
            decoder=lambda _: [0.0] * 16000,
            results_dir=tmp_path / "results",
            prepared_model=object(),
            engine=lambda *_: pytest.fail("inference must not start"),
        )
