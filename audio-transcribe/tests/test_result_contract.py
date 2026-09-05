from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Callable, Literal, assert_type, get_type_hints

import pytest
from audio_transcribe_contract import (
    ResultManifest,
    ResultValidationError,
    Transcript,
    TranscriptionResult,
    load_manifest,
    load_result,
)
from result_fixtures import resolved_request


def _assert_public_result_types(result: TranscriptionResult) -> None:
    assert_type(
        result.manifest["request"]["provider"],
        Literal["faster-whisper", "qwen3-asr"],
    )
    assert_type(result.transcript["segments"][0]["text"], str)
    assert_type(
        result.transcript["items"][0]["probability"],
        int | float | None,
    )


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_result(root: Path, provider: str = "faster-whisper") -> Path:
    request = resolved_request(provider)
    request = {"config_digest": _canonical_sha256(request), **request}
    duration = 1.0
    transcript = {
        "schema_version": 3,
        "audio_id": "a" * 64,
        "config_digest": request["config_digest"],
        "provider": provider,
        "language": "zh",
        "duration": duration,
        "segments": [
            {"id": 0, "start": 0.0, "end": 0.5, "text": "敏感转写内容。"},
            {"id": 1, "start": 0.5, "end": 1.0, "text": "第二句。"},
        ],
    }
    transcript["items"] = [
        {
            "text": "敏感转写内容。",
            "start": 0.0,
            "end": 0.5,
            "probability": None if provider == "qwen3-asr" else 0.9,
        },
        {"text": "第二句。", "start": 0.5, "end": 1.0, "probability": None},
    ]
    transcript_path = root / "transcript.json"
    _write_json(transcript_path, transcript)
    manifest = {
        "schema_version": 3,
        "status": "complete",
        "audio": {
            "id": "a" * 64,
            "size": 10,
            "sample_count": 16_000,
            "sample_rate": 16_000,
            "duration": duration,
        },
        "request": request,
        "artifacts": {
            "transcript": "transcript.json",
        },
        "artifact_sha256": {
            "transcript": hashlib.sha256(transcript_path.read_bytes()).hexdigest(),
        },
    }
    manifest_path = root / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def _rewrite_manifest(path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    _write_json(path, manifest)


def _rewrite_artifact(
    manifest_path: Path,
    name: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = manifest_path.parent / manifest["artifacts"][name]
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    _write_json(path, payload)
    manifest["artifact_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)


@pytest.mark.parametrize("provider", ["faster-whisper", "qwen3-asr"])
@pytest.mark.parametrize(
    "node",
    [
        "",
        "audio",
        "artifacts",
        "artifact_sha256",
        "request",
        "request.provider_identity",
        "request.provider_identity.model",
        "request.execution_policy",
        "request.vad_parameters",
        "request.planning_parameters",
        "request.text_normalization",
        "request.alignment_policy",
    ],
)
@pytest.mark.parametrize("mutation", ["unknown", "missing", "wrong_type"])
def test_manifest_objects_are_recursively_strict(
    workspace_tmp_path: Path, provider: str, node: str, mutation: str
) -> None:
    path = _write_result(workspace_tmp_path / "result", provider)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    target = manifest
    for key in node.split(".") if node else []:
        target = target[key]
    if mutation == "unknown":
        target["unexpected"] = 1
    elif mutation == "missing":
        del target[next(key for key in target if key != "config_digest")]
    else:
        key = next(key for key in target if key != "config_digest")
        target[key] = []
    request = manifest.get("request")
    if isinstance(request, dict):
        request["config_digest"] = _canonical_sha256(
            {key: value for key, value in request.items() if key != "config_digest"}
        )
    _write_json(path, manifest)
    with pytest.raises(ResultValidationError) as error:
        load_manifest(path)
    assert "config_digest does not match" not in str(error.value)
    if mutation == "unknown":
        assert f"manifest.{node + '.' if node else ''}unexpected" in str(error.value)


@pytest.mark.parametrize("node", ["", "segments", "items"])
@pytest.mark.parametrize("mutation", ["unknown", "missing", "wrong_type"])
def test_transcript_objects_are_recursively_strict(
    workspace_tmp_path: Path, node: str, mutation: str
) -> None:
    path = _write_result(workspace_tmp_path / "result")

    def mutate(payload):
        target = payload[node][0] if node else payload
        if mutation == "unknown":
            target["unexpected"] = 1
        elif mutation == "missing":
            del target[next(iter(target))]
        else:
            target[next(iter(target))] = []

    _rewrite_artifact(path, "transcript", mutate)
    with pytest.raises(ResultValidationError) as error:
        load_result(path)
    assert "digest" not in str(error.value)
    if mutation == "unknown":
        assert "unexpected" in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_policy", resolved_request("qwen3-asr")["execution_policy"]),
        ("provider_identity", resolved_request("qwen3-asr")["provider_identity"]),
        (
            "alignment_policy",
            {**resolved_request()["alignment_policy"], "schema_version": True},
        ),
        (
            "text_normalization",
            {**resolved_request()["text_normalization"], "schema_version": True},
        ),
    ],
)
def test_resolved_request_rejects_mixed_provider_and_boolean_versions(
    workspace_tmp_path: Path, field: str, value: Any
) -> None:
    path = _write_result(workspace_tmp_path / "result")

    def mutate(manifest):
        request = manifest["request"]
        request[field] = value
        request["config_digest"] = _canonical_sha256(
            {key: item for key, item in request.items() if key != "config_digest"}
        )

    _rewrite_manifest(path, mutate)
    with pytest.raises(ResultValidationError):
        load_manifest(path)


@pytest.mark.parametrize("target", ["manifest", "transcript", "request"])
def test_v2_is_rejected_even_with_valid_digests(
    workspace_tmp_path: Path, target: str
) -> None:
    path = _write_result(workspace_tmp_path / "result")
    if target == "transcript":
        _rewrite_artifact(
            path, "transcript", lambda value: value.update(schema_version=2)
        )
    else:

        def mutate(manifest):
            if target == "manifest":
                manifest["schema_version"] = 2
            else:
                request = manifest["request"]
                request["public_schema_version"] = 2
                request["config_digest"] = _canonical_sha256(
                    {
                        key: value
                        for key, value in request.items()
                        if key != "config_digest"
                    }
                )

        _rewrite_manifest(path, mutate)
    with pytest.raises(ResultValidationError, match="schema_version"):
        load_result(path)


def test_public_typed_dict_key_boundaries() -> None:
    assert ResultManifest.__required_keys__ == {
        "schema_version",
        "status",
        "audio",
        "request",
        "artifacts",
        "artifact_sha256",
    }
    assert Transcript.__required_keys__ == {
        "schema_version",
        "audio_id",
        "config_digest",
        "provider",
        "language",
        "duration",
        "segments",
        "items",
    }

    request = get_type_hints(ResultManifest)["request"]
    assert request.__required_keys__ == {
        "config_digest",
        "provider",
        "language",
        "alignment_policy",
        "public_schema_version",
        "provider_identity",
        "execution_policy",
        "vad_parameters",
        "planning_parameters",
        "segmentation_schema_version",
        "text_normalization",
    }
    assert request.__optional_keys__ == set()


@pytest.mark.parametrize("provider", ["faster-whisper", "qwen3-asr"])
def test_load_result_returns_complete_snapshot(
    workspace_tmp_path: Path, provider: str
) -> None:
    manifest_path = _write_result(workspace_tmp_path / provider, provider)

    result = load_result(manifest_path)

    assert isinstance(result, TranscriptionResult)
    assert result.manifest_path == manifest_path.resolve()
    assert (
        result.transcript_path == (manifest_path.parent / "transcript.json").resolve()
    )
    assert result.manifest["request"]["provider"] == provider
    with pytest.raises(FrozenInstanceError):
        result.manifest_path = Path("elsewhere")  # type: ignore[misc]
    result.transcript["segments"][0]["text"] = "changed in memory"  # type: ignore[index]
    assert "changed in memory" not in result.transcript_path.read_text(encoding="utf-8")


def test_two_file_bundle_is_portable_without_private_files(
    workspace_tmp_path: Path,
) -> None:
    source = _write_result(workspace_tmp_path / "source")
    destination = workspace_tmp_path / "archive"
    destination.mkdir()
    for name in ("manifest.json", "transcript.json"):
        shutil.copy2(source.parent / name, destination / name)
    expected = load_result(source)
    # Make the source unusable, proving the copy resolves its own relative paths.
    source.write_text("{", encoding="utf-8")
    actual = load_result(destination / "manifest.json")
    assert actual.manifest == expected.manifest
    assert actual.transcript == expected.transcript
    assert actual.transcript_path == destination / "transcript.json"
    assert set(path.name for path in destination.iterdir()) == {
        "manifest.json",
        "transcript.json",
    }
    # Private-looking entries of the wrong type have no effect either.
    (destination / "workspace").write_text("unused", encoding="utf-8")
    (destination / "transcribe.log").mkdir()
    assert load_result(destination / "manifest.json").transcript == expected.transcript


def test_load_manifest_does_not_certify_body(workspace_tmp_path: Path) -> None:
    path = _write_result(workspace_tmp_path / "result")
    expected = load_result(path).manifest
    (path.parent / "transcript.json").unlink()
    assert load_manifest(path) == expected
    with pytest.raises(ResultValidationError, match="must be a file"):
        load_result(path)


@pytest.mark.parametrize("field", ["log", "workspace", "raw_timestamps"])
def test_v3_manifest_rejects_legacy_artifact_references(
    workspace_tmp_path: Path, field: str
) -> None:
    path = _write_result(workspace_tmp_path / "result")
    _rewrite_manifest(path, lambda value: value["artifacts"].__setitem__(field, field))
    with pytest.raises(
        ResultValidationError, match="Unknown field: manifest.artifacts"
    ):
        load_manifest(path)


def test_manifest_cannot_reference_itself(workspace_tmp_path: Path) -> None:
    path = _write_result(workspace_tmp_path / "result")
    _rewrite_manifest(
        path,
        lambda value: value["artifacts"].__setitem__("transcript", "manifest.json"),
    )
    with pytest.raises(ResultValidationError, match="separate"):
        load_manifest(path)


@pytest.mark.parametrize("value", [None, 1, True])
def test_public_schema_participates_in_config_identity(
    workspace_tmp_path: Path, value: Any
) -> None:
    path = _write_result(workspace_tmp_path / "result")

    def mutate(manifest):
        request = manifest["request"]
        request["public_schema_version"] = value
        request["config_digest"] = _canonical_sha256(
            {key: item for key, item in request.items() if key != "config_digest"}
        )

    _rewrite_manifest(path, mutate)
    with pytest.raises(ResultValidationError, match="public_schema_version"):
        load_manifest(path)


@pytest.mark.parametrize("target", ["manifest", "transcript"])
@pytest.mark.parametrize(
    "invalid_json",
    [
        '{"敏感转写内容": 1, "敏感转写内容": 2}',
        '{"value": NaN}',
    ],
)
def test_json_is_strict_and_must_be_an_object(
    workspace_tmp_path: Path, target: str, invalid_json: str
) -> None:
    manifest_path = _write_result(workspace_tmp_path / f"{target}-{len(invalid_json)}")
    if target == "manifest":
        path = manifest_path
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = manifest_path.parent / manifest["artifacts"][target]
        path.write_text(invalid_json, encoding="utf-8")
        manifest["artifact_sha256"][target] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        _write_json(manifest_path, manifest)
    if target == "manifest":
        path.write_text(invalid_json, encoding="utf-8")

    with pytest.raises(ResultValidationError) as caught:
        load_result(manifest_path)

    assert caught.value.__cause__ is not None
    assert "敏感转写内容" not in str(caught.value)
    assert "敏感转写内容" not in str(caught.value.__cause__)


@pytest.mark.parametrize("target", ["manifest", "transcript"])
def test_json_root_must_be_an_object(workspace_tmp_path: Path, target: str) -> None:
    manifest_path = _write_result(workspace_tmp_path / target)
    if target == "manifest":
        path = manifest_path
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = manifest_path.parent / manifest["artifacts"][target]
        path.write_text("[]", encoding="utf-8")
        manifest["artifact_sha256"][target] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        _write_json(manifest_path, manifest)
    if target == "manifest":
        path.write_text("[]", encoding="utf-8")

    with pytest.raises(ResultValidationError, match="JSON object"):
        load_result(manifest_path)


def test_manifest_must_be_a_file(workspace_tmp_path: Path) -> None:
    missing = workspace_tmp_path / "missing.json"

    with pytest.raises(ResultValidationError) as caught:
        load_result(missing)

    assert isinstance(caught.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("schema_version", True),
        lambda value: value.__setitem__("schema_version", 1),
        lambda value: value.__setitem__("status", "running"),
        lambda value: value.__setitem__("audio", []),
        lambda value: value["audio"].__setitem__("id", "A" * 64),
        lambda value: value["audio"].__setitem__("size", True),
        lambda value: value["audio"].__setitem__("size", 10**1000),
        lambda value: value["audio"].__setitem__("duration", math.inf),
        lambda value: value["request"].__setitem__("provider", "qwen3"),
        lambda value: value["request"].__setitem__("language", ""),
        lambda value: value["request"].pop("alignment_policy"),
        lambda value: value["request"]["alignment_policy"].__setitem__(
            "ordering", "relaxed"
        ),
        lambda value: value["request"].__setitem__("config_digest", "b" * 64),
        lambda value: value["artifact_sha256"].__setitem__("transcript", "0" * 63),
    ],
)
def test_manifest_schema_and_identity_are_strict(
    workspace_tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    manifest_path = _write_result(workspace_tmp_path / "result")
    _rewrite_manifest(manifest_path, mutate)

    with pytest.raises(ResultValidationError):
        load_result(manifest_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request.pop("alignment_policy"),
        lambda request: request["alignment_policy"].__setitem__(
            "zero_duration", "keep"
        ),
    ],
)
def test_alignment_policy_is_required_and_exact(
    workspace_tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    manifest_path = _write_result(workspace_tmp_path / "alignment-policy")
    _rewrite_manifest(
        manifest_path,
        lambda manifest: mutate(manifest["request"]),
    )

    with pytest.raises(ResultValidationError, match="alignment_policy"):
        load_result(manifest_path)


@pytest.mark.parametrize("name", ["transcript"])
def test_public_artifact_digest_must_match(workspace_tmp_path: Path, name: str) -> None:
    manifest_path = _write_result(workspace_tmp_path / name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = manifest_path.parent / manifest["artifacts"][name]
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(ResultValidationError, match="digest"):
        load_result(manifest_path)


@pytest.mark.parametrize("name", ["transcript"])
def test_file_artifacts_must_be_files(workspace_tmp_path: Path, name: str) -> None:
    manifest_path = _write_result(workspace_tmp_path / name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = manifest_path.parent / manifest["artifacts"][name]
    path.unlink()
    path.mkdir()

    with pytest.raises(ResultValidationError):
        load_result(manifest_path)


@pytest.mark.parametrize("value", ["../outside.json", "C:/outside.json"])
def test_artifact_paths_must_stay_inside_result(
    workspace_tmp_path: Path, value: str
) -> None:
    manifest_path = _write_result(workspace_tmp_path / "result")
    _rewrite_manifest(
        manifest_path,
        lambda manifest: manifest["artifacts"].__setitem__("transcript", value),
    )

    with pytest.raises(ResultValidationError):
        load_result(manifest_path)


def test_artifact_symlink_cannot_escape_result(workspace_tmp_path: Path) -> None:
    manifest_path = _write_result(workspace_tmp_path / "result")
    outside = workspace_tmp_path / "outside"
    outside.mkdir()
    (outside / "transcript.json").write_text("{}", encoding="utf-8")
    link = manifest_path.parent / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"symlinks unavailable: {exc}")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    _rewrite_manifest(
        manifest_path,
        lambda manifest: manifest["artifacts"].__setitem__(
            "transcript", "linked/transcript.json"
        ),
    )

    with pytest.raises(ResultValidationError, match="escapes"):
        load_result(manifest_path)


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("transcript", lambda value: value.__setitem__("schema_version", True)),
        ("transcript", lambda value: value.__setitem__("audio_id", "b" * 64)),
        ("transcript", lambda value: value.__setitem__("provider", "qwen3-asr")),
        ("transcript", lambda value: value.__setitem__("language", "en")),
        ("transcript", lambda value: value.__setitem__("duration", 2.0)),
        ("transcript", lambda value: value.__setitem__("config_digest", "b" * 64)),
        ("transcript", lambda value: value.__setitem__("duration", True)),
    ],
)
def test_cross_file_identity_must_match(
    workspace_tmp_path: Path,
    name: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    manifest_path = _write_result(workspace_tmp_path / f"{name}-identity")
    _rewrite_artifact(manifest_path, name, mutate)

    with pytest.raises(ResultValidationError):
        load_result(manifest_path)


@pytest.mark.parametrize(
    "segments",
    [
        [],
        [{"id": True, "start": 0.0, "end": 0.5, "text": "x"}],
        [{"id": 0, "start": 0.0, "end": 0.5, "text": ""}],
        [{"id": 0, "start": -0.1, "end": 0.5, "text": "x"}],
        [{"id": 0, "start": 0.5, "end": 0.5, "text": "x"}],
        [{"id": 0, "start": 0.0, "end": 1.1, "text": "x"}],
        [
            {"id": 0, "start": 0.0, "end": 0.6, "text": "x"},
            {"id": 1, "start": 0.5, "end": 0.8, "text": "y"},
        ],
    ],
)
def test_transcript_segment_contract(
    workspace_tmp_path: Path, segments: list[dict[str, Any]]
) -> None:
    manifest_path = _write_result(workspace_tmp_path / "segments")
    _rewrite_artifact(
        manifest_path,
        "transcript",
        lambda transcript: transcript.__setitem__("segments", segments),
    )

    with pytest.raises(ResultValidationError):
        load_result(manifest_path)


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"text": "x", "start": 0.0, "end": 0.5}],
        [{"text": "", "start": 0.0, "end": 0.5, "probability": None}],
        [{"text": "x", "start": True, "end": 0.5, "probability": None}],
        [{"text": "x", "start": 0.0, "end": 1.1, "probability": None}],
        [{"text": "x", "start": 0.6, "end": 0.5, "probability": None}],
        [{"text": "x", "start": 0.5, "end": 0.5, "probability": None}],
        [
            {"text": "x", "start": 0.0, "end": 0.6, "probability": None},
            {"text": "y", "start": 0.5, "end": 0.8, "probability": None},
        ],
        [{"text": "x", "start": 0.0, "end": 0.5, "probability": True}],
        [{"text": "x", "start": 0.0, "end": 0.5, "probability": 1.1}],
    ],
)
def test_raw_timestamp_contract(
    workspace_tmp_path: Path, items: list[dict[str, Any]]
) -> None:
    manifest_path = _write_result(workspace_tmp_path / "timestamps")
    _rewrite_artifact(
        manifest_path,
        "transcript",
        lambda raw: raw.__setitem__("items", items),
    )

    with pytest.raises(ResultValidationError):
        load_result(manifest_path)


def test_qwen_probability_must_be_null(workspace_tmp_path: Path) -> None:
    manifest_path = _write_result(workspace_tmp_path / "qwen", "qwen3-asr")
    _rewrite_artifact(
        manifest_path,
        "transcript",
        lambda raw: raw["items"][0].__setitem__("probability", 0.5),
    )

    with pytest.raises(ResultValidationError, match="probability"):
        load_result(manifest_path)


def test_failure_does_not_modify_artifacts_or_leak_transcript(
    workspace_tmp_path: Path,
) -> None:
    manifest_path = _write_result(workspace_tmp_path / "result")
    _rewrite_artifact(
        manifest_path,
        "transcript",
        lambda transcript: transcript["segments"][0].__setitem__("end", 2.0),
    )
    before = {
        path: path.read_bytes()
        for path in manifest_path.parent.iterdir()
        if path.is_file()
    }

    with pytest.raises(ResultValidationError) as caught:
        load_result(manifest_path)

    assert "敏感转写内容" not in str(caught.value)
    assert {path: path.read_bytes() for path in before} == before
