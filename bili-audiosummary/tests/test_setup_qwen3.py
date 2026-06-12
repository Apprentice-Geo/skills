from pathlib import Path

from scripts.setup.install_qwen3 import (
    QWEN3_TORCH_INDEX_URL,
    install_cuda_torch,
)
from scripts.setup.process_logging import ProcessResult


class RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def run(self, command, _description, *, env=None, **_kwargs):
        self.calls.append(
            ([str(part) for part in command], dict(env or {}))
        )
        return ProcessResult(0, "")


def test_install_cuda_torch_uses_pytorch_cuda_index(
    workspace_tmp_path: Path,
) -> None:
    python = workspace_tmp_path / "python.exe"
    logger = RecordingLogger()

    install_cuda_torch(python, logger, {"PIP_INDEX_URL": "https://mirror.invalid"})

    assert len(logger.calls) == 1
    command, env = logger.calls[0]
    assert command[:4] == [str(python), "-m", "pip", "install"]
    assert command[command.index("--index-url") + 1] == QWEN3_TORCH_INDEX_URL
    assert {"torch", "torchaudio"} <= set(command)
    assert env == {"PIP_INDEX_URL": "https://mirror.invalid"}
