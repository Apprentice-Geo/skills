from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from scripts.asr.parallel.plan import AsrChunkPlan, ParallelAsrPlan
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


def _is_chinese_language(language: str) -> bool:
    return language.lower().startswith("zh")


def _tokenize_chinese_text(text: str) -> list[_TextToken]:
    return [
        _TextToken(ch.lower(), index, index + 1)
        for index, ch in enumerate(text)
        # 判断字符串是否只由字母或数字组成
        # 支持 Unicode，因此汉字等文字字符也会被视为字母字符
        if ch.isalnum()
    ]


def _tokenize_english_text(text: str) -> list[_TextToken]:
    return [
        _TextToken(match.group(0).lower(), match.start(), match.end())
        for match in re.finditer(r"[A-Za-z0-9]+", text)
    ]


def _tokenize_text(text: str, language: str) -> list[_TextToken]:
    # 数据清洗 切分出单词或中文字符
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
            # zip 打包成元组
            for left, right in zip(a_slice, b_slice)
        )
        score = same_count / token_count
        # 取分数最高且最短的匹配
        if score < min_score:
            continue
        if best is None or score > best.score:
            best = _OverlapMatch(token_count, score)
        elif score == best.score and token_count < best.b_token_count:
            best = _OverlapMatch(token_count, score)
    return best


def _trim_leading_separators(text: str, start: int) -> str:
    # 移除后缀开头的非字母数字字符 再返回后缀
    while start < len(text) and not text[start].isalnum():
        start += 1
    return text[start:]


def _deduplicate_cross_chunk_text(
    segments: list[dict[str, Any]],
    plan: ParallelAsrPlan,
) -> list[dict[str, Any]]:
    min_tokens, min_score = _overlap_settings(plan.language)
    segments_by_chunk: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for segment in segments:
        identity = (
            int(segment["_macro_index"]),
            int(segment["_chunk_index"]),
        )
        segments_by_chunk.setdefault(identity, []).append(segment)

    deduplicated: list[dict[str, Any]] = []
    previous_chunk: AsrChunkPlan | None = None
    previous_segments: list[dict[str, Any]] = []

    for chunk in plan.asr_chunks:
        identity = (chunk.macro_index, chunk.chunk_index)
        current_segments = list(segments_by_chunk.get(identity, []))
        if not current_segments:
            previous_chunk = chunk
            previous_segments = []
            continue

        if previous_chunk is not None and previous_segments:
            previous = previous_segments[-1]
            current = current_segments[0]
            source_overlap_start = max(
                previous_chunk.source_start,
                chunk.source_start,
            )
            source_overlap_end = min(
                previous_chunk.source_start + previous_chunk.source_duration,
                chunk.source_start + chunk.source_duration,
            )
            # start 是转写结果的时间戳开始
            segment_overlap_start = max(
                float(previous["start"]),
                float(current["start"]),
            )
            # end 是转写结果的时间戳结束
            segment_overlap_end = min(
                float(previous["end"]),
                float(current["end"]),
            )
            # 检查理论上有没有重叠部分
            has_time_evidence = (
                max(source_overlap_start, segment_overlap_start)
                < min(source_overlap_end, segment_overlap_end)
            )
            if has_time_evidence:
                previous_tokens = _tokenize_text(
                    str(previous.get("text") or ""),
                    plan.language,
                )
                current_text = str(current.get("text") or "")
                current_tokens = _tokenize_text(current_text, plan.language)
                # 中文不要求首尾匹配，英文要求首尾匹配
                match = _best_prefix_overlap(
                    previous_tokens,
                    current_tokens,
                    min_tokens,
                    min_score,
                    require_edge_matches=not _is_chinese_language(plan.language),
                )
                if match is not None:
                    cutoff = current_tokens[match.b_token_count - 1].end
                    # 合并为一句
                    previous["text"] = previous.get("text", "") + _trim_leading_separators(
                        current_text,
                        cutoff,
                    )
                    previous["end"] = current["end"]
                    current_segments.pop(0)

        deduplicated.extend(current_segments)
        previous_chunk = chunk
        previous_segments = current_segments

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
    merged: list[dict[str, Any]] = []
    previous_start = 0.0

    for chunk in plan.asr_chunks:
        key = chunk_key(chunk)
        if key not in result_map:
            raise RuntimeError(f"Missing ASR chunk result: {key}")
        result = result_map[key]
        trusted_start = chunk.start
        trusted_end = trusted_start + chunk.duration
        offset = chunk.source_start
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
    merged = _deduplicate_cross_chunk_text(merged, plan)
    for index, segment in enumerate(merged):
        start = float(segment["start"])
        if index > 0 and start < previous_end:
            raise RuntimeError("Merged ASR timestamps are not monotonically increasing.")
        if float(segment["end"]) < start:
            raise RuntimeError("Merged ASR segment end is earlier than start.")
        previous_end = float(segment["end"])
        segment["id"] = index

        # 后面两步删除清理了合并时的信息
        del segment["_macro_index"]
        del segment["_chunk_index"]
    return merged
