import os
from pathlib import Path


def main() -> None:
    try:
        import ffmpeg_binaries as ffmpeg

        ffmpeg.init()
        ffmpeg_path = Path(str(ffmpeg.FFMPEG_PATH))
    except Exception:
        return

    bin_dir = ffmpeg_path.parent if ffmpeg_path.is_file() else ffmpeg_path
    exe_suffix = ".exe" if os.name == "nt" else ""
    ffmpeg_exe = bin_dir / f"ffmpeg{exe_suffix}"
    ffprobe_exe = bin_dir / f"ffprobe{exe_suffix}"

    if ffmpeg_exe.exists() and ffprobe_exe.exists():
        print(ffmpeg_exe)
        print(ffprobe_exe)


if __name__ == "__main__":
    main()
