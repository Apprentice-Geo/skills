"""Probe and cross-harness runner for ChatGPT Plus (Codex) and Google AI Pro (Antigravity)."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

ToolName = Literal["codex", "agy"]

EXIT_OK = 0
EXIT_EXEC_ERROR = 1
EXIT_USAGE_OR_POLICY = 2
EXIT_QUOTA_EXHAUSTED = 3

DEFAULT_CODEX_MODEL = "gpt-6.2-sol"
CODEX_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
NEON_EFFORTS = ("low", "medium", "high", "xhigh")
OPUS_EFFORTS = ("low", "medium", "high")
AGY_EFFORT_SUFFIXES = ("low", "medium", "high", "xhigh", "max")

LONG_PROMPT_THRESHOLD = 8000

ANTI_RECURSION_GUARD = (
    "[Orchestrator Guard] You are running as a delegated child harness. "
    "Do not invoke external agent CLIs (such as `codex` or `agy`) or recursively "
    "delegate back to another harness. Complete the requested task directly within "
    "your current workspace permissions."
)

QUOTA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b429\b", re.IGNORECASE),
    re.compile(r"rate[\s_-]*limit", re.IGNORECASE),
    re.compile(r"usage[\s_-]*limit", re.IGNORECASE),
    re.compile(r"quota[\s_-]*(exceeded|exhausted|reached|limit)", re.IGNORECASE),
    re.compile(r"insufficient[\s_-]*quota", re.IGNORECASE),
    re.compile(r"resource[\s_-]*exhausted", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"credits?[\s_-]*(exhausted|depleted|insufficient)", re.IGNORECASE),
    re.compile(r"(hit|reached)\s+(your\s+)?(usage|message|rate)\s+limit", re.IGNORECASE),
)

BILLING_ENV_KEYS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


class OrchestratorError(Exception):
    """Error carrying a structured CLI exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_USAGE_OR_POLICY) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class AgyModelInfo:
    slug: str
    display_name: str


@dataclass(frozen=True)
class HarnessProbe:
    name: str
    status: Literal["ready", "not_installed", "unauthenticated", "error"]
    executable: str | None
    version: str | None
    auth_summary: str | None
    target_models: dict[str, list[str]]
    notes: list[str]


@dataclass(frozen=True)
class ProbeReport:
    codex: HarnessProbe
    agy: HarnessProbe
    billing_risk_env: list[str]
    nested_child_env: bool


def find_executable(
    tool: ToolName,
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    home: Path | None = None,
) -> Path | None:
    """Locate `codex` or `agy`, checking PATH first and then common install paths."""
    env_map = os.environ if env is None else env
    user_home = Path.home() if home is None else home

    env_override = env_map.get(f"ORCHESTRATOR_{tool.upper()}_BIN")
    if env_override:
        override_path = Path(env_override)
        if override_path.is_file():
            return override_path

    found = which(tool)
    if found:
        return Path(found)

    candidates: list[Path] = []
    local_app_data = env_map.get("LOCALAPPDATA")
    app_data = env_map.get("APPDATA")

    if tool == "agy":
        if local_app_data:
            candidates.append(Path(local_app_data) / "agy" / "bin" / "agy.exe")
        candidates.extend(
            [
                user_home / ".local" / "bin" / "agy",
                Path("/usr/local/bin/agy"),
            ]
        )
    else:
        if local_app_data:
            candidates.append(
                Path(local_app_data) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
            )
        if app_data:
            candidates.append(Path(app_data) / "npm" / "codex.cmd")
        candidates.extend(
            [
                user_home / ".local" / "bin" / "codex",
                Path("/usr/local/bin/codex"),
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def parse_agy_models(output: str) -> list[AgyModelInfo]:
    """Parse tab-separated `agy models` output into structured model entries."""
    models: list[AgyModelInfo] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("fetching "):
            continue
        if "\t" in line:
            slug, display_name = line.split("\t", 1)
            slug = slug.strip()
            display_name = display_name.strip()
            if slug and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slug):
                models.append(AgyModelInfo(slug=slug, display_name=display_name))
    return models


def load_codex_models_cache(cache_path: Path) -> dict[str, list[str]]:
    """Read `~/.codex/models_cache.json` to extract model slugs and reasoning levels."""
    if not cache_path.is_file():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    models = payload.get("models")
    if not isinstance(models, list):
        return {}
    result: dict[str, list[str]] = {}
    for item in models:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        levels_raw = item.get("supported_reasoning_levels")
        levels: list[str] = []
        if isinstance(levels_raw, list):
            for level in levels_raw:
                if isinstance(level, dict) and isinstance(level.get("effort"), str):
                    levels.append(level["effort"])
        result[slug] = levels
    return result


def detect_billing_risk_env(env: Mapping[str, str]) -> list[str]:
    """Return API key environment variable names that are currently non-empty."""
    return [key for key in BILLING_ENV_KEYS if env.get(key, "").strip()]


def classify_quota_error(text: str) -> str | None:
    """Return the matched quota/rate-limit snippet if output indicates exhausted quota."""
    for pattern in QUOTA_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def build_child_env(
    base_env: Mapping[str, str],
    *,
    strip_keys: Sequence[str],
) -> dict[str, str]:
    """Create a child environment with paid-API keys stripped and child marker set."""
    env = {k: v for k, v in base_env.items() if k not in strip_keys}
    env["ORCHESTRATOR_CHILD_HARNESS"] = "1"
    return env


def decorate_prompt(prompt: str, *, include_guard: bool) -> str:
    """Prepend the anti-recursion guard unless disabled."""
    cleaned = prompt.strip()
    if not cleaned:
        raise OrchestratorError("Prompt must not be empty.", EXIT_USAGE_OR_POLICY)
    if not include_guard:
        return cleaned
    return f"{ANTI_RECURSION_GUARD}\n\n{cleaned}"


def resolve_agy_model(profile_or_model: str, effort: str) -> tuple[str, bool, bool]:
    """Resolve an Antigravity shortcut profile or raw model ID.

    Returns `(model_slug, is_opus, pass_effort_flag)`.
    """
    key = profile_or_model.strip().lower()
    if key in {"neon", "gemini-4-neon"}:
        if effort not in NEON_EFFORTS:
            raise OrchestratorError(
                f"Gemini 4 Neon supports efforts {', '.join(NEON_EFFORTS)}; got '{effort}'.",
                EXIT_USAGE_OR_POLICY,
            )
        return (f"gemini-4-neon-{effort}", False, False)

    if key in {"opus", "opus-5.5", "claude-opus-5-5"}:
        if effort not in OPUS_EFFORTS:
            raise OrchestratorError(
                f"Claude Opus 5.5 supports efforts {', '.join(OPUS_EFFORTS)}; got '{effort}'.",
                EXIT_USAGE_OR_POLICY,
            )
        return (f"claude-opus-5-5-{effort}", True, False)

    raw = profile_or_model.strip()
    is_opus = raw.lower().startswith("claude-opus-5-5") or "opus-5-5" in raw.lower()
    has_effort_suffix = any(raw.lower().endswith(f"-{tier}") for tier in AGY_EFFORT_SUFFIXES)
    return (raw, is_opus, not has_effort_suffix)


def build_codex_command(
    executable: Path,
    *,
    prompt: str | None,
    model: str = DEFAULT_CODEX_MODEL,
    effort: str = "high",
    sandbox: str = "read-only",
    cwd: Path | None = None,
    review: bool = False,
    uncommitted: bool = False,
    base: str | None = None,
    commit: str | None = None,
    title: str | None = None,
    images: Sequence[Path] = (),
    output_file: Path | None = None,
    output_schema: Path | None = None,
    ephemeral: bool = False,
    skip_git_repo_check: bool = False,
    json_output: bool = False,
    include_guard: bool = True,
) -> tuple[list[str], str | None]:
    """Construct a Codex CLI command and optional stdin payload."""
    if effort not in CODEX_EFFORTS:
        raise OrchestratorError(
            f"Codex effort must be one of {', '.join(CODEX_EFFORTS)}; got '{effort}'.",
            EXIT_USAGE_OR_POLICY,
        )

    cmd: list[str] = [str(executable), "exec"]
    if review:
        cmd.append("review")
        cmd.extend(["-m", model, "-c", f"model_reasoning_effort={effort}"])
        if uncommitted:
            cmd.append("--uncommitted")
        if base:
            cmd.extend(["--base", base])
        if commit:
            cmd.extend(["--commit", commit])
        if title:
            cmd.extend(["--title", title])
        if ephemeral:
            cmd.append("--ephemeral")
        if skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        if json_output:
            cmd.append("--json")
        if output_file:
            cmd.extend(["-o", str(output_file)])
        if output_schema:
            cmd.extend(["--output-schema", str(output_schema)])

        if prompt is not None and prompt.strip():
            stdin_payload = decorate_prompt(prompt, include_guard=include_guard)
            cmd.append("-")
            return (cmd, stdin_payload)

        if not (uncommitted or base or commit):
            cmd.append("--uncommitted")
        return (cmd, None)

    if prompt is None:
        raise OrchestratorError("Codex exec requires a prompt.", EXIT_USAGE_OR_POLICY)

    cmd.extend(["-m", model, "-c", f"model_reasoning_effort={effort}", "-s", sandbox])
    if cwd is not None:
        cmd.extend(["-C", str(cwd)])
    for image_path in images:
        cmd.extend(["-i", str(image_path)])
    if ephemeral:
        cmd.append("--ephemeral")
    if skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    if json_output:
        cmd.append("--json")
    if output_file:
        cmd.extend(["-o", str(output_file)])
    if output_schema:
        cmd.extend(["--output-schema", str(output_schema)])
    cmd.append("-")

    stdin_payload = decorate_prompt(prompt, include_guard=include_guard)
    return (cmd, stdin_payload)


def build_agy_command(
    executable: Path,
    *,
    prompt: str,
    profile_or_model: str = "neon",
    effort: str = "high",
    mode: str | None = None,
    allow_opus_write: bool = False,
    add_dirs: Sequence[Path] = (),
    output_format: str = "text",
    json_schema: Path | None = None,
    print_timeout: str | None = None,
    sandbox: bool = False,
    continue_last: bool = False,
    conversation_id: str | None = None,
    disable_slash_commands: bool = False,
    include_guard: bool = True,
) -> tuple[list[str], str | None]:
    """Construct an Antigravity CLI (`agy`) command with Opus 5.5 planning guardrail."""
    model_slug, is_opus, pass_effort_flag = resolve_agy_model(profile_or_model, effort)

    effective_mode = mode if mode is not None else ("plan" if is_opus else "accept-edits")
    if is_opus and effective_mode != "plan" and not allow_opus_write:
        raise OrchestratorError(
            "Policy block: Claude Opus 5.5 quota is scarce and reserved for planning/design "
            "without code edits (--mode plan). Use --profile neon for implementation, or pass "
            "--allow-opus-write only with explicit user approval.",
            EXIT_USAGE_OR_POLICY,
        )

    final_prompt = decorate_prompt(prompt, include_guard=include_guard)
    stdin_payload: str | None = None
    if len(final_prompt) > LONG_PROMPT_THRESHOLD:
        prompt_arg = "Execute the complete task instructions provided on stdin."
        stdin_payload = final_prompt
    else:
        prompt_arg = final_prompt

    cmd: list[str] = [
        str(executable),
        "-p",
        prompt_arg,
        "--model",
        model_slug,
        "--mode",
        effective_mode,
        "--output-format",
        output_format,
    ]
    if pass_effort_flag:
        cmd.extend(["--effort", effort])
    for extra_dir in add_dirs:
        cmd.extend(["--add-dir", str(extra_dir)])
    if json_schema:
        cmd.extend(["--json-schema", str(json_schema)])
    if print_timeout:
        cmd.extend(["--print-timeout", print_timeout])
    if sandbox:
        cmd.append("--sandbox")
    if continue_last:
        cmd.append("--continue")
    if conversation_id:
        cmd.extend(["--conversation", conversation_id])
    if disable_slash_commands:
        cmd.append("--disable-slash-commands")
    return (cmd, stdin_payload)


def _run_capture(
    cmd: Sequence[str],
    *,
    timeout: int = 15,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=dict(env) if env is not None else None,
            check=False,
        )
        return (completed.returncode, completed.stdout.strip(), completed.stderr.strip())
    except subprocess.TimeoutExpired:
        return (124, "", f"command timed out after {timeout}s")
    except OSError as exc:
        return (127, "", str(exc))


def probe_codex(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    runner: Callable[[Sequence[str], int, Mapping[str, str] | None], tuple[int, str, str]]
    | None = None,
) -> HarnessProbe:
    """Probe Codex CLI installation, ChatGPT subscription login, and cached target model."""
    env_map = os.environ if env is None else env
    user_home = Path.home() if home is None else home
    exe = find_executable("codex", env=env_map, home=user_home)
    if exe is None:
        return HarnessProbe(
            name="codex",
            status="not_installed",
            executable=None,
            version=None,
            auth_summary=None,
            target_models={},
            notes=["Codex CLI not found on PATH or standard Windows/POSIX locations."],
        )

    def _exec(
        cmd: Sequence[str], timeout: int, run_env: Mapping[str, str] | None
    ) -> tuple[int, str, str]:
        if runner is not None:
            return runner(cmd, timeout, run_env)
        return _run_capture(cmd, timeout=timeout, env=run_env)

    notes: list[str] = []
    ver_rc, ver_out, ver_err = _exec([str(exe), "--version"], 10, None)
    version = ver_out.splitlines()[0].strip() if ver_rc == 0 and ver_out else None
    if ver_rc != 0 and ver_err:
        notes.append(f"version check failed: {ver_err}")

    safe_env = build_child_env(env_map, strip_keys=("OPENAI_API_KEY",))
    auth_rc, auth_out, auth_err = _exec([str(exe), "login", "status"], 10, safe_env)
    auth_text = auth_out or auth_err
    status: Literal["ready", "not_installed", "unauthenticated", "error"]
    if auth_rc == 0 and "chatgpt" in auth_text.lower():
        status = "ready"
    elif auth_rc == 0:
        status = "unauthenticated"
        notes.append("Codex is logged in, but not confirmed as ChatGPT subscription auth.")
    else:
        status = "unauthenticated"
        if auth_text:
            notes.append(f"login status: {auth_text}")

    codex_home = Path(env_map.get("CODEX_HOME", str(user_home / ".codex")))
    cached_models = load_codex_models_cache(codex_home / "models_cache.json")
    target_models: dict[str, list[str]] = {}
    if DEFAULT_CODEX_MODEL in cached_models:
        target_models[DEFAULT_CODEX_MODEL] = cached_models[DEFAULT_CODEX_MODEL]
    elif cached_models:
        notes.append(f"{DEFAULT_CODEX_MODEL} not found in local models_cache.json.")

    return HarnessProbe(
        name="codex",
        status=status,
        executable=str(exe),
        version=version,
        auth_summary=auth_text or None,
        target_models=target_models,
        notes=notes,
    )


def probe_agy(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    skip_network: bool = False,
    runner: Callable[[Sequence[str], int, Mapping[str, str] | None], tuple[int, str, str]]
    | None = None,
) -> HarnessProbe:
    """Probe Antigravity CLI (`agy`) installation, auth, and Neon / Opus 5.5 availability."""
    env_map = os.environ if env is None else env
    user_home = Path.home() if home is None else home
    exe = find_executable("agy", env=env_map, home=user_home)
    if exe is None:
        return HarnessProbe(
            name="agy",
            status="not_installed",
            executable=None,
            version=None,
            auth_summary=None,
            target_models={},
            notes=["Antigravity CLI (`agy`) not found on PATH or standard install locations."],
        )

    def _exec(
        cmd: Sequence[str], timeout: int, run_env: Mapping[str, str] | None
    ) -> tuple[int, str, str]:
        if runner is not None:
            return runner(cmd, timeout, run_env)
        return _run_capture(cmd, timeout=timeout, env=run_env)

    notes: list[str] = []
    ver_rc, ver_out, ver_err = _exec([str(exe), "--version"], 10, None)
    version = ver_out.splitlines()[0].strip() if ver_rc == 0 and ver_out else None
    if ver_rc != 0 and ver_err:
        notes.append(f"version check failed: {ver_err}")

    if skip_network:
        cli_dir = user_home / ".gemini" / "antigravity-cli"
        has_state = cli_dir.is_dir()
        return HarnessProbe(
            name="agy",
            status="ready" if has_state else "unauthenticated",
            executable=str(exe),
            version=version,
            auth_summary="local state present (--fast skipped live model check)"
            if has_state
            else "no local ~/.gemini/antigravity-cli state found",
            target_models={},
            notes=notes,
        )

    safe_env = build_child_env(env_map, strip_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    mod_rc, mod_out, mod_err = _exec([str(exe), "models"], 20, safe_env)
    combined = "\n".join(part for part in (mod_out, mod_err) if part)
    models = parse_agy_models(combined)
    neon_slugs = [m.slug for m in models if m.slug.startswith("gemini-4-neon-")]
    opus_slugs = [m.slug for m in models if m.slug.startswith("claude-opus-5-5-")]

    target_models: dict[str, list[str]] = {}
    if neon_slugs:
        target_models["neon"] = neon_slugs
    if opus_slugs:
        target_models["opus"] = opus_slugs

    if mod_rc == 0 and models:
        status: Literal["ready", "not_installed", "unauthenticated", "error"] = "ready"
        auth_summary = f"authenticated ({len(models)} models available)"
    elif any(token in combined.lower() for token in ("login", "auth", "sign in", "credential")):
        status = "unauthenticated"
        auth_summary = mod_err or mod_out or "authentication required"
    else:
        status = "error"
        auth_summary = mod_err or mod_out or f"agy models exited with {mod_rc}"

    if status == "ready" and not neon_slugs:
        notes.append("No gemini-4-neon-* model found in `agy models` output.")
    if status == "ready" and not opus_slugs:
        notes.append("No claude-opus-5-5-* model found in `agy models` output.")

    return HarnessProbe(
        name="agy",
        status=status,
        executable=str(exe),
        version=version,
        auth_summary=auth_summary,
        target_models=target_models,
        notes=notes,
    )


def format_probe_text(report: ProbeReport) -> str:
    """Render a compact, Agent-readable status summary."""
    lines: list[str] = ["=== Subscription Harness Probe ==="]
    for probe in (report.codex, report.agy):
        version_part = f" ({probe.version})" if probe.version else ""
        lines.append(f"[{probe.name}] status={probe.status}{version_part}")
        if probe.executable:
            lines.append(f"  exe: {probe.executable}")
        if probe.auth_summary:
            lines.append(f"  auth: {probe.auth_summary}")
        if probe.target_models:
            summary = "; ".join(
                f"{group}=[{', '.join(values)}]" for group, values in probe.target_models.items()
            )
            lines.append(f"  target_models: {summary}")
        for note in probe.notes:
            lines.append(f"  note: {note}")

    if report.billing_risk_env:
        lines.append(
            "WARN billing_risk_env: "
            + ", ".join(report.billing_risk_env)
            + " (stripped automatically by run-codex / run-agy to avoid paid API billing)"
        )
    if report.nested_child_env:
        lines.append(
            "WARN nested_child_env: ORCHESTRATOR_CHILD_HARNESS=1 detected; "
            "do not recursively delegate to another harness."
        )
    return "\n".join(lines)


def _read_prompt(
    prompt: str | None,
    prompt_file: Path | None,
    *,
    required: bool,
) -> str | None:
    if prompt is not None and prompt_file is not None:
        raise OrchestratorError("Specify either --prompt or --prompt-file, not both.")
    if prompt_file is not None:
        try:
            return prompt_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise OrchestratorError(f"Failed to read prompt file: {exc}") from exc
    if prompt == "-":
        return sys.stdin.read()
    if prompt is not None:
        return prompt
    if required:
        raise OrchestratorError(
            "A prompt is required (--prompt TEXT, --prompt -, or --prompt-file PATH)."
        )
    return None


def _execute_delegated(
    tool_label: str,
    cmd: Sequence[str],
    *,
    stdin_payload: str | None,
    cwd: Path | None,
    child_env: Mapping[str, str],
    timeout: int | None,
    dry_run: bool,
) -> int:
    if dry_run:
        payload = {
            "tool": tool_label,
            "cwd": str(cwd) if cwd else None,
            "command": list(cmd),
            "stdin_bytes": len(stdin_payload.encode("utf-8")) if stdin_payload else 0,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    if os.environ.get("ORCHESTRATOR_CHILD_HARNESS") == "1":
        raise OrchestratorError(
            "Blocked recursive harness call: ORCHESTRATOR_CHILD_HARNESS=1 is already set.",
            EXIT_USAGE_OR_POLICY,
        )

    try:
        completed = subprocess.run(
            list(cmd),
            input=stdin_payload,
            cwd=str(cwd) if cwd else None,
            env=dict(child_env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[orchestrator] {tool_label} timed out after {timeout}s.",
            file=sys.stderr,
        )
        return EXIT_EXEC_ERROR
    except OSError as exc:
        print(f"[orchestrator] Failed to launch {tool_label}: {exc}", file=sys.stderr)
        return EXIT_USAGE_OR_POLICY

    if completed.stdout:
        sys.stdout.write(completed.stdout)
        if not completed.stdout.endswith("\n"):
            sys.stdout.write("\n")

    combined = f"{completed.stdout}\n{completed.stderr}"
    quota_hit = classify_quota_error(combined)
    if completed.returncode != 0 and quota_hit is not None:
        print(
            f"[orchestrator] QUOTA_EXHAUSTED ({tool_label}): detected '{quota_hit}'. "
            "Stop using this harness for the remainder of the task and fall back.",
            file=sys.stderr,
        )
        if completed.stderr.strip():
            print(completed.stderr.strip(), file=sys.stderr)
        return EXIT_QUOTA_EXHAUSTED

    if completed.returncode != 0:
        if completed.stderr.strip():
            print(completed.stderr.strip(), file=sys.stderr)
        return EXIT_EXEC_ERROR

    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.orchestrator",
        description="Probe and invoke Codex / Antigravity CLIs under subscription routing rules.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    probe_p = sub.add_parser("probe", help="Check codex and agy availability, auth, and models.")
    probe_p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    probe_p.add_argument(
        "--fast",
        action="store_true",
        help="Skip live `agy models` network query and only check local binary/state.",
    )

    codex_p = sub.add_parser(
        "run-codex", help="Delegate an engineering or review task to Codex CLI."
    )
    codex_p.add_argument("--prompt", help="Task prompt text, or '-' to read from stdin.")
    codex_p.add_argument("--prompt-file", type=Path, help="Read task prompt from a UTF-8 file.")
    codex_p.add_argument("--model", default=DEFAULT_CODEX_MODEL, help="Codex model slug.")
    codex_p.add_argument(
        "--effort",
        choices=CODEX_EFFORTS,
        default="high",
        help="Reasoning effort (default: high).",
    )
    codex_p.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default="read-only",
        help="Sandbox mode for `codex exec` (default: read-only).",
    )
    codex_p.add_argument("--cwd", type=Path, help="Working directory for the delegated task.")
    codex_p.add_argument("--review", action="store_true", help="Run `codex exec review`.")
    codex_p.add_argument("--uncommitted", action="store_true", help="Review uncommitted changes.")
    codex_p.add_argument("--base", help="Review changes against base branch.")
    codex_p.add_argument("--commit", help="Review changes in a specific commit SHA.")
    codex_p.add_argument("--title", help="Optional title for review summary.")
    codex_p.add_argument("--image", type=Path, action="append", default=[], help="Attach image(s).")
    codex_p.add_argument("-o", "--output-file", type=Path, help="Write final message to file.")
    codex_p.add_argument("--output-schema", type=Path, help="Enforce JSON schema output.")
    codex_p.add_argument("--ephemeral", action="store_true", help="Do not persist session.")
    codex_p.add_argument(
        "--skip-git-repo-check",
        action="store_true",
        help="Allow running outside a Git repository.",
    )
    codex_p.add_argument("--json", action="store_true", dest="json_output", help="Emit JSONL.")
    codex_p.add_argument("--timeout", type=int, help="Optional timeout in seconds.")
    codex_p.add_argument(
        "--no-guard", action="store_true", help="Omit anti-recursion prompt guard."
    )
    codex_p.add_argument("--dry-run", action="store_true", help="Print resolved command only.")

    agy_p = sub.add_parser(
        "run-agy",
        help="Delegate UI/frontend/visual planning or implementation to Antigravity CLI (`agy`).",
    )
    agy_p.add_argument("--prompt", help="Task prompt text, or '-' to read from stdin.")
    agy_p.add_argument("--prompt-file", type=Path, help="Read task prompt from a UTF-8 file.")
    agy_p.add_argument(
        "--profile",
        default="neon",
        help="Model profile ('neon' or 'opus') or an exact `agy models` slug (default: neon).",
    )
    agy_p.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default="high",
        help="Reasoning effort (default: high; Opus 5.5 supports low|medium|high).",
    )
    agy_p.add_argument(
        "--mode",
        choices=("plan", "accept-edits", "auto"),
        default=None,
        help="Execution mode (defaults to 'plan' for opus, 'accept-edits' for neon).",
    )
    agy_p.add_argument(
        "--allow-opus-write",
        action="store_true",
        help="Override Opus 5.5 plan-only guardrail (requires explicit user approval).",
    )
    agy_p.add_argument("--cwd", type=Path, help="Working directory for the delegated task.")
    agy_p.add_argument(
        "--add-dir", type=Path, action="append", default=[], help="Extra workspace dir."
    )
    agy_p.add_argument(
        "--output-format",
        choices=("text", "json", "stream-json"),
        default="text",
        help="Output format (default: text).",
    )
    agy_p.add_argument("--json-schema", type=Path, help="Enforce JSON schema output.")
    agy_p.add_argument("--print-timeout", help="Optional agy --print-timeout (e.g. 5m).")
    agy_p.add_argument("--timeout", type=int, help="Optional subprocess timeout in seconds.")
    agy_p.add_argument("--sandbox", action="store_true", help="Enable agy terminal sandbox.")
    agy_p.add_argument(
        "--continue", dest="continue_last", action="store_true", help="Continue last chat."
    )
    agy_p.add_argument("--conversation", help="Resume a specific conversation ID.")
    agy_p.add_argument(
        "--disable-slash-commands",
        action="store_true",
        help="Disable slash command and skill expansion in print mode.",
    )
    agy_p.add_argument("--no-guard", action="store_true", help="Omit anti-recursion prompt guard.")
    agy_p.add_argument("--dry-run", action="store_true", help="Print resolved command only.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "probe":
            report = ProbeReport(
                codex=probe_codex(),
                agy=probe_agy(skip_network=args.fast),
                billing_risk_env=detect_billing_risk_env(os.environ),
                nested_child_env=os.environ.get("ORCHESTRATOR_CHILD_HARNESS") == "1",
            )
            if args.format == "json":
                print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
            else:
                print(format_probe_text(report))
            any_ready = report.codex.status == "ready" or report.agy.status == "ready"
            return EXIT_OK if any_ready else EXIT_EXEC_ERROR

        if args.command == "run-codex":
            exe = find_executable("codex")
            if exe is None:
                raise OrchestratorError(
                    "Codex CLI not found. Install Codex CLI or check PATH before delegating.",
                    EXIT_USAGE_OR_POLICY,
                )
            prompt = _read_prompt(args.prompt, args.prompt_file, required=not args.review)
            cmd, stdin_payload = build_codex_command(
                exe,
                prompt=prompt,
                model=args.model,
                effort=args.effort,
                sandbox=args.sandbox,
                cwd=args.cwd,
                review=args.review,
                uncommitted=args.uncommitted,
                base=args.base,
                commit=args.commit,
                title=args.title,
                images=args.image,
                output_file=args.output_file,
                output_schema=args.output_schema,
                ephemeral=args.ephemeral,
                skip_git_repo_check=args.skip_git_repo_check,
                json_output=args.json_output,
                include_guard=not args.no_guard,
            )
            child_env = build_child_env(os.environ, strip_keys=("OPENAI_API_KEY",))
            return _execute_delegated(
                "codex",
                cmd,
                stdin_payload=stdin_payload,
                cwd=args.cwd if args.review else None,
                child_env=child_env,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )

        if args.command == "run-agy":
            exe = find_executable("agy")
            if exe is None:
                raise OrchestratorError(
                    "Antigravity CLI (`agy`) not found. Check PATH or %LOCALAPPDATA%\\agy\\bin.",
                    EXIT_USAGE_OR_POLICY,
                )
            prompt = _read_prompt(args.prompt, args.prompt_file, required=True)
            assert prompt is not None
            cmd, stdin_payload = build_agy_command(
                exe,
                prompt=prompt,
                profile_or_model=args.profile,
                effort=args.effort,
                mode=args.mode,
                allow_opus_write=args.allow_opus_write,
                add_dirs=args.add_dir,
                output_format=args.output_format,
                json_schema=args.json_schema,
                print_timeout=args.print_timeout,
                sandbox=args.sandbox,
                continue_last=args.continue_last,
                conversation_id=args.conversation,
                disable_slash_commands=args.disable_slash_commands,
                include_guard=not args.no_guard,
            )
            child_env = build_child_env(
                os.environ,
                strip_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            )
            return _execute_delegated(
                "agy",
                cmd,
                stdin_payload=stdin_payload,
                cwd=args.cwd,
                child_env=child_env,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )

    except OrchestratorError as exc:
        print(f"[orchestrator] {exc}", file=sys.stderr)
        return exc.exit_code

    return EXIT_USAGE_OR_POLICY


if __name__ == "__main__":
    raise SystemExit(main())
