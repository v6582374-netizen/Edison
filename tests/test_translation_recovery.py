from pathlib import Path

import pytest

from coworker.providers.base import AssistantTurn
from youtube_strataread.workbench.translation import (
    DEFAULTS,
    CompositionExhausted,
    CompositionRetryable,
    TranslationPipeline,
)


def test_whole_composition_attempts_survive_restart_without_amplification(tmp_path: Path):
    checkpoint = tmp_path / "translation-attempt.json"
    calls: list[int] = []

    def complete(model, messages, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("provider temporarily unavailable")
        return AssistantTurn(text="# Complete\n\nA shorter reading document.", finish_reason="stop")

    translation = "A retained translation with its exact trailing newline.\n"
    for _ in range(2):
        pipeline = TranslationPipeline(complete, "test-model", DEFAULTS, checkpoint)
        with pytest.raises(CompositionRetryable):
            pipeline.compose(translation)

    pipeline = TranslationPipeline(complete, "test-model", DEFAULTS, checkpoint)
    result = pipeline.compose(translation)
    assert result.markdown.startswith("# Complete")
    assert len(calls) == 3

    replay = TranslationPipeline(complete, "test-model", DEFAULTS, checkpoint)
    assert replay.compose(translation).markdown == result.markdown
    assert len(calls) == 3


def test_composition_stops_after_three_requests_and_manual_retry_starts_new_bound(tmp_path: Path):
    checkpoint = tmp_path / "translation-attempt.json"
    calls = 0

    def complete(model, messages, **kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider temporarily unavailable")

    translation = "A retained translation."
    for _ in range(3):
        with pytest.raises(CompositionRetryable):
            TranslationPipeline(complete, "test-model", DEFAULTS, checkpoint).compose(translation)
    with pytest.raises(CompositionExhausted):
        TranslationPipeline(complete, "test-model", DEFAULTS, checkpoint).compose(translation)
    assert calls == 3

    def recover(model, messages, **kwargs):
        nonlocal calls
        calls += 1
        return AssistantTurn(text="# Recovered", finish_reason=None)

    result = TranslationPipeline(recover, "test-model", DEFAULTS, checkpoint).compose(
        translation, force_retry=True
    )
    assert result.markdown == "# Recovered"
    assert calls == 4
