"""Experiment-only three-stage baseline at the shared natural chapter boundaries."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict

from translation_agent import runtime, utils
from youtube_strataread.workbench.experiment_translation import (
    DirectTranslationPipeline,
    effective_chapter_plan,
)
from youtube_strataread.workbench.translation import (
    FUNCTION_STAGES,
    UPSTREAM_COMMIT,
    TranslationError,
    TranslationResult,
    check_completeness,
)


class ComparableBaselinePipeline(DirectTranslationPipeline):
    """Reuse experiment guards and retained upstream stages, without B's mechanism."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.identity.update(
            variant="A-comparable-three-stage",
            prompt=self.config["prompts"],
            upstream=UPSTREAM_COMMIT,
        )
        self.identity_sha256 = hashlib.sha256(
            json.dumps(self.identity, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    def _request(self, key, messages, *, validator=None, **options):
        # Review/revision include prior model output, so check the rendered request.
        if sum(len(message["content"]) for message in messages) > self.capacity.input_characters:
            raise TranslationError("完整上下文超过已验证的输入能力；未按任意字符边界截断来源。")
        return super()._request(key, messages, validator=validator, **options)

    def _upstream_completion(self, *args, function, **kwargs):
        stage = FUNCTION_STAGES[function]
        started = time.monotonic()
        try:
            return super()._upstream_completion(*args, function=function, **kwargs)
        finally:
            self.stage_elapsed_seconds[stage] = (
                self.stage_elapsed_seconds.get(stage, 0) + time.monotonic() - started
            )

    def _run(self, transcript):
        chunks = ["\n".join(unit.text for unit in chapter.units) for chapter in self.chapters]
        # Upstream joins chunks without separators; preserve the canonical cue joins.
        chunks = [text + ("\n" if i + 1 < len(chunks) else "") for i, text in enumerate(chunks)]
        if "".join(chunks) != transcript:
            raise TranslationError("章节与完整来源不一致，未发布稿件。")
        started = time.monotonic()
        try:
            with runtime.bind(self._upstream_completion):
                revised = utils.multichunk_translation(
                    self.state["source_lang"], "简体中文", chunks, self.config["country"]
                )
        finally:
            self.stage_elapsed_seconds["translation"] = time.monotonic() - started
        if len(revised) != len(self.chapters):
            raise TranslationError("修订译文章节数量不一致，未发布稿件。")
        for source, output in zip(chunks, revised, strict=True):
            check_completeness(source, output)
        translation = "\n\n".join(text.strip() for text in revised)
        check_completeness(transcript, translation)
        documents = []
        pair = self.config["prompts"]["composition"]
        started = time.monotonic()
        try:
            for i, part in enumerate(revised):
                if self.on_progress:
                    self.on_progress("composition", f"第 {i + 1} / {len(revised)} 章")
                values = {
                    "translation": part,
                    "context": "\n".join(
                        [
                            revised[i - 1][-400:] if i else "",
                            revised[i + 1][:400] if i + 1 < len(revised) else "",
                        ]
                    ),
                }
                messages = [
                    {"role": role, "content": pair[role].format_map(values)}
                    for role in ("system", "user")
                ]
                documents.append(
                    self._request(
                        f"composition:{self.chapters[i].id}",
                        messages,
                        validator=lambda text, source=part: check_completeness(
                            source, text, composition=True
                        ),
                    )
                )
        finally:
            self.stage_elapsed_seconds["composition"] = time.monotonic() - started
        return TranslationResult(
            markdown="\n\n".join(documents),
            translation=translation,
            provenance={
                "variant": "A-comparable-three-stage",
                "upstream": UPSTREAM_COMMIT,
                "model": self.model,
                "settings": self.config,
                "capacity": asdict(self.capacity),
                "segmentation": asdict(self.segmentation),
                "effective_chapter_plan": effective_chapter_plan(self.chapters),
                "candidate_identity_sha256": self.identity_sha256,
                "coverage": {
                    "source_ids": [id for chapter in self.chapters for id in chapter.source_ids],
                    "scheduled_source_ranges_exactly_once": True,
                    "output_mapping": "host-assigned-chapter-range",
                    "model_returned_source_ids_verified": False,
                    "semantic_fidelity_verified": False,
                },
                "output_units": [
                    [{"source_ids": chapter.source_ids, "text": text.strip()}]
                    for chapter, text in zip(self.chapters, revised, strict=True)
                ],
                "calls": self.state["calls"],
                "stage_elapsed_seconds": self.stage_elapsed_seconds,
                "limitations": [
                    "Chapter ranges are assigned by the host, not verified model-returned source IDs; semantic fidelity is unverified.",
                    "Upstream plain-text prompts repeat the selected chapter; JSON/source-ID overhead differs in B/C.",
                    "Existing composition validation, including literal numeric checks, is retained as baseline behavior.",
                ],
            },
        )
