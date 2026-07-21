import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.benchmark as benchmark
import scripts.benchmark_whisper_parallel as whisper_parallel_benchmark
import scripts.fetch_audio as fetch_audio
from scripts.runtime_options import FetchOptions
from scripts.utils import read_json, write_json


def test_whisper_parallel_benchmark_cli_defaults_to_three_repetitions(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_whisper_parallel.py",
            "--video",
            "BV1MN4y177PB",
        ],
    )

    args = whisper_parallel_benchmark.parse_args()

    assert args.video == ["BV1MN4y177PB"]
    assert args.repetitions == 3
    assert args.max_chunk_seconds == [180, 300, 450]


def test_chunk_limit_benchmark_rotates_limit_order() -> None:
    assert whisper_parallel_benchmark.rotated_limits((180, 300, 450), 0) == (
        180,
        300,
        450,
    )
    assert whisper_parallel_benchmark.rotated_limits((180, 300, 450), 1) == (
        300,
        450,
        180,
    )
    assert whisper_parallel_benchmark.rotated_limits((180, 300, 450), 2) == (
        450,
        180,
        300,
    )


def test_chunk_limit_report_applies_hard_cut_gate_and_geometric_mean() -> None:
    results = []
    values = {
        "a": {180: (10.0, 0), 300: (12.0, 0), 450: (9.0, 1)},
        "b": {180: (20.0, 0), 300: (20.0, 0), 450: (18.0, 0)},
    }
    for bvid, by_limit in values.items():
        for limit, (elapsed, hard_cuts) in by_limit.items():
            for repetition in range(3):
                results.append(
                    {
                        "bvid": bvid,
                        "max_chunk_seconds": limit,
                        "repetition": repetition + 1,
                        "elapsed_seconds": elapsed,
                        "chunk_count": 2,
                        "num_workers": 1,
                        "batch_count": 2,
                        "hard_cut_count": hard_cuts,
                        "chunk_estimated_speech_durations": [1.0, 1.0],
                        "max_estimated_speech_duration": 1.0,
                        "speech_load_msre": 0.0,
                    }
                )

    summary = whisper_parallel_benchmark.summarize_results(results)

    assert summary["quality_pass"] == {"180": True, "300": True, "450": False}
    assert summary["winner_max_chunk_seconds"] == 180
    assert summary["geometric_mean_runtime_ratio_to_300"]["180"] == pytest.approx(
        (10 / 12 * 20 / 20) ** 0.5
    )


def test_default_benchmark_matrix_covers_requested_videos_and_models() -> None:
    assert [
        (video.bvid, video.duration_label) for video in benchmark.BENCHMARK_VIDEOS
    ] == [
        ("BV1W694BEE7F", "00:01:03"),
        ("BV1Nt4y1D7pW", "00:07:56"),
        ("BV1MN4y177PB", "00:11:27"),
        ("BV1ks411e7W4", "00:19:45"),
        ("BV1Fa411c7Vh", "00:30:23"),
        ("BV1rb4y1D7Gf", "00:39:51"),
        ("BV1jJ411r7EL", "01:02:23"),
        ("BV1e24y1D7qt", "01:47:01"),
        ("BV1mL411z7Kf", "02:59:43"),
    ]
    assert benchmark.DEFAULT_PROVIDERS == ("whisper", "qwen3")
    assert whisper_parallel_benchmark.DEFAULT_VIDEO_IDS == (
        "BV1W694BEE7F",
        "BV1Nt4y1D7pW",
        "BV1MN4y177PB",
        "BV1ks411e7W4",
    )


def test_benchmark_fetches_audio_without_subtitles_or_explicit_cookies(mocker) -> None:
    video = benchmark.BENCHMARK_VIDEOS[0]
    audio_path = Path("cache") / video.bvid / "resource" / f"{video.bvid}.m4a"

    def run_fetch(options):
        assert options.url == video.url
        assert options.skip_subtitles is True
        assert options.skip_audio is False
        assert options.cookies is None
        return {"audio_files": [audio_path]}

    mocker.patch("scripts.benchmark.fetch_audio.run_fetch", side_effect=run_fetch)
    result = benchmark.prefetch_audio((video,), Path("cache"))

    assert result == {video.bvid: audio_path}


def test_root_cookie_file_is_auto_detected(workspace_tmp_path: Path, mocker) -> None:
    cookie_path = workspace_tmp_path / "cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    mocker.patch("scripts.fetch_audio.SKILL_ROOT", workspace_tmp_path)

    resolved = fetch_audio.resolve_cookie_path(
        FetchOptions(url="https://www.bilibili.com/video/BVTEST/")
    )

    assert resolved == cookie_path


def test_provider_preflight_happens_before_audio_fetch(
    mocker, workspace_tmp_path: Path
) -> None:
    mocker.patch("scripts.benchmark.require_psutil")
    prefetch_audio = mocker.patch("scripts.benchmark.prefetch_audio")
    mocker.patch(
        "scripts.benchmark.require_provider_ready",
        side_effect=RuntimeError("missing model"),
    )

    with pytest.raises(RuntimeError, match="missing model"):
        benchmark.run_benchmark(
            benchmark.BENCHMARK_VIDEOS[:1],
            ("whisper",),
            workspace_tmp_path,
        )

    prefetch_audio.assert_not_called()


def test_benchmark_records_failed_case_and_continues(
    workspace_tmp_path: Path, mocker
) -> None:
    videos = benchmark.BENCHMARK_VIDEOS[:1]
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    run_dir = workspace_tmp_path / "run"

    mocker.patch("scripts.benchmark.require_psutil")
    mocker.patch("scripts.benchmark.require_provider_ready")
    mocker.patch(
        "scripts.benchmark.prefetch_audio", return_value={videos[0].bvid: audio_path}
    )
    mocker.patch("scripts.benchmark.create_run_dir", return_value=run_dir)
    mocker.patch("scripts.benchmark.probe_audio_duration", return_value=60.0)
    mocker.patch(
        "scripts.benchmark.run_case_process",
        side_effect=[
            {"status": "failed", "error": "failed", "peak_rss_bytes": 1},
            {"status": "ok", "elapsed_seconds": 30.0, "peak_rss_bytes": 2},
        ],
    )

    report = benchmark.run_benchmark(videos, ("whisper", "qwen3"), workspace_tmp_path)

    assert [result["status"] for result in report["results"]] == ["failed", "ok"]
    assert (run_dir / "benchmark.json").is_file()
    assert (run_dir / "benchmark.md").is_file()


def test_process_tree_rss_sums_parent_and_children() -> None:
    class Process:
        def __init__(self, rss: int, children=()):
            self.rss = rss
            self._children = list(children)

        def children(self, recursive: bool):
            assert recursive is True
            return self._children

        def memory_info(self):
            return SimpleNamespace(rss=self.rss)

    process = Process(100, [Process(200), Process(300)])

    assert benchmark.process_tree_rss_bytes(process) == 600


def test_child_exit_without_result_is_recorded_as_failure(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    case_path = workspace_tmp_path / "case.json"
    result_path = workspace_tmp_path / "case_result.json"
    write_json(case_path, {"result_path": str(result_path)})

    class MonitoredProcess:
        def children(self, recursive: bool):
            assert recursive is True
            return []

        def memory_info(self):
            return SimpleNamespace(rss=123)

    class Psutil:
        @staticmethod
        def Process(_pid):
            return MonitoredProcess()

    class ChildProcess:
        pid = 123

        def poll(self):
            return 1

        def wait(self):
            return 1

    mocker.patch("scripts.benchmark.require_psutil", return_value=Psutil())
    mocker.patch("scripts.benchmark.subprocess.Popen", return_value=ChildProcess())

    result = benchmark.run_case_process(case_path, workspace_tmp_path / "child.log")

    assert result["status"] == "failed"
    assert result["returncode"] == 1
    assert result["peak_rss_bytes"] == 123


def test_run_case_records_provider_specific_memory_fields(
    workspace_tmp_path: Path,
    monkeypatch,
    mocker,
) -> None:
    class FakeCuda:
        def empty_cache(self):
            pass

        def reset_peak_memory_stats(self):
            pass

        def synchronize(self):
            pass

        def max_memory_allocated(self):
            return 10

        def max_memory_reserved(self):
            return 20

    fake_torch = SimpleNamespace(cuda=FakeCuda())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    mocker.patch("scripts.benchmark.transcribe.run_transcribe")

    for provider, expected_allocated, expected_reserved in (
        ("whisper", None, None),
        ("qwen3", 10, 20),
    ):
        result_path = workspace_tmp_path / f"{provider}.json"
        case_path = workspace_tmp_path / f"{provider}-case.json"
        write_json(
            case_path,
            {
                "provider": provider,
                "audio_path": str(workspace_tmp_path / "audio.m4a"),
                "transcript_dir": str(workspace_tmp_path / provider),
                "result_path": str(result_path),
            },
        )

        assert benchmark.run_case(case_path) == 0
        result = read_json(result_path)
        assert result["status"] == "ok"
        assert result["cuda_peak_allocated_bytes"] == expected_allocated
        assert result["cuda_peak_reserved_bytes"] == expected_reserved
