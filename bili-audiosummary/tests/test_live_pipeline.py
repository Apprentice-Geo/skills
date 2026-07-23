import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.live
def test_live_pipeline_can_force_asr_with_url_and_cookies() -> None:
    url = os.environ.get("BILI_TEST_URL")
    cookies = os.environ.get("BILI_TEST_COOKIES")
    if not url or not cookies:
        pytest.skip(
            "Set BILI_TEST_URL and BILI_TEST_COOKIES to run live pipeline tests."
        )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_pipeline",
            url,
            "--cookies",
            cookies,
            "--language",
            "zh",
            "--skip-subtitles",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=1800,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "[Stage] Transcribe audio with " in completed.stdout
    assert "Summary Prompt:" in completed.stdout
    assert "Skipping subtitles; using ASR from audio." not in completed.stdout
    assert "Transcript JSON:" not in completed.stdout
