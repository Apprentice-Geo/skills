import argparse

from scripts.runtime_options import FetchOptions, PipelineOptions


def test_fetch_options_default_language_controls_subtitle_selection() -> None:
    options = FetchOptions(url="https://www.bilibili.com/video/BVTEST/")

    assert options.language == "zh"


def test_pipeline_options_keep_summary_language_independent() -> None:
    options = PipelineOptions.from_args(
        argparse.Namespace(
            url="https://www.bilibili.com/video/BVTEST/",
            language="en",
            summary_language="zh",
        )
    )

    assert options.language == "en"
    assert options.summary_language == "zh"
