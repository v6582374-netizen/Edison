"""Direct faithful translation for explicitly selected isolated experiments.

Source coverage is a structural publication contract, never a semantic guarantee.
The reading manuscript is formatted deterministically from the retained translation.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from youtube_strataread.workbench.experiment_source import SourceUnit
from youtube_strataread.workbench.translation import TranslationError, TranslationPipeline, TranslationResult

DIRECT_PROMPT = (
    "将指定来源单元忠实、完整地译为简体中文。保留观点、例子、数字、单位、否定、"
    "限定条件、已有说话人和论证顺序；不得摘要或增写。完整来源仅作上下文。"
    "只输出 JSON 对象，units 数组中每项为 source_ids（按来源顺序的 ID 数组）和 text（完整译文）。"
    "每个指定 ID 必须恰好出现一次；不要输出非指定来源。"
)


@dataclass(frozen=True)
class CompletionCapacity:
    input_characters: int
    output_tokens: int
    simulated: bool = False
    verified: bool = False
    evidence_reference: str = ""

    def validate(self, *, simulated_model: bool) -> None:
        if self.input_characters < 1 or self.output_tokens < 1:
            raise ValueError("Completion capacity must be positive")
        if not simulated_model and (self.simulated or not self.verified or not self.evidence_reference):
            raise ValueError("Real candidate execution requires verified configured-proxy capacity evidence")


@dataclass(frozen=True)
class OutputSegmentation:
    strategy: str = "whole"
    chapter_end_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputChapter:
    id: str
    units: tuple[SourceUnit, ...]

    @property
    def source_ids(self) -> list[str]:
        return [unit.id for unit in self.units]


def output_chapters(units: tuple[SourceUnit, ...], segmentation: OutputSegmentation) -> tuple[OutputChapter, ...]:
    active = tuple(unit for unit in units if unit.text)
    if not active:
        raise TranslationError("没有可翻译的来源单元。")
    if segmentation.strategy == "whole":
        if segmentation.chapter_end_ids:
            raise ValueError("Whole output cannot specify chapter ends")
        return (OutputChapter("chapter-000001", active),)
    if segmentation.strategy != "semantic-chapters":
        raise ValueError("Unknown output segmentation strategy")
    ends = segmentation.chapter_end_ids
    if not ends or ends[-1] != active[-1].id or len(set(ends)) != len(ends):
        raise ValueError("Semantic chapter ends must cover the source through its final unit")
    positions = {unit.id: i for i, unit in enumerate(active)}
    if any(end not in positions for end in ends) or [positions[end] for end in ends] != sorted(positions[end] for end in ends):
        raise ValueError("Semantic chapter ends must follow source order")
    chapters = []
    start = 0
    for end in ends:
        stop = positions[end] + 1
        if stop < len(active) and not re.search(r'[.!?。！？][\s\"\'）)\]]*$', active[stop - 1].text):
            raise ValueError("Semantic output boundaries must end a complete sentence; source cues may split numbers or sentences")
        chapters.append(OutputChapter(f"chapter-{len(chapters) + 1:06d}", active[start:stop]))
        start = stop
    return tuple(chapters)


class DirectTranslationPipeline(TranslationPipeline):
    """Reuse the host request/checkpoint adapter with an independent direct mechanism."""

    def __init__(self, *args, source_units: tuple[SourceUnit, ...], capacity: CompletionCapacity,
                 segmentation: OutputSegmentation, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_units = source_units
        self.capacity = capacity
        self.segmentation = segmentation
        self.chapters = output_chapters(source_units, segmentation)
        self.stage_elapsed_seconds: dict[str, float] = {}
        self.identity = {
            "variant": "B-direct", "model": self.model, "settings": self.config,
            "source_units": [asdict(unit) for unit in source_units],
            "capacity": asdict(capacity), "segmentation": asdict(segmentation), "prompt": DIRECT_PROMPT,
        }
        self.identity_sha256 = hashlib.sha256(json.dumps(self.identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _save(self):
        if self.state:
            self.state["candidate_identity_sha256"] = self.identity_sha256
        super()._save()

    def run(self, transcript: str, *, source_lang: str = "auto-detected source language") -> TranslationResult:
        if self.checkpoint and self.checkpoint.exists():
            saved = json.loads(self.checkpoint.read_text(encoding="utf-8"))
            if saved.get("candidate_identity_sha256") != self.identity_sha256:
                raise TranslationError("方案、来源、提示、模型或能力设置已变化，不能复用旧输出；请显式开始新尝试。")
        return super().run(transcript, source_lang=source_lang)

    def _translate_chapter(self, chapter: OutputChapter, full_source: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = [
            {"role": "system", "content": DIRECT_PROMPT},
            {"role": "user", "content": json.dumps({"full_source_context": full_source, "output_source_ids": chapter.source_ids,
                                                     "source_language": self.state["source_lang"]}, ensure_ascii=False)},
        ]
        if sum(len(message["content"]) for message in messages) > self.capacity.input_characters:
            raise TranslationError("完整上下文超过已验证的输入能力；未按任意字符边界截断来源。")

        def validate(text):
            self._parse_units(text, chapter.source_ids)

        response = self._request(f"direct:{chapter.id}", messages, validator=validate, temperature=0.3)
        return self._parse_units(response, chapter.source_ids)

    @staticmethod
    def _parse_units(text: str, expected: list[str]) -> list[dict[str, Any]]:
        try:
            value = json.loads(text)
        except (ValueError, TypeError) as error:
            raise TranslationError("译文缺少可验证的来源单元结构，未发布稿件。") from error
        if not isinstance(value, dict) or set(value) != {"units"} or not isinstance(value["units"], list):
            raise TranslationError("译文来源单元结构无效，未发布稿件。")
        ids = []
        for unit in value["units"]:
            if (not isinstance(unit, dict) or set(unit) != {"source_ids", "text"}
                    or not isinstance(unit["source_ids"], list) or not unit["source_ids"]
                    or not isinstance(unit["text"], str) or not unit["text"].strip()):
                raise TranslationError("译文来源单元缺失或内容为空，未发布稿件。")
            ids.extend(unit["source_ids"])
        if ids != expected:
            raise TranslationError("译文来源单元缺失、重复或顺序变化，未发布稿件。")
        return value["units"]

    def _run(self, transcript):
        started = time.monotonic()
        full_source = [{"id": unit.id, "text": unit.text} for unit in self.source_units if unit.text]
        outputs = []
        for index, chapter in enumerate(self.chapters, 1):
            if self.on_progress:
                self.on_progress("initial", f"直接翻译第 {index} / {len(self.chapters)} 章")
            outputs.append(self._translate_chapter(chapter, full_source))
        self.stage_elapsed_seconds["translation"] = time.monotonic() - started
        paragraphs = [unit["text"].strip() for chapter in outputs for unit in chapter]
        translation = "\n\n".join(paragraphs)
        formatted = []
        for index, chapter in enumerate(outputs, 1):
            formatted.append(f"## 第 {index} 节\n\n" + "\n\n".join(unit["text"].strip() for unit in chapter))
        coverage = [source_id for chapter in outputs for unit in chapter for source_id in unit["source_ids"]]
        return TranslationResult(
            markdown="\n\n".join(formatted), translation=translation,
            provenance={"variant": "B-direct", "model": self.model, "prompt": DIRECT_PROMPT,
                        "settings": self.config, "capacity": asdict(self.capacity), "segmentation": asdict(self.segmentation),
                        "candidate_identity_sha256": self.identity_sha256,
                        "coverage": {"source_ids": coverage, "exactly_once_in_source_order": True,
                                     "semantic_fidelity_verified": False},
                        "output_units": outputs, "calls": self.state["calls"],
                        "stage_elapsed_seconds": self.stage_elapsed_seconds,
                        "limitations": ["Source ID coverage does not prove faithful semantics. Deterministic formatting adds headings and paragraph breaks only."]},
        )
