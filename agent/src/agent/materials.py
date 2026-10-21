"""Extracted evidence retains its source location before chunking or indexing."""

from pydantic import Field

from .schemas import StrictModel


class Segment(StrictModel):
    text: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    timestamp_ms: int | None = Field(default=None, ge=0)


def timestamp(milliseconds: int) -> str:
    seconds = milliseconds // 1000
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def chunk_segments(segments, size=800, overlap=100):
    if not 0 <= overlap < size:
        raise ValueError('分块重叠长度必须小于块大小')
    chunks = []
    for segment in segments:
        text = segment.text.strip()
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                boundary = max(text.rfind(mark, start + size // 2, end) for mark in ('\n', '。', '；', '. '))
                if boundary >= 0:
                    end = boundary + 1
            content = text[start:end].strip()
            if content:
                chunks.append(Segment(text=content, ref=segment.ref, timestamp_ms=segment.timestamp_ms))
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks
