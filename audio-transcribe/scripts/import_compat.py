from __future__ import annotations

import importlib
import sys
from types import ModuleType

# SpeechBrain 1.1 的可选旧路径是懒加载模块；inspect 会意外触发它们。
OPTIONAL_ALIASES = (
    "speechbrain.pretrained",
    "speechbrain.k2_integration",
    "speechbrain.wordemb",
    "speechbrain.lobes.models.huggingface_transformers",
    "speechbrain.lobes.models.spacy",
    "speechbrain.lobes.models.flair",
    "speechbrain.nnet.loss.transducer_loss",
)


def clear_optional_aliases() -> None:
    for name in OPTIONAL_ALIASES:
        module = sys.modules.get(name)
        # 不删除真实模块，不通过 getattr 触发懒加载。
        if (
            module is not None
            and type(module).__module__ == "speechbrain.utils.importutils"
        ):
            sys.modules.pop(name, None)


def import_dependency(name: str) -> ModuleType:
    clear_optional_aliases()
    module = importlib.import_module(name)
    clear_optional_aliases()
    return module


def describe_import_error(module: str, exc: Exception) -> str:
    causes = []
    missing_names: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        missing = current.name if isinstance(current, ModuleNotFoundError) else None
        if missing:
            missing_names.append(missing)
        causes.append(
            f"{type(current).__name__}: {current}"
            + (f"; missing_module={missing}" if missing else "")
        )
        current = current.__cause__
    if "k2" in missing_names:
        category = "optional-module/compatibility"
    elif missing_names:
        category = "required-dependency-missing"
    else:
        category = "import-compatibility"
    packages = {"qwen_asr": "qwen-asr", "huggingface_hub": "huggingface-hub"}
    package = packages.get(module.split(".")[0], module.split(".")[0])
    return (
        f"importing={module}; package={package}; category={category}; "
        + " <- ".join(causes)
    )


def probe_statement(modules: tuple[str, ...]) -> str:
    return f"""
import json
import traceback
from scripts.import_compat import import_dependency, describe_import_error
for name in {modules!r}:
    try:
        module = import_dependency(name)
    except Exception as exc:
        print(json.dumps({{"error": describe_import_error(name, exc)}}))
        traceback.print_exc()
        raise SystemExit(1)
print(json.dumps({{"result": "importable"}}))
"""
