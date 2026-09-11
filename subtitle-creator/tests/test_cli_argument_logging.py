from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import (
    attach_transcription,
    create_subtitle,
    finalize_subtitle,
    remove_subtitle_job,
)

CliMain = Callable[[list[str] | None], int]


@pytest.mark.parametrize(
    "main",
    [
        create_subtitle.main,
        attach_transcription.main,
        finalize_subtitle.main,
        remove_subtitle_job.main,
    ],
)
def test_help_does_not_start_logging_session(
    main: CliMain, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    terminal = capsys.readouterr()
    assert raised.value.code == 0
    assert "usage:" in terminal.out
    assert terminal.err == ""
    assert not (tmp_path / ".cache" / "logs").exists()


@pytest.mark.parametrize(
    "main",
    [
        create_subtitle.main,
        attach_transcription.main,
        finalize_subtitle.main,
        remove_subtitle_job.main,
    ],
)
def test_parse_error_stays_outside_logging_session(
    main: CliMain, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([]) == 1

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err.startswith("error: the following arguments are required:")
    assert "Full log:" not in terminal.err
    assert not (tmp_path / ".cache" / "logs").exists()
