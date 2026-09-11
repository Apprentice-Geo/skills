from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.io_utils import canonical_sha256


def model_configuration(
    request: dict[str, Any], execution: dict[str, Any]
) -> dict[str, Any]:
    if request["provider"] == "faster-whisper":
        return {
            "model": request["model"],
            "device": request["device"],
            "compute_type": request["compute_type"],
            "cpu_threads": execution["cpu_threads"],
            "num_workers": execution["num_workers"],
        }
    model = request["model"]
    return {
        "model": {key: model[key] for key in ("repo", "revision", "logical_id")},
        "aligner": {
            key: model[f"aligner_{key}"] for key in ("repo", "revision", "logical_id")
        },
        "device": request["device"],
        "dtype": request["compute_type"],
        "batch_size": execution["batch_size"],
        "max_new_tokens": request["max_new_tokens"],
    }


@dataclass(frozen=True)
class PreparedModel:
    model: Any
    configuration_digest: str

    @classmethod
    def bind(
        cls, model: Any, request: dict[str, Any], execution: dict[str, Any]
    ) -> PreparedModel:
        return cls(model, canonical_sha256(model_configuration(request, execution)))

    def validate(self, request: dict[str, Any], execution: dict[str, Any]) -> None:
        if self.configuration_digest != canonical_sha256(
            model_configuration(request, execution)
        ):
            raise ValueError(
                "Prepared model identity or loading configuration does not match request."
            )


def validate_prepared_model(
    model: Any, request: dict[str, Any], execution: dict[str, Any]
) -> None:
    if not isinstance(model, PreparedModel):
        raise ValueError("Prepared model has no verified loading identity.")
    model.validate(request, execution)


def unwrap_model(model: Any) -> Any:
    return model.model if isinstance(model, PreparedModel) else model
