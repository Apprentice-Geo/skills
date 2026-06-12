import argparse
from pathlib import Path
from typing import Any

import fetch_audio
import subtitle_transcript
import transcribe
from config import (
    DEFAULT_ASR_PROVIDER,
    DEFAULT_TRANSCRIBE_LANGUAGE,
    RESULTS_DIR,
    SUMMARY_INSTRUCTIONS_PATH,
    SUMMARY_TEMPLATE_BY_LANGUAGE,
)
from runtime_options import FetchOptions, PipelineOptions, TranscribeOptions
from utils import ensure_dir, path_to_posix, read_json


def resolve_transcribe_language(options: PipelineOptions) -> str:
    return options.language or DEFAULT_TRANSCRIBE_LANGUAGE


def make_fetch_args(options: PipelineOptions) -> FetchOptions:
    return FetchOptions(
        url=options.url,
        output_dir=RESULTS_DIR,
        cookies=options.cookies,
        playlist=False,
        skip_audio=False,
        skip_subtitles=options.skip_subtitles,
        language=resolve_transcribe_language(options),
        write_auto_subs=True,
        subtitle_langs=[],
        subtitle_format="srt/best",
        audio_selector=fetch_audio.DEFAULT_AUDIO_SELECTOR,
        audio_format=fetch_audio.DEFAULT_AUDIO_CODEC,
        audio_quality="0",
        retries=10,
        socket_timeout=30,
        quiet=False,
    )


def make_transcribe_args(
    options: PipelineOptions,
    manifest_path: Path,
    output_dir: Path | None,
) -> TranscribeOptions:
    return TranscribeOptions(
        input=None,
        manifest=manifest_path,
        audio=None,
        output_dir=output_dir,
        asr_provider=options.asr_provider,
        model=None,
        language=resolve_transcribe_language(options),
        device=transcribe.DEFAULT_TRANSCRIBE_DEVICE,
        compute_type=transcribe.DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
        beam_size=transcribe.DEFAULT_TRANSCRIBE_BEAM_SIZE,
        cpu_threads=0,
        num_workers=1,
    )


def require_audio_for_stt(audio_files: list[Path]) -> None:
    if audio_files:
        return
    raise RuntimeError(
        "No usable audio files available. Download or reuse at least one audio file before running ASR."
    )


def normalize_template_language(language: str | None) -> str:
    if not language:
        return "en"

    value = language.lower()
    if value.startswith("ai-zh"):
        return "zh"
    if value.startswith("ai-en"):
        return "en"
    if value.startswith("zh"):
        return "zh"
    if value.startswith("en"):
        return "en"
    return value


def select_summary_template(language: str | None) -> tuple[str, Path]:
    template_language = normalize_template_language(language)
    template_path = SUMMARY_TEMPLATE_BY_LANGUAGE.get(template_language)
    if template_path and template_path.exists():
        return template_language, template_path

    fallback_language = "en"
    fallback_path = SUMMARY_TEMPLATE_BY_LANGUAGE[fallback_language]
    if not fallback_path.exists():
        raise FileNotFoundError(f"Summary template not found: {fallback_path}")
    return fallback_language, fallback_path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def write_summary_prompt(
    result_dir: Path,
    video_id: str,
    transcript_markdown_path: Path,
    transcript_json_path: Path,
) -> dict[str, Path]:
    transcript_payload = read_json(transcript_json_path)
    language = transcript_payload.get("language")
    template_language, template_path = select_summary_template(language)

    prompt_path = result_dir / f"{video_id}_summary_prompt.md"
    summary_path = result_dir / f"{video_id}_summary_{template_language}.md"

    if not SUMMARY_INSTRUCTIONS_PATH.exists():
        raise FileNotFoundError(f"Summary instructions not found: {SUMMARY_INSTRUCTIONS_PATH}")

    sections = [
        "# Output File",
        "",
        "Write the final summary to the following UTF-8 Markdown file:",
        "",
        f"`{path_to_posix(summary_path)}`",
        "",
        "---",
        "",
        f"BEGIN FILE: {path_to_posix(SUMMARY_INSTRUCTIONS_PATH.relative_to(SUMMARY_INSTRUCTIONS_PATH.parents[1]))}",
        "",
        read_text(SUMMARY_INSTRUCTIONS_PATH),
        "",
        "---",
        "",
        f"BEGIN FILE: {path_to_posix(template_path.relative_to(template_path.parents[1]))}",
        "",
        read_text(template_path),
        "",
        "---",
        "",
        f"BEGIN FILE: {path_to_posix(transcript_markdown_path)}",
        "",
        read_text(transcript_markdown_path),
        "",
    ]

    ensure_dir(prompt_path.parent)
    prompt_path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    return {
        "prompt_path": prompt_path,
        "summary_path": summary_path,
        "template_path": template_path,
    }


def write_prompt_for_transcript(
    fetch_result: dict[str, Any],
    transcript_result: dict[str, Any],
) -> dict[str, Path]:
    return write_summary_prompt(
        result_dir=fetch_result["paths"]["result"],
        video_id=fetch_result["video_id"],
        transcript_markdown_path=transcript_result["markdown_path"],
        transcript_json_path=transcript_result["json_path"],
    )


def run_asr_transcript(
    options: PipelineOptions,
    fetch_result: dict[str, Any],
    audio_files: list[Path],
) -> dict[str, Any]:
    require_audio_for_stt(audio_files)
    transcribe_args = make_transcribe_args(
        options,
        fetch_result["manifest_path"],
        fetch_result["paths"]["result"],
    )
    return transcribe.run_transcribe(transcribe_args)


def select_usable_subtitle(
    subtitle_files: list[Path],
    preferred_languages: list[str],
) -> Path | None:
    sorted_subtitle_files = fetch_audio.sort_subtitle_files(
        subtitle_files,
        preferred_languages,
    )
    for candidate_path in sorted_subtitle_files:
        segments, error = subtitle_transcript.probe_srt(candidate_path)
        if segments is not None:
            return candidate_path
        print(f"Warning: subtitle is unusable: {path_to_posix(candidate_path)} ({error})")

    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full Bilibili audio summary preparation pipeline: fetch subtitles and audio, prefer usable subtitles, fall back to STT when needed, then build a summary prompt."
    )
    parser.add_argument("url", help="Bilibili video URL.")
    parser.add_argument("--cookies", type=Path, help="Path to a Netscape-format cookies.txt file.")
    parser.add_argument("--language", choices=("zh", "en"), default=DEFAULT_TRANSCRIBE_LANGUAGE)
    parser.add_argument(
        "--asr-provider",
        choices=("whisper", "qwen3"),
        default=DEFAULT_ASR_PROVIDER,
        help="ASR provider. qwen3 is only for machines with available CUDA.",
    )
    parser.add_argument(
        "--skip-subtitles",
        action="store_true",
        help="Skip subtitle reuse/download and force ASR from audio.",
    )
    return parser.parse_args()


def run_pipeline(args: argparse.Namespace | PipelineOptions) -> dict[str, Any]:
    options = PipelineOptions.from_args(args)
    fetch_args = make_fetch_args(options)
    fetch_result = fetch_audio.run_fetch(fetch_args)
    manifest_path = fetch_result["manifest_path"]
    result_dir = fetch_result["paths"]["result"]

    transcript_result = None
    prompt_result = None

    subtitle_files = fetch_result.get("subtitle_files") or []
    audio_files = [Path(path) for path in (fetch_result.get("audio_files") or [])]
    usable_subtitle_files = [Path(path) for path in subtitle_files if fetch_audio.is_usable_subtitle(Path(path))]
    if options.skip_subtitles:
        print("Skipping subtitles; using ASR from audio.")
        transcript_result = run_asr_transcript(options, fetch_result, audio_files)
        prompt_result = write_prompt_for_transcript(fetch_result, transcript_result)
    elif usable_subtitle_files:
        subtitle_path = select_usable_subtitle(
            usable_subtitle_files,
            fetch_audio.resolve_subtitle_langs(fetch_args),
        )
        if subtitle_path is not None:
            transcript_result = subtitle_transcript.subtitle_to_transcript(
                subtitle_path=subtitle_path,
                manifest=read_json(manifest_path),
                metadata=read_json(fetch_result["metadata_path"]),
                output_dir=result_dir,
            )
            prompt_result = write_prompt_for_transcript(fetch_result, transcript_result)
        else:
            print("Subtitle SRT is empty or invalid; falling back to STT.")
            transcript_result = run_asr_transcript(options, fetch_result, audio_files)
            prompt_result = write_prompt_for_transcript(fetch_result, transcript_result)
    else:
        if subtitle_files:
            print("No usable SRT subtitles found; falling back to STT.")
        transcript_result = run_asr_transcript(options, fetch_result, audio_files)
        prompt_result = write_prompt_for_transcript(fetch_result, transcript_result)
    print("")
    print("Pipeline completed.")
    print(f"Result: {path_to_posix(result_dir)}")
    print(f"Manifest: {path_to_posix(manifest_path)}")
    if transcript_result:
        print(f"Transcript JSON: {path_to_posix(transcript_result['json_path'])}")
        print(f"Transcript Markdown: {path_to_posix(transcript_result['markdown_path'])}")
    if prompt_result:
        print(f"Summary Prompt: {path_to_posix(prompt_result['prompt_path'])}")
        print(f"Final Summary Path: {path_to_posix(prompt_result['summary_path'])}")
        print("Agent should read the summary prompt file above to generate the final summary.")

    return {
        "fetch": fetch_result,
        "transcript": transcript_result,
        "prompt": prompt_result,
    }


def main() -> int:
    options = PipelineOptions.from_args(parse_args())
    try:
        run_pipeline(options)
    except fetch_audio.CookieRequiredError as exc:
        print(f"Error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
