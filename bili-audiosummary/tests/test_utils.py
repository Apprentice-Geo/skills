import importlib.util
import sys
import types
from pathlib import Path



def test_resolve_ffmpeg_location_ignores_legacy_tools_bin_fallback(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    legacy_bin_dir = workspace_tmp_path / "ffmpeg" / "bin"
    legacy_bin_dir.mkdir(parents=True)
    legacy_ffmpeg = legacy_bin_dir / "ffmpeg.exe"
    legacy_ffprobe = legacy_bin_dir / "ffprobe.exe"
    legacy_ffmpeg.write_bytes(b"")
    legacy_ffprobe.write_bytes(b"")

    fake_config = types.ModuleType("config")
    legacy_names = {
        "_".join(["PORTABLE", "FFMPEG", "BIN", "DIR"]): legacy_bin_dir,
        "_".join(["PORTABLE", "FFMPEG", "EXE"]): legacy_ffmpeg,
        "_".join(["PORTABLE", "FFPROBE", "EXE"]): legacy_ffprobe,
    }

    def get_legacy_config_value(name: str):
        if name in legacy_names:
            return legacy_names[name]
        raise AttributeError(name)

    fake_config.__getattr__ = get_legacy_config_value
    monkeypatch.setitem(sys.modules, "config", fake_config)

    utils_path = Path(__file__).resolve().parents[1] / "scripts" / "utils.py"
    spec = importlib.util.spec_from_file_location("isolated_utils", utils_path)
    assert spec is not None
    assert spec.loader is not None
    isolated_utils = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_utils)

    monkeypatch.setattr(isolated_utils.shutil, "which", lambda _name: None)
    monkeypatch.setattr(isolated_utils, "resolve_ffmpeg_binaries_location", lambda: None)

    assert isolated_utils.resolve_ffmpeg_location() is None
