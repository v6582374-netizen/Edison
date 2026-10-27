"""Conservative, immutable source representation for isolated comparisons.

This module is selected explicitly by the experiment entry. Production caption
normalization remains unchanged. Offsets refer to each original cue's content;
normalization only collapses whitespace and does not infer speaker identities.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace

import srt

from youtube_strataread.downloader.request_policy import YouTubeError

UNAVAILABLE_REASON = "暂无可用字幕，未处理"


@dataclass(frozen=True)
class SourceUnit:
    id: str
    ordinal: int
    caption_index: int | None
    start_ms: int
    end_ms: int
    source_char_range: tuple[int, int]
    raw_text: str
    text: str
    overlap_of: str | None = None


@dataclass(frozen=True)
class NormalizedSource:
    raw_sha256: str
    units: tuple[SourceUnit, ...]
    policy: str = "conservative-v1"

    @property
    def text(self) -> str:
        return "\n".join(unit.text for unit in self.units if unit.text)

    def record(self) -> dict:
        value = {
            "schema_version": 1,
            "policy": self.policy,
            "raw_sha256": self.raw_sha256,
            "text": self.text,
            "units": [asdict(unit) for unit in self.units],
        }
        value["sha256"] = hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return value


def normalize_captions(
    raw: str,
    *,
    source_type: str = "speech",
    verified_rolling_pairs: tuple[tuple[int, int], ...] = (),
) -> NormalizedSource:
    """Retain cue order, timing and meaningful text; never globally deduplicate.

    A verified rolling pair is an explicit source annotation using 1-based cue
    ordinals. Even annotated pairs must overlap in time and share an exact
    suffix/prefix. Invalid annotations fail rather than erase uncertain speech.
    Without annotations every repetition is retained.
    """
    if source_type not in {"speech", "live_chat", "absent"}:
        raise ValueError("Unknown source type")
    if source_type != "speech":
        raise YouTubeError(UNAVAILABLE_REASON)
    parsed = list(srt.parse(raw.lstrip("\ufeff")))
    units = tuple(
        SourceUnit(
            id=f"cue-{ordinal:06d}", ordinal=ordinal, caption_index=cue.index,
            start_ms=round(cue.start.total_seconds() * 1000),
            end_ms=round(cue.end.total_seconds() * 1000),
            source_char_range=(0, len(cue.content)), raw_text=cue.content,
            text=re.sub(r"\s+", " ", cue.content).strip(),
        )
        for ordinal, cue in enumerate(parsed, 1)
    )
    if not any(unit.text for unit in units):
        raise YouTubeError(UNAVAILABLE_REASON)
    normalized = list(units)
    seen = set()
    for first, second in verified_rolling_pairs:
        if (first, second) in seen or first < 1 or second != first + 1 or second > len(units):
            raise ValueError("Rolling annotation must identify one adjacent cue pair")
        seen.add((first, second))
        previous, current = units[first - 1], units[second - 1]
        if not previous.start_ms <= current.start_ms < previous.end_ms <= current.end_ms:
            raise ValueError("Verified rolling captions must overlap in time")
        overlap = 0
        for size in range(min(len(previous.raw_text), len(current.raw_text)), 0, -1):
            prefix = current.raw_text[:size]
            if not previous.raw_text.endswith(prefix) or len(prefix.split()) < 2:
                continue
            if size < len(current.raw_text) and not current.raw_text[size].isspace():
                continue
            if size < len(previous.raw_text) and not previous.raw_text[-size - 1].isspace():
                continue
            overlap = size
            break
        if not overlap:
            raise ValueError("Rolling annotation has no exact complete-word suffix/prefix overlap")
        while overlap < len(current.raw_text) and current.raw_text[overlap].isspace():
            overlap += 1
        normalized[second - 1] = replace(
            current, source_char_range=(overlap, len(current.raw_text)),
            text=re.sub(r"\s+", " ", current.raw_text[overlap:]).strip(),
            overlap_of=previous.id,
        )
    return NormalizedSource(hashlib.sha256(raw.encode("utf-8")).hexdigest(), tuple(normalized))
