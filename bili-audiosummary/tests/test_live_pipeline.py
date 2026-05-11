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
        pytest.skip("Set BILI_TEST_URL and BILI_TEST_COOKIES to run live pipeline tests.")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_pipeline.py",
            url,
            "--cookies",
            cookies,
            "--skip-subtitles",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=1800,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Skipping subtitles; using ASR from audio." in completed.stdout
    assert "Summary Prompt:" in completed.stdout
