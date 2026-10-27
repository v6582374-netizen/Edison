"""Direct translation through the confirmed preparation and persistence boundary."""

import json
from pathlib import Path

from coworker.providers.base import AssistantTurn, TokenUsage
from youtube_strataread.workbench.experiment import CaptionSnapshot, ModelIdentity, run_baseline
from youtube_strataread.workbench.experiment_translation import CompletionCapacity, OutputSegmentation
from youtube_strataread.workbench.workspace import LocalWorkspace

IDENTITY = ModelIdentity("mock-model", "synthetic-v1", "0" * 64, simulated=True)
CAPACITY = CompletionCapacity(input_characters=100000, output_tokens=8192, simulated=True)


def source(tmp_path, text=None):
    path = tmp_path / "source.srt"
    path.write_text(text or (
        "1\n00:00:00,000 --> 00:00:02,000\nAda: We need 25 units (not 30).\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nBen: We must not reorder the steps.\n"
    ))
    return CaptionSnapshot(path, "sample", "Private", "en")


class DirectModel:
    def __init__(self, units):
        self.units = units
        self.calls = []

    def __call__(self, model, messages, **options):
        self.calls.append((model, messages, options))
        return AssistantTurn(
            text=json.dumps({"units": self.units}, ensure_ascii=False),
            finish_reason="stop", usage=TokenUsage(input=200, output=100),
        )


def test_direct_variant_retains_complete_translation_and_formatted_reading_manuscript(tmp_path):
    model = DirectModel([
        {"source_ids": ["cue-000001", "cue-000002"],
         "text": "Ada：需要二十五个单位（不是三十个）。Ben：不得改变步骤顺序。"},
    ])
    result = run_baseline(tmp_path / "run", source(tmp_path), IDENTITY, model,
                          variant="B-direct", capacity=CAPACITY)
    record = json.loads(result.read_text())
    assert record["state"] == "ready"
    assert record["variant"] == "B-direct"
    assert (result.parent / "translation.md").read_text() == "Ada：需要二十五个单位（不是三十个）。Ben：不得改变步骤顺序。\n"
    assert (result.parent / "manuscript.md").read_text() == "## 第 1 节\n\nAda：需要二十五个单位（不是三十个）。Ben：不得改变步骤顺序。\n"
    assert record["attempts"] == 1
    ws = LocalWorkspace.open(tmp_path / "run" / "workspace")
    assert ws.document("sample")["version"] == 1
    assert ws.retained_transcript("sample")["content"].startswith("1\n00:00:00")
    mapping = json.loads((result.parent / record["artifacts"]["candidate_provenance"]).read_text())
    assert mapping["coverage"]["source_ids"] == ["cue-000001", "cue-000002"]
    assert mapping["coverage"]["semantic_fidelity_verified"] is False
    assert record["segmentation"]["strategy"] == "whole"
    assert model.calls[0][2]["max_tokens"] == 8192
