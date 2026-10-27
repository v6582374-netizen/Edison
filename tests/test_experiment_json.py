"""Strict response-envelope compatibility through isolated preparation."""

import json

import pytest
from test_experiment_review import CAPACITY, IDENTITY, row, source

from coworker.providers.base import AssistantTurn
from youtube_strataread.workbench.experiment import run_baseline
from youtube_strataread.workbench.workspace import LocalWorkspace


@pytest.mark.parametrize("variant", ["B-direct", "C-conditional"])
@pytest.mark.parametrize(
    "defect",
    [
        "prose-before",
        "prose-after",
        "two-blocks",
        "other-code",
        "unlabelled",
        "unclosed",
        "inner-garbage",
        "malformed-json",
        "extra-schema",
        "partial-units",
        "truncated",
        "missing-finish",
    ],
)
def test_json_envelope_never_salvages_invalid_or_incomplete_output(tmp_path, variant, defect):
    snapshot = source(tmp_path)
    correct = [row(["cue-000001"], "年龄是25岁。"), row(["cue-000002"], "顺序保持不变。")]
    payload = (
        {"units": correct}
        if variant == "B-direct"
        else {"decision": "replace", "units": correct[:1]}
    )
    if defect == "extra-schema":
        payload["extra"] = "outside contract"
    if defect == "partial-units":
        payload["units"] = correct[:1] if variant == "B-direct" else []
    inner = json.dumps(payload)
    if defect == "inner-garbage":
        inner += " garbage"
    if defect == "malformed-json":
        inner = inner[:-1]
    response = "```json\n" + inner + "\n```"
    if defect == "prose-before":
        response = "Here is the response:\n" + response
    if defect == "prose-after":
        response += "\nDone."
    if defect == "two-blocks":
        response += "\n" + response
    if defect == "other-code":
        response = response.replace("```json", "```python", 1)
    if defect == "unlabelled":
        response = response.replace("```json", "```", 1)
    if defect == "unclosed":
        response = response[:-3]
    finish = "length" if defect == "truncated" else None if defect == "missing-finish" else "stop"
    calls = []

    def complete(_model, messages, **_options):
        calls.append(1)
        if variant == "C-conditional" and len(calls) == 1:
            return AssistantTurn(
                text=json.dumps({"units": [row(["cue-000001"], "年龄是26岁。"), correct[1]]}),
                finish_reason="stop",
            )
        return AssistantTurn(text=response, finish_reason=finish)

    result = run_baseline(
        tmp_path / "run", snapshot, IDENTITY, complete, variant=variant, capacity=CAPACITY
    )
    record = json.loads(result.read_text())
    assert record["state"] == "failed"
    assert record["attempts"] <= 3
    assert len(calls) <= 3
    assert "translation" not in record["artifacts"]
    assert "manuscript" not in record["artifacts"]
    workspace = LocalWorkspace.open(tmp_path / "run/workspace")
    assert workspace.generation_records("sample") == []
    assert workspace.retained_transcript("sample")["content"] == snapshot.path.read_text()
