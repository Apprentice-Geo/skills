from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

STRONG_PUNCTUATION = set("。.!！？?")
WEAK_PUNCTUATION = set("，,；;")
MIN_SEGMENT_SECONDS = 3.0


@dataclass
class AlignmentItem:
    text: str
    start: float
    end: float


@dataclass
class SegmentDraft:
    text: str
    start: float
    end: float
    strong_end: bool

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def consumes_timestamp(char: str) -> bool:
    if char.isspace():
        return False
    return not unicodedata.category(char).startswith("P")


def _to_float(value: Any) -> float:
    return round(float(value), 3)


def consume_alignment_item(
    alignment_items: list[AlignmentItem], item_index: int
) -> tuple[AlignmentItem | None, int]:
    if item_index >= len(alignment_items):
        return None, item_index
    return alignment_items[item_index], item_index + 1


def normalize_alignment_items(items: list[Any]) -> list[AlignmentItem]:
    normalized: list[AlignmentItem] = []
    for item in items:
        text = str(getattr(item, "text", "") or "")
        start = getattr(item, "start_time", None)
        end = getattr(item, "end_time", None)
        if start is None or end is None:
            continue
        normalized.append(
            AlignmentItem(text=text, start=_to_float(start), end=_to_float(end))
        )
    return normalized


def _merge_segments(segments: list[SegmentDraft]) -> list[SegmentDraft]:
    merged = list(segments)
    while True:
        changed = False
        index = 0
        while index < len(merged):
            current = merged[index]
            if current.duration >= MIN_SEGMENT_SECONDS:
                index += 1
                continue
            if index + 1 < len(merged) and not current.strong_end:
                nxt = merged[index + 1]
                merged[index] = SegmentDraft(
                    text=f"{current.text}{nxt.text}",
                    start=current.start,
                    end=nxt.end,
                    strong_end=nxt.strong_end,
                )
                del merged[index + 1]
                changed = True
                continue
            if index > 0 and not merged[index - 1].strong_end:
                prev = merged[index - 1]
                merged[index - 1] = SegmentDraft(
                    text=f"{prev.text}{current.text}",
                    start=prev.start,
                    end=current.end,
                    strong_end=current.strong_end,
                )
                del merged[index]
                changed = True
                index -= 1
                continue
            index += 1
        if not changed:
            return merged


def build_sentence_segments(
    text: str,
    alignment_items: list[AlignmentItem],
    duration: float | None = None,
) -> list[dict[str, Any]]:
    segments: list[SegmentDraft] = []
    item_index = 0
    current_chars: list[str] = []
    current_start: float | None = None
    current_end: float | None = None

    def flush_segment(strong_end: bool) -> None:
        nonlocal current_chars, current_start, current_end
        chunk_text = "".join(current_chars).strip()
        if chunk_text and current_start is not None and current_end is not None:
            segments.append(
                SegmentDraft(
                    text=chunk_text,
                    start=_to_float(current_start),
                    end=_to_float(current_end),
                    strong_end=strong_end,
                )
            )
        current_chars = []
        current_start = None
        current_end = None

    def append_remaining_segment(remaining_text: str) -> None:
        nonlocal current_start, current_end
        tail_text = remaining_text.strip()
        if not tail_text:
            return
        start = current_end
        if start is None and alignment_items:
            start = alignment_items[-1].end
        if start is None:
            start = 0.0
        end = duration if duration is not None else start
        if end < start:
            end = start
        segments.append(
            SegmentDraft(
                text=tail_text,
                start=_to_float(start),
                end=_to_float(end),
                strong_end=tail_text[-1] in STRONG_PUNCTUATION,
            )
        )

    for index, char in enumerate(text):
        if consumes_timestamp(char):
            item, item_index = consume_alignment_item(alignment_items, item_index)
            if item is None:
                flush_segment(strong_end=False)
                append_remaining_segment(text[index:])
                break
            if current_start is None:
                current_start = item.start
            current_end = item.end
        current_chars.append(char)
        if char in STRONG_PUNCTUATION:
            flush_segment(strong_end=True)
            continue
        if (
            char in WEAK_PUNCTUATION
            and current_start is not None
            and current_end is not None
            and current_end - current_start >= MIN_SEGMENT_SECONDS
        ):
            flush_segment(strong_end=False)
    else:
        flush_segment(strong_end=False)

    normalized = _merge_segments(segments)
    return [
        {
            "id": index,
            "start": _to_float(segment.start),
            "end": _to_float(segment.end),
            "text": segment.text,
        }
        for index, segment in enumerate(normalized)
    ]
