"""按原文段落切分长文，并保留可校验的源文本位置。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


DEFAULT_MIN_SEGMENT_CHARS = 4000
DEFAULT_MAX_SEGMENT_CHARS = 6000

PARAGRAPH_BREAK_PATTERN = re.compile(r"\n\s*\n")
SENTENCE_END_PATTERN = re.compile(r"[.!?。！？]+(?:[\"'”’）)\]]*)")


@dataclass(frozen=True)
class TextSegment:
    """一段连续源文本及其在全文中的位置。"""

    index: int
    total: int
    start: int
    end: int
    text: str

    @property
    def ordinal(self) -> int:
        """返回从 1 开始的段序号。"""

        return self.index + 1

    @property
    def char_count(self) -> int:
        """返回包括段内空白的字符数。"""

        return len(self.text)

    @property
    def non_whitespace_char_count(self) -> int:
        """返回非空白字符数，用于拒绝空段。"""

        return len("".join(self.text.split()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ordinal": self.ordinal,
            "total": self.total,
            "start": self.start,
            "end": self.end,
            "charCount": self.char_count,
            "nonWhitespaceCharCount": self.non_whitespace_char_count,
        }


@dataclass(frozen=True)
class SegmentationResult:
    """一次完整分段结果。"""

    source_text: str
    segments: tuple[TextSegment, ...]
    min_segment_chars: int
    max_segment_chars: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceCharCount": len(self.source_text),
            "segmentCount": len(self.segments),
            "minSegmentChars": self.min_segment_chars,
            "maxSegmentChars": self.max_segment_chars,
            "segments": [segment.as_dict() for segment in self.segments],
        }


def segment_text(
    text: str,
    *,
    min_chars: int = DEFAULT_MIN_SEGMENT_CHARS,
    max_chars: int = DEFAULT_MAX_SEGMENT_CHARS,
) -> SegmentationResult:
    """按段落和完整句尾切分长文。

    分块按 `max_chars` 硬限制；`min_chars` 只用于表达目标区间。段落较小时
    会与后续段落合并，接近上限后再开始下一段。段位置连续覆盖全文，因此
    调用方可以逐字重建原文。
    """

    if not isinstance(text, str):
        raise ValueError("待分段文本必须是字符串")
    if not text.strip():
        raise ValueError("待分段文本不能为空")
    if min_chars <= 0 or max_chars <= 0:
        raise ValueError("分段字符上限和下限必须是正整数")
    if min_chars > max_chars:
        raise ValueError("min_chars 不能大于 max_chars")

    atoms = _sentence_atoms(text, max_chars=max_chars)
    spans: list[tuple[int, int]] = []
    current_start = 0
    current_end = -1

    for atom_start, atom_end in atoms:
        if current_end < 0:
            current_end = atom_end
            continue
        if atom_end - current_start > max_chars:
            spans.append((current_start, current_end))
            # 段间空白归入下一段，保证位置连续且全文可重建。
            current_start = current_end
            current_end = atom_end
        else:
            current_end = atom_end

    if current_end < 0:
        raise ValueError("待分段文本没有有效内容")
    spans.append((current_start, len(text)))

    segments: list[TextSegment] = []
    total = len(spans)
    for index, (start, end) in enumerate(spans):
        segment_value = text[start:end]
        if not segment_value.strip():
            raise ValueError(f"分段 {index} 只包含空白")
        if len(segment_value) > max_chars:
            raise ValueError(
                f"分段 {index} 超出上限：{len(segment_value)} > {max_chars}"
            )
        segments.append(
            TextSegment(
                index=index,
                total=total,
                start=start,
                end=end,
                text=segment_value,
            )
        )

    result = SegmentationResult(
        source_text=text,
        segments=tuple(segments),
        min_segment_chars=min_chars,
        max_segment_chars=max_chars,
    )
    validate_segmentation(result)
    return result


def validate_segmentation(result: SegmentationResult) -> None:
    """验证顺序、连续覆盖、空段和字符上限。"""

    if not result.segments:
        raise ValueError("分段结果不能为空")

    expected_start = 0
    parts: list[str] = []
    total = len(result.segments)
    for expected_index, segment in enumerate(result.segments):
        if segment.index != expected_index or segment.total != total:
            raise ValueError("分段序号或总数不一致")
        if segment.start != expected_start:
            raise ValueError(
                f"分段位置不连续：期望 {expected_start}，实际 {segment.start}"
            )
        if segment.end <= segment.start:
            raise ValueError(f"分段 {segment.index} 的结束位置无效")
        if not segment.text.strip():
            raise ValueError(f"分段 {segment.index} 是空文本")
        if segment.text != result.source_text[segment.start : segment.end]:
            raise ValueError(f"分段 {segment.index} 与源文本位置不一致")
        if segment.char_count > result.max_segment_chars:
            raise ValueError(f"分段 {segment.index} 超过字符上限")
        expected_start = segment.end
        parts.append(segment.text)

    if expected_start != len(result.source_text):
        raise ValueError("分段结果没有覆盖到源文本末尾")
    if "".join(parts) != result.source_text:
        raise ValueError("拼接后的分段文本与原文不一致")


def _sentence_atoms(text: str, *, max_chars: int) -> list[tuple[int, int]]:
    """生成不超过上限的内容原子，段落间空白由分块边界自然保留。"""

    atoms: list[tuple[int, int]] = []
    for paragraph_start, paragraph_end in _paragraph_spans(text):
        paragraph_length = paragraph_end - paragraph_start
        if paragraph_length <= max_chars:
            atoms.append((paragraph_start, paragraph_end))
            continue

        sentence_start = paragraph_start
        for match in SENTENCE_END_PATTERN.finditer(
            text,
            paragraph_start,
            paragraph_end,
        ):
            sentence_end = match.end()
            atoms.extend(
                _hard_split_span(
                    sentence_start,
                    sentence_end,
                    max_chars=max_chars,
                )
            )
            sentence_start = sentence_end
        if sentence_start < paragraph_end:
            atoms.extend(
                _hard_split_span(
                    sentence_start,
                    paragraph_end,
                    max_chars=max_chars,
                )
            )

    return [atom for atom in atoms if text[atom[0] : atom[1]].strip()]


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """返回非空段落的内容边界，不包含段落间空白。"""

    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in PARAGRAPH_BREAK_PATTERN.finditer(text):
        _append_trimmed_span(spans, text, cursor, match.start())
        cursor = match.end()
    _append_trimmed_span(spans, text, cursor, len(text))
    return spans


def _append_trimmed_span(
    spans: list[tuple[int, int]],
    text: str,
    start: int,
    end: int,
) -> None:
    """去除段落首尾空白后加入边界。"""

    raw = text[start:end]
    if not raw.strip():
        return
    left = len(raw) - len(raw.lstrip())
    right = len(raw.rstrip())
    spans.append((start + left, start + right))


def _hard_split_span(
    start: int,
    end: int,
    *,
    max_chars: int,
) -> list[tuple[int, int]]:
    """在没有可靠句尾时按上限做保守切分。"""

    if end <= start:
        return []
    return [
        (offset, min(offset + max_chars, end))
        for offset in range(start, end, max_chars)
    ]
