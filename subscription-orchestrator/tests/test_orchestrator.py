"""Unit tests for subscription-orchestrator probe and runner script."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import orchestrator as orch


def test_find_executable_uses_localappdata_fallback_for_agy(tmp_path: Path) -> None:
    agy_exe = tmp_path / "agy" / "bin" / "agy.exe"
    agy_exe.parent.mkdir(parents=True)
    agy_exe.write_bytes(b"")

    found = orch.find_executable(
        "agy",
        env={"LOCALAPPDATA": str(tmp_path)},
        which=lambda _name: None,
        home=tmp_path / "home",
    )
    assert found == agy_exe


def test_find_executable_prefers_env_override(tmp_path: Path) -> None:
    custom = tmp_path / "custom-codex.exe"
    custom.write_bytes(b"")

    found = orch.find_executable(
        "codex",
        env={"ORCHESTRATOR_CODEX_BIN": str(custom)},
        which=lambda _name: "/usr/bin/codex",
        home=tmp_path,
    )
    assert found == custom


def test_parse_agy_models_filters_noise_and_extracts_slugs() -> None:
    sample = (
        "Fetching available models...\n"
        "gemini-4-neon-high\tGemini 4 Neon (High)\n"
        "gemini-4-neon-xhigh\tGemini 4 Neon (XHigh)\n"
        "claude-opus-5-5-high\tClaude Opus 5.5 (High)\n"
        "\n"
        "invalid line without tab\n"
    )
    parsed = orch.parse_agy_models(sample)
    assert [m.slug for m in parsed] == [
        "gemini-4-neon-high",
        "gemini-4-neon-xhigh",
        "claude-opus-5-5-high",
    ]
    assert parsed[2].display_name == "Claude Opus 5.5 (High)"


def test_load_codex_models_cache_reads_supported_efforts(tmp_path: Path) -> None:
    cache_file = tmp_path / "models_cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-6.2-sol",
                        "supported_reasoning_levels": [
                            {"effort": "low"},
                            {"effort": "high"},
                            {"effort": "xhigh"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = orch.load_codex_models_cache(cache_file)
    assert loaded == {"gpt-6.2-sol": ["low", "high", "xhigh"]}


def test_resolve_agy_model_profiles_and_limits() -> None:
    assert orch.resolve_agy_model("neon", "xhigh") == ("gemini-4-neon-xhigh", False, False)
    assert orch.resolve_agy_model("opus", "high") == ("claude-opus-5-5-high", True, False)
    assert orch.resolve_agy_model("gemini-4-neon", "medium") == (
        "gemini-4-neon-medium",
        False,
        False,
    )
    assert orch.resolve_agy_model("custom-model", "high") == ("custom-model", False, True)

    with pytest.raises(orch.OrchestratorError) as exc_info:
        orch.resolve_agy_model("opus", "xhigh")
    assert exc_info.value.exit_code == orch.EXIT_USAGE_OR_POLICY


def test_build_agy_command_enforces_opus_plan_only_policy(tmp_path: Path) -> None:
    exe = tmp_path / "agy.exe"

    cmd, stdin_payload = orch.build_agy_command(
        exe,
        prompt="Design a dashboard layout",
        profile_or_model="opus",
        effort="high",
    )
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-opus-5-5-high"
    assert "--mode" in cmd and cmd[cmd.index("--mode") + 1] == "plan"
    assert "--effort" not in cmd
    assert stdin_payload is None
    assert "[Orchestrator Guard]" in cmd[cmd.index("-p") + 1]

    with pytest.raises(orch.OrchestratorError) as exc_info:
        orch.build_agy_command(
            exe,
            prompt="Implement the dashboard",
            profile_or_model="opus",
            effort="high",
            mode="accept-edits",
        )
    assert exc_info.value.exit_code == orch.EXIT_USAGE_OR_POLICY

    allowed_cmd, _ = orch.build_agy_command(
        exe,
        prompt="Implement the dashboard",
        profile_or_model="opus",
        effort="high",
        mode="accept-edits",
        allow_opus_write=True,
    )
    assert allowed_cmd[allowed_cmd.index("--mode") + 1] == "accept-edits"


def test_build_agy_command_routes_long_prompt_to_stdin(tmp_path: Path) -> None:
    exe = tmp_path / "agy.exe"
    huge_prompt = "x" * (orch.LONG_PROMPT_THRESHOLD + 100)
    cmd, stdin_payload = orch.build_agy_command(
        exe,
        prompt=huge_prompt,
        profile_or_model="neon",
        effort="high",
    )
    assert cmd[cmd.index("--mode") + 1] == "accept-edits"
    assert stdin_payload is not None
    assert huge_prompt in stdin_payload
    assert len(cmd[cmd.index("-p") + 1]) < 200


def test_build_codex_command_exec_and_review(tmp_path: Path) -> None:
    exe = tmp_path / "codex.exe"

    cmd, stdin_payload = orch.build_codex_command(
        exe,
        prompt="Fix the bug in parser.py",
        effort="xhigh",
        sandbox="workspace-write",
        cwd=tmp_path,
    )
    assert cmd[:8] == [
        str(exe),
        "exec",
        "-m",
        "gpt-6.2-sol",
        "-c",
        "model_reasoning_effort=xhigh",
        "-s",
        "workspace-write",
    ]
    assert "-C" in cmd and cmd[-1] == "-"
    assert stdin_payload is not None and "[Orchestrator Guard]" in stdin_payload

    review_cmd, review_stdin = orch.build_codex_command(
        exe,
        prompt=None,
        review=True,
        base="main",
        cwd=tmp_path,
    )
    assert review_cmd[:6] == [
        str(exe),
        "exec",
        "review",
        "-m",
        "gpt-6.2-sol",
        "-c",
    ]
    assert "--base" in review_cmd
    assert "-s" not in review_cmd
    assert "-C" not in review_cmd
    assert review_stdin is None


def test_child_env_strips_paid_api_keys_and_sets_guard() -> None:
    base = {
        "PATH": "/bin",
        "OPENAI_API_KEY": "sk-test",
        "GEMINI_API_KEY": "gm-test",
        "GOOGLE_API_KEY": "gg-test",
    }
    assert orch.detect_billing_risk_env(base) == [
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ]

    codex_env = orch.build_child_env(base, strip_keys=("OPENAI_API_KEY",))
    assert "OPENAI_API_KEY" not in codex_env
    assert codex_env["ORCHESTRATOR_CHILD_HARNESS"] == "1"

    agy_env = orch.build_child_env(base, strip_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    assert "GEMINI_API_KEY" not in agy_env
    assert "GOOGLE_API_KEY" not in agy_env
    assert agy_env["ORCHESTRATOR_CHILD_HARNESS"] == "1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Error 429: Too Many Requests", True),
        ("You have reached your usage limit. Try again later.", True),
        ("RESOURCE_EXHAUSTED: quota exceeded for model", True),
        ("SyntaxError: unexpected indent at line 12", False),
    ],
)
def test_classify_quota_error(text: str, expected: bool) -> None:
    assert (orch.classify_quota_error(text) is not None) is expected


def test_probe_codex_and_agy_with_mock_runners(tmp_path: Path) -> None:
    codex_exe = tmp_path / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
    codex_exe.parent.mkdir(parents=True)
    codex_exe.write_bytes(b"")

    agy_exe = tmp_path / "agy" / "bin" / "agy.exe"
    agy_exe.parent.mkdir(parents=True)
    agy_exe.write_bytes(b"")

    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-6.2-sol",
                        "supported_reasoning_levels": [{"effort": "high"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    env = {"LOCALAPPDATA": str(tmp_path), "CODEX_HOME": str(codex_home)}

    def codex_runner(cmd: object, _timeout: int, _env: object) -> tuple[int, str, str]:
        args = list(cmd) if isinstance(cmd, (list, tuple)) else []
        if "--version" in args:
            return (0, "codex-cli 0.172.0", "")
        return (0, "Logged in using ChatGPT", "")

    def agy_runner(cmd: object, _timeout: int, _env: object) -> tuple[int, str, str]:
        args = list(cmd) if isinstance(cmd, (list, tuple)) else []
        if "--version" in args:
            return (0, "1.5.18", "")
        return (
            0,
            (
                "Fetching available models...\n"
                "gemini-4-neon-high\tGemini 4 Neon (High)\n"
                "claude-opus-5-5-high\tClaude Opus 5.5 (High)\n"
            ),
            "",
        )

    codex_probe = orch.probe_codex(env=env, home=tmp_path, runner=codex_runner)
    agy_probe = orch.probe_agy(env=env, home=tmp_path, runner=agy_runner)

    assert codex_probe.status == "ready"
    assert codex_probe.target_models == {"gpt-6.2-sol": ["high"]}
    assert agy_probe.status == "ready"
    assert agy_probe.target_models == {
        "neon": ["gemini-4-neon-high"],
        "opus": ["claude-opus-5-5-high"],
    }


def test_main_blocks_recursive_call_and_reports_quota_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_bytes(b"")
    monkeypatch.setenv("ORCHESTRATOR_AGY_BIN", str(fake_agy))

    # Dry-run succeeds
    rc_dry = orch.main(["run-agy", "--profile", "opus", "--prompt", "plan UI", "--dry-run"])
    assert rc_dry == orch.EXIT_OK

    # Opus write blocked
    rc_policy = orch.main(
        ["run-agy", "--profile", "opus", "--mode", "accept-edits", "--prompt", "edit UI"]
    )
    assert rc_policy == orch.EXIT_USAGE_OR_POLICY

    # Recursive child harness blocked
    monkeypatch.setenv("ORCHESTRATOR_CHILD_HARNESS", "1")
    rc_recursive = orch.main(["run-agy", "--profile", "neon", "--prompt", "implement UI"])
    assert rc_recursive == orch.EXIT_USAGE_OR_POLICY

    # Quota error mapped to EXIT_QUOTA_EXHAUSTED (3)
    monkeypatch.delenv("ORCHESTRATOR_CHILD_HARNESS")

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[str(fake_agy)],
            returncode=1,
            stdout="",
            stderr="Error 429: quota exceeded for gemini-4-neon-high",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc_quota = orch.main(["run-agy", "--profile", "neon", "--prompt", "implement UI"])
    assert rc_quota == orch.EXIT_QUOTA_EXHAUSTED
