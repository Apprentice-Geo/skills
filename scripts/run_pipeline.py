import argparse
from pathlib import Path
from typing import Any, Optional

import fetch_audio
import transcribe
from config import (
    SUMMARY_INSTRUCTIONS_PATH,
    SUMMARY_TEMPLATE_BY_LANGUAGE,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_AUDIO_SELECTOR,
    DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
    DEFAULT_TRANSCRIBE_DEVICE,
    DEFAULT_TRANSCRIBE_LANGUAGE,
    RESULTS_DIR,
)
from utils import ensure_dir, path_to_posix, read_json


def make_fetch_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        url=args.url,
        output_dir=args.output_dir,
        cookies=None,
        cookies_from_browser=None,
        playlist=args.playlist,
        skip_audio=args.skip_audio,
        fetch_subtitles=False,
        skip_subtitles=True,
        write_auto_subs=False,
        subtitle_langs=[],
        subtitle_format="srt/best",
        audio_selector=args.audio_selector,
        audio_format=args.audio_format,
        audio_quality=args.audio_quality,
        retries=args.retries,
        socket_timeout=args.socket_timeout,
        quiet=args.quiet,
    )


def make_transcribe_args(
    args: argparse.Namespace,
    manifest_path: Path,
    output_dir: Optional[Path],
) -> argparse.Namespace:
    return argparse.Namespace(
        input=None,
        manifest=manifest_path,
        audio=None,
        output_dir=output_dir,
        model=args.model,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        cpu_threads=args.cpu_threads,
        num_workers=args.num_workers,
        vad_filter=args.vad_filter,
        word_timestamps=args.word_timestamps,
    )


def normalize_template_language(language: Optional[str]) -> str:
    if not language:
        return "zh"

    value = language.lower()
    if value.startswith("zh"):
        return "zh"
    return value


def select_summary_template(language: Optional[str]) -> tuple[str, Path]:
    template_language = normalize_template_language(language)
    template_path = SUMMARY_TEMPLATE_BY_LANGUAGE.get(template_language)
    if template_path and template_path.exists():
        return template_language, template_path

    fallback_language = "zh"
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full Bilibili audio summary preparation pipeline: fetch audio, transcribe it, then build a summary prompt."
    )
    parser.add_argument("url", help="Bilibili video URL.")
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR)

    parser.add_argument("--playlist", action="store_true", help="Allow playlist/multi-entry downloads.")
    parser.add_argument("--skip-audio", action="store_true")
    parser.add_argument(
        "--audio-selector",
        default=DEFAULT_AUDIO_SELECTOR,
        help="yt-dlp format selector. Defaults to the lowest available audio stream.",
    )
    parser.add_argument("--audio-format", default=DEFAULT_AUDIO_CODEC)
    parser.add_argument("--audio-quality", default="0")
    parser.add_argument("--retries", type=int, default=10)
    parser.add_argument("--socket-timeout", type=int, default=30)
    parser.add_argument("--quiet", action="store_true")

    parser.add_argument("--skip-stt", action="store_true", help="Do not run STT.")
    parser.add_argument("--model", help="Model name or local faster-whisper model directory.")
    parser.add_argument("--language", default=DEFAULT_TRANSCRIBE_LANGUAGE)
    parser.add_argument("--device", default=DEFAULT_TRANSCRIBE_DEVICE)
    parser.add_argument("--compute-type", default=DEFAULT_TRANSCRIBE_COMPUTE_TYPE)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--cpu-threads", type=int, default=0)
    parser.add_argument("--num-workers", type=int, default=1)
    parser.add_argument("--vad-filter", dest="vad_filter", action="store_true", default=True)
    parser.add_argument("--no-vad-filter", dest="vad_filter", action="store_false")
    parser.add_argument("--word-timestamps", action="store_true")
    return parser.parse_args()


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    fetch_result = fetch_audio.run_fetch(make_fetch_args(args))
    manifest_path = fetch_result["manifest_path"]
    result_dir = fetch_result["paths"]["result"]

    transcript_result = None
    prompt_result = None

    if not args.skip_stt:
        if args.skip_audio:
            raise ValueError("STT requires audio. Remove --skip-audio or also pass --skip-stt.")

        transcribe_args = make_transcribe_args(args, manifest_path, result_dir)
        transcript_result = transcribe.run_transcribe(transcribe_args)
        prompt_result = write_summary_prompt(
            result_dir=result_dir,
            video_id=fetch_result["video_id"],
            transcript_markdown_path=transcript_result["markdown_path"],
            transcript_json_path=transcript_result["json_path"],
        )
    else:
        print("STT skipped: --skip-stt was set")

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
    args = parse_args()
    run_pipeline(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
