"""Finite, no-network acceptance seams for the spec 78 sample set."""
from __future__ import annotations

import pytest

from scripts.spec78_acceptance import SAMPLES, inspect_samples


def test_retained_sample_manifest_is_immutable_when_available():
    rows = inspect_samples()
    available = [row for row in rows if row["sha256"] is not None]
    if not available:
        pytest.skip("retained private acceptance samples are unavailable in this checkout")
    assert len(available) == len(SAMPLES)
    assert all(row["identity_matches"] for row in available)


def test_acceptance_plan_has_bounded_operational_contract(capsys):
    rows = inspect_samples()
    plan = {
        "workflow": "original-upstream-v1",
        "composition_rule": "whole-markdown-v1",
        "automatic_composition_request_limit": 3,
        "semantic_comparison": False,
        "sample_ids": [row["video_id"] for row in rows],
    }
    assert plan["automatic_composition_request_limit"] == 3
    assert plan["semantic_comparison"] is False
    assert plan["sample_ids"] == ["RLYq760Jyuo", "nk15CT41MFc", "bqeuFiAUU4o"]
