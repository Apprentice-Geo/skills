from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from scripts.asr.parallel.plan import AsrChunkPlan, MacroChunkPlan, ParallelAsrPlan
from scripts.asr.parallel.state import chunk_key

ZH_MIN_OVERLAP_TOKENS = 8
ZH_MIN_OVERLAP_SCORE = 0.6
EN_MIN_OVERLAP_TOKENS = 5
EN_MIN_OVERLAP_SCORE = 0.75


@dataclass(frozen=True)
class _TextToken:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class _OverlapMatch:
    b_token_count: int
    score: float


def _chunk_by_key(plan: ParallelAsrPlan) -> dict[str, AsrChunkPlan]:
    return {chunk_key(chunk): chunk for chunk in plan.asr_chunks}


def _macro_by_index(plan: ParallelAsrPlan) -> dict[int, MacroChunkPlan]:
    return {macro.index: macro for macro in plan.macro_chunks}


def _is_chinese_language(language: str) -> bool:
    return language.lower().startswith("zh")


def _tokenize_chinese_text(text: str) -> list[_TextToken]:
    return [
        _TextToken(ch.lower(), index, index + 1)
        for index, ch in enumerate(text)
        if ch.isalnum()
    ]


def _tokenize_english_text(text: str) -> list[_TextToken]:
    return [
        _TextToken(match.group(0).lower(), match.start(), match.end())
        for match in re.finditer(r"[A-Za-z0-9]+", text)
    ]


def _tokenize_text(text: str, language: str) -> list[_TextToken]:
    if _is_chinese_language(language):
        return _tokenize_chinese_text(text)
    return _tokenize_english_text(text)


def _overlap_settings(language: str) -> tuple[int, float]:
    if _is_chinese_language(language):
        return ZH_MIN_OVERLAP_TOKENS, ZH_MIN_OVERLAP_SCORE
    return EN_MIN_OVERLAP_TOKENS, EN_MIN_OVERLAP_SCORE


def _best_prefix_overlap(
    a_tokens: list[_TextToken],
    b_tokens: list[_TextToken],
    min_tokens: int,
    min_score: float,
    require_edge_matches: bool,
) -> _OverlapMatch | None:
    best: _OverlapMatch | None = None
    for index in range(len(a_tokens)):
        token_count = min(len(a_tokens) - index, len(b_tokens))
        if token_count < min_tokens:
            continue
        a_slice = a_tokens[index : index + token_count]
        b_slice = b_tokens[:token_count]
        if require_edge_matches and (
            a_slice[0].value != b_slice[0].value
            or a_slice[-1].value != b_slice[-1].value
        ):
            continue
        same_count = sum(
            left.value == right.value
            for left, right in zip(a_slice, b_slice)
        )
        score = same_count / token_count
        if score < min_score:
            continue
        if best is None or score > best.score:
            best = _OverlapMatch(token_count, score)
        elif score == best.score and token_count < best.b_token_count:
            best = _OverlapMatch(token_count, score)
    return best


def _trim_leading_separators(text: str, start: int) -> str:
    while start < len(text) and not text[start].isalnum():
        start += 1
    return text[start:]


def _deduplicate_cross_chunk_text(
    segments: list[dict[str, Any]],
    language: str,
) -> list[dict[str, Any]]:
    min_tokens, min_score = _overlap_settings(language)
    deduplicated: list[dict[str, Any]] = []

    for segment in segments:
        if not deduplicated:
            deduplicated.append(segment)
            continue

        previous = deduplicated[-1]
        same_chunk = (
            int(previous["_macro_index"]) == int(segment["_macro_index"])
            and int(previous["_chunk_index"]) == int(segment["_chunk_index"])
        )
        if same_chunk:
            deduplicated.append(segment)
            continue

        previous_tokens = _tokenize_text(str(previous.get("text") or ""), language)
        current_text = str(segment.get("text") or "")
        current_tokens = _tokenize_text(current_text, language)
        match = _best_prefix_overlap(
            previous_tokens,
            current_tokens,
            min_tokens,
            min_score,
            require_edge_matches=not _is_chinese_language(language),
        )
        if match is None:
            deduplicated.append(segment)
            continue

        cutoff = current_tokens[match.b_token_count - 1].end
        segment = {
            **segment,
            "text": _trim_leading_separators(current_text, cutoff),
        }
        if segment["text"]:
            deduplicated.append(segment)

    return deduplicated


def merge_chunk_results(
    plan: ParallelAsrPlan,
    chunk_results: dict[str, dict[str, Any]] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result_map = (
        {chunk_key(result): result for result in chunk_results}
        if isinstance(chunk_results, list)
        else chunk_results
    )
    macros = _macro_by_index(plan)
    chunks = _chunk_by_key(plan)
    merged: list[dict[str, Any]] = []
    previous_start = 0.0

    for key in sorted(chunks, key=lambda value: (chunks[value].macro_index, chunks[value].chunk_index)):
        if key not in result_map:
            raise RuntimeError(f"Missing ASR chunk result: {key}")
        chunk = chunks[key]
        macro = macros[chunk.macro_index]
        result = result_map[key]
        trusted_start = macro.start + chunk.start
        trusted_end = trusted_start + chunk.duration
        offset = macro.start + chunk.source_start
        for segment in result.get("segments", []):
            global_start = round(float(segment["start"]) + offset, 3)
            global_end = round(float(segment["end"]) + offset, 3)
            midpoint = (global_start + global_end) / 2
            if midpoint < trusted_start or midpoint > trusted_end:
                continue
            merged.append(
                {
                    **segment,
                    "id": 0,
                    "start": global_start,
                    "end": global_end,
                    "_macro_index": chunk.macro_index,
                    "_chunk_index": chunk.chunk_index,
                }
            )

    merged.sort(
        key=lambda segment: (
            int(segment["_macro_index"]),
            int(segment["_chunk_index"]),
            float(segment["start"]),
            float(segment["end"]),
        )
    )
    merged = _deduplicate_cross_chunk_text(merged, plan.language)
    for index, segment in enumerate(merged):
        start = float(segment["start"])
        if index > 0 and start < previous_start:
            raise RuntimeError("Merged ASR timestamps are not monotonic.")
        if float(segment["end"]) < start:
            raise RuntimeError("Merged ASR segment end is earlier than start.")
        previous_start = start
        segment["id"] = index
        del segment["_macro_index"]
        del segment["_chunk_index"]
    return merged
