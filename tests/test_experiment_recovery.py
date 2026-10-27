"""Recovery observed at the real isolated preparation boundary."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_translation_experiment import identity, snapshot
from translation_fixture import EchoModel

from coworker.providers.base import AssistantTurn
from youtube_strataread.workbench.experiment import run_baseline
from youtube_strataread.workbench.experiment_translation import (
    CompletionCapacity,
    OutputSegmentation,
)
from youtube_strataread.workbench.workspace import LocalWorkspace


def test_resume_reuses_completed_stages_and_completed_attempt_is_idempotent(tmp_path):
    root = tmp_path / "experiment"
    source = snapshot(tmp_path)
    model = EchoModel()
    model.truncate_at = 3
    failed = json.loads(run_baseline(root, source, identity(), model, attempt_id="one").read_text())
    assert failed["state"] == "failed"
    resumed = json.loads(
        run_baseline(
            root, source, identity(), EchoModel(), attempt_id="one", mode="resume"
        ).read_text()
    )
    assert resumed["state"] == "ready"
    assert resumed["attempts"] == 2
    assert resumed["recovery"]["cached_responses_before"] == 2

    def forbidden(*args, **kwargs):
        raise AssertionError("completed attempt must not call model")

    repeated = json.loads(
        run_baseline(
            root, source, identity(), forbidden, attempt_id="one", mode="resume"
        ).read_text()
    )
    assert repeated["state"] == "ready"
    assert repeated["attempts"] == 0
    ws = LocalWorkspace.open(root / "workspace")
    assert len(ws.generation_records("sample")) == 1
    assert Path(ws.document("sample")["path"]).read_text() == "Source with 42 people.\n"


VARIANTS = ["A-current-baseline", "B-direct", "C-conditional"]


def options(variant):
    return {"variant": variant, "capacity": CompletionCapacity(100000, 8192, simulated=True)}


class DirectEcho:
    def __init__(self, failure=None, fail_after=0, finish="stop"):
        self.calls = 0
        self.failure, self.fail_after, self.finish = failure, fail_after, finish

    def __call__(self, model, messages, **kwargs):
        self.calls += 1
        if self.failure is not None and self.calls > self.fail_after:
            raise self.failure
        payload = json.loads(messages[-1]["content"])
        ids = payload["output_source_ids"]
        return AssistantTurn(
            text=json.dumps(
                {"units": [{"source_ids": [i], "text": "Source with 42 people."} for i in ids]}
            ),
            finish_reason=self.finish,
        )


def model_for(variant):
    return EchoModel() if variant == "A-current-baseline" else DirectEcho()


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("boundary", ["after_bundle", "after_commit"])
def test_interrupted_publication_recovers_once_and_preserves_history(tmp_path, variant, boundary):
    class PowerLoss(BaseException):
        pass

    root, source = tmp_path / "experiment", snapshot(tmp_path)
    run_baseline(
        root, source, identity(), model_for(variant), attempt_id="first", **options(variant)
    )
    ws = LocalWorkspace.open(root / "workspace")
    original = ws.document("sample")
    original_bytes = Path(original["path"]).read_bytes()

    def interrupt(stage):
        if stage == boundary:
            raise PowerLoss()

    with pytest.raises(PowerLoss):
        run_baseline(
            root,
            source,
            identity(),
            model_for(variant),
            attempt_id="second",
            publication_fault=interrupt,
            **options(variant),
        )
    assert Path(original["path"]).read_bytes() == original_bytes

    def forbidden(*args, **kwargs):
        raise AssertionError("completed stages must survive publication interruption")

    record = json.loads(
        run_baseline(
            root,
            source,
            identity(),
            forbidden,
            attempt_id="second",
            mode="resume",
            **options(variant),
        ).read_text()
    )
    assert record["state"] == "ready"
    assert record["attempts"] == 0
    assert record["artifacts"]["manuscript_version"] == 2
    assert [r["manuscript_version"] for r in ws.generation_records("sample")] == [1, 2]
    assert source.path.read_bytes() == ws.retained_transcript("sample")["content"].encode()
    assert Path(original["path"]).read_bytes() == original_bytes


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("finish", [None, "length"])
def test_incomplete_finish_never_publishes_and_can_resume(tmp_path, variant, finish):
    root, source = tmp_path / "experiment", snapshot(tmp_path)
    if variant == "A-current-baseline":
        echo = EchoModel()

        def broken(*args, **kwargs):
            turn = echo(*args, **kwargs)
            turn.finish_reason = finish
            return turn
    else:
        broken = DirectEcho(finish=finish)
    record = json.loads(
        run_baseline(
            root, source, identity(), broken, attempt_id="bad", **options(variant)
        ).read_text()
    )
    assert record["state"] == "failed"
    assert record["attempts"] <= 3
    ws = LocalWorkspace.open(root / "workspace")
    assert ws.generation_records("sample") == []
    assert "manuscript" not in record["artifacts"]
    assert list((root / "runs").glob("*/request-*.json"))
    resumed = json.loads(
        run_baseline(
            root,
            source,
            identity(),
            model_for(variant),
            attempt_id="bad",
            mode="resume",
            **options(variant),
        ).read_text()
    )
    assert resumed["state"] == "ready"


@pytest.mark.parametrize(
    "change",
    [
        "model",
        "proxy",
        "configuration",
        "prompts",
        "capacity",
        "variant",
        "review",
        "source_policy",
        "segmentation",
    ],
)
def test_incompatible_resume_rejects_before_mutating_storage(tmp_path, change):
    import copy

    from youtube_strataread.workbench.experiment_review import ReviewPolicy
    from youtube_strataread.workbench.translation import DEFAULTS

    root, source = tmp_path / "experiment", snapshot(tmp_path)
    run_baseline(root, source, identity(), EchoModel(), attempt_id="same")
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    requested_identity = identity()
    kwargs = {}
    if change == "model":
        requested_identity = replace(requested_identity, id="other")
    if change == "proxy":
        requested_identity = replace(requested_identity, proxy_version="other")
    if change == "configuration":
        requested_identity = replace(requested_identity, configuration_sha256="1" * 64)
    if change == "prompts":
        kwargs["config"] = copy.deepcopy(DEFAULTS)
        kwargs["config"]["prompts"]["review"]["system"] += " changed"
    if change == "capacity":
        kwargs["capacity"] = CompletionCapacity(100000, 4096, simulated=True)
    if change == "variant":
        kwargs.update(options("B-direct"))
    if change == "review":
        kwargs["review_policy"] = ReviewPolicy(max_repairs=1)
    if change == "source_policy":
        kwargs["source_policy"] = "historical"
    if change == "segmentation":
        kwargs["segmentation"] = OutputSegmentation("semantic-chapters")
    with pytest.raises(ValueError, match="identity"):
        run_baseline(
            root,
            source,
            requested_identity,
            EchoModel(),
            attempt_id="same",
            mode="resume",
            **kwargs,
        )
    assert {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before


@pytest.mark.parametrize("variant", VARIANTS)
def test_cancelled_attempt_stays_cancelled_on_resume_and_new_submission(tmp_path, variant):
    root, source = tmp_path / "experiment", snapshot(tmp_path)

    def fail(*args, **kwargs):
        raise ValueError("ordinary model failure")

    run_baseline(root, source, identity(), fail, attempt_id="cancelled", **options(variant))
    ws = LocalWorkspace.open(root / "workspace")
    ws.retry_failed("sample")
    assert ws.change_queue(["sample"])["changed"] == ["sample"]

    def forbidden(*args, **kwargs):
        raise AssertionError("cancellation must survive")

    record = json.loads(
        run_baseline(
            root,
            source,
            identity(),
            forbidden,
            attempt_id="cancelled",
            mode="resume",
            **options(variant),
        ).read_text()
    )
    assert record["state"] == "cancelled"
    assert record["attempts"] == 0
    with pytest.raises(ValueError, match="取消"):
        run_baseline(root, source, identity(), forbidden, attempt_id="new", **options(variant))
    assert ws.asset("sample")["preparation_state"] == "cancelled"
    assert ws.generation_records("sample") == []


def test_resuming_earlier_failed_attempt_does_not_reuse_newer_attempt_checkpoint(tmp_path):
    root, source = tmp_path / "experiment", snapshot(tmp_path)
    bad = EchoModel()
    bad.truncate_at = 3
    run_baseline(root, source, identity(), bad, attempt_id="first")
    run_baseline(root, source, identity(), bad, attempt_id="second")
    # The second invocation completed; resuming the first must reuse only its own stages.
    result = json.loads(
        run_baseline(
            root, source, identity(), EchoModel(), attempt_id="first", mode="resume"
        ).read_text()
    )
    assert result["state"] == "ready"
    assert result["attempts"] == 2
    assert result["artifacts"]["manuscript_version"] == 2


@pytest.mark.parametrize("variant", VARIANTS)
def test_connection_failure_is_bounded_and_resume_reuses_prior_work(tmp_path, monkeypatch, variant):
    import time

    monkeypatch.setattr("youtube_strataread.workbench.translation.time.sleep", lambda _: None)
    root, source = tmp_path / "experiment", snapshot(tmp_path)
    kwargs = options(variant)
    if variant != "A-current-baseline":
        source.path.write_text(
            source.path.read_text() + "\n2\n00:00:03,000 --> 00:00:05,000\nSource with 42 people.\n"
        )
        kwargs["segmentation"] = OutputSegmentation(
            "semantic-chapters", ("cue-000001", "cue-000002")
        )
    echo = model_for(variant)
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise ConnectionError("private transport details")
        return echo(*args, **kwargs)

    failed = json.loads(
        run_baseline(
            root, source, identity(), interrupted, attempt_id="retry", **kwargs
        ).read_text()
    )
    assert failed["state"] == "waiting_retry"
    assert failed["attempts"] == 4  # one accepted response plus three bounded failures
    assert "private transport details" not in failed["reason"]
    # An explicit resume respects the existing retry deadline.
    gated = json.loads(
        run_baseline(
            root, source, identity(), interrupted, attempt_id="retry", mode="resume", **kwargs
        ).read_text()
    )
    assert gated["attempts"] == 0
    future = time.time() + 3600
    monkeypatch.setattr(time, "time", lambda: future)
    resumed = json.loads(
        run_baseline(
            root,
            source,
            identity(),
            model_for(variant),
            attempt_id="retry",
            mode="resume",
            **kwargs,
        ).read_text()
    )
    assert resumed["state"] == "ready"
    assert resumed["recovery"]["cached_responses_before"] == 1
    assert resumed["attempts"] == (3 if variant == "A-current-baseline" else 1)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize(
    "status,code,verified,discarded",
    [
        (429, "insufficient_quota", True, True),
        (429, "rate_limit_exceeded", True, False),
        (402, None, True, False),
        (401, "insufficient_quota", True, False),
        (429, "insufficient_quota", False, False),
    ],
)
def test_only_confirmed_exact_quota_discards_unfinished_attempt(
    tmp_path, variant, status, code, verified, discarded
):
    from youtube_strataread.workbench.experiment_provider import QuotaContract

    class ProviderFailure(Exception):
        status_code = status
        body = {"error": {"code": code, "message": "private details"}}

    root, source = tmp_path / "experiment", snapshot(tmp_path)
    kwargs = options(variant)
    if variant != "A-current-baseline":
        source.path.write_text(
            source.path.read_text() + "\n2\n00:00:03,000 --> 00:00:05,000\nSource with 42 people.\n"
        )
        kwargs["segmentation"] = OutputSegmentation(
            "semantic-chapters", ("cue-000001", "cue-000002")
        )
    run_baseline(root, source, identity(), model_for(variant), attempt_id="history", **kwargs)
    ws = LocalWorkspace.open(root / "workspace")
    old = ws.document("sample")
    echo = model_for(variant)
    n = 0

    def failing(*args, **kwargs):
        nonlocal n
        n += 1
        if n > 1:
            raise ProviderFailure()
        return echo(*args, **kwargs)

    contract = QuotaContract(
        "0" * 64, 429, "insufficient_quota", "synthetic exact contract", verified
    )
    result = json.loads(
        run_baseline(
            root, source, identity(), failing, attempt_id="new", quota_contract=contract, **kwargs
        ).read_text()
    )
    assert result["state"] != "ready"
    assert (root / "attempts/new/checkpoint.json").exists() is not discarded
    assert ws.document("sample") == old
    assert Path(old["path"]).exists()
    assert ws.retained_transcript("sample")
    assert "private details" not in result["reason"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_concurrent_completed_resumes_publish_one_version(tmp_path, variant):
    from concurrent.futures import ThreadPoolExecutor

    root, source = tmp_path / "experiment", snapshot(tmp_path)

    class PowerLoss(BaseException):
        pass

    def interrupt(stage):
        if stage == "after_bundle":
            raise PowerLoss()

    with pytest.raises(PowerLoss):
        run_baseline(
            root,
            source,
            identity(),
            model_for(variant),
            attempt_id="concurrent",
            publication_fault=interrupt,
            **options(variant),
        )

    def resume(_):
        def forbidden(*args, **kwargs):
            raise AssertionError("completed work cannot regenerate")

        return json.loads(
            run_baseline(
                root,
                source,
                identity(),
                forbidden,
                attempt_id="concurrent",
                mode="resume",
                **options(variant),
            ).read_text()
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(resume, range(2)))
    assert [record["attempts"] for record in records] == [0, 0]
    assert [record["artifacts"]["manuscript_version"] for record in records] == [1, 1]
    assert len(LocalWorkspace.open(root / "workspace").generation_records("sample")) == 1


@pytest.mark.parametrize("variant", VARIANTS)
def test_large_restored_counters_do_not_discard_main_attempt(tmp_path, variant):
    root, source = tmp_path / "experiment", snapshot(tmp_path)
    echo = model_for(variant)
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ValueError("ordinary failure")
        return echo(*args, **kwargs)

    kwargs = options(variant)
    if variant != "A-current-baseline":
        source.path.write_text(
            source.path.read_text() + "\n2\n00:00:03,000 --> 00:00:05,000\nSource with 42 people.\n"
        )
        kwargs["segmentation"] = OutputSegmentation(
            "semantic-chapters", ("cue-000001", "cue-000002")
        )
    run_baseline(root, source, identity(), interrupted, attempt_id="retained", **kwargs)
    checkpoint = root / "attempts/retained/checkpoint.json"
    retained = json.loads(checkpoint.read_text())
    retained.update(calls=1000000, tokens=1000000000)
    checkpoint.write_text(json.dumps(retained))
    result = json.loads(
        run_baseline(
            root,
            source,
            identity(),
            model_for(variant),
            attempt_id="retained",
            mode="resume",
            **kwargs,
        ).read_text()
    )
    assert result["state"] == "ready"
    assert result["recovery"]["cached_responses_before"] == 1
    assert result["attempts"] == (3 if variant == "A-current-baseline" else 1)


@pytest.mark.parametrize("variant", VARIANTS)
def test_all_variants_refuse_production_and_link_redirection(tmp_path, monkeypatch, variant):
    source = snapshot(tmp_path)
    production = tmp_path / "production"
    production.mkdir()
    sentinel = production / "settings.json"
    sentinel.write_text("untouched")
    monkeypatch.setenv("YOUTUBE_WORKBENCH_WORKSPACE", str(production))

    def forbidden(*args, **kwargs):
        raise AssertionError("isolation rejects before model calls")

    for root in (production, production / "experiment", tmp_path):
        with pytest.raises(ValueError, match="production"):
            run_baseline(root, source, identity(), forbidden, **options(variant))
    root = tmp_path / "experiment"
    run_baseline(
        root, source, identity(), model_for(variant), attempt_id="safe", **options(variant)
    )
    redirection = root / "workspace/redirect"
    redirection.symlink_to(sentinel)
    with pytest.raises(ValueError, match="link"):
        run_baseline(
            root,
            source,
            identity(),
            forbidden,
            attempt_id="safe",
            mode="resume",
            **options(variant),
        )
    redirection.unlink()
    redirection.hardlink_to(sentinel)
    with pytest.raises(ValueError, match="link"):
        run_baseline(
            root,
            source,
            identity(),
            forbidden,
            attempt_id="safe",
            mode="resume",
            **options(variant),
        )
    assert sentinel.read_text() == "untouched"
    assert sorted(p.name for p in production.iterdir()) == ["settings.json"]


def test_replaying_older_completed_attempt_does_not_finish_newer_interrupted_work(tmp_path):
    class PowerLoss(BaseException):
        pass

    root, source = tmp_path / "experiment", snapshot(tmp_path)
    run_baseline(root, source, identity(), EchoModel(), attempt_id="first")

    def crash(*args, **kwargs):
        raise PowerLoss()

    with pytest.raises(PowerLoss):
        run_baseline(root, source, identity(), crash, attempt_id="second")
    ws = LocalWorkspace.open(root / "workspace")
    assert ws.asset("sample")["preparation_state"] == "generating"
    replay = json.loads(
        run_baseline(
            root, source, identity(), EchoModel(), attempt_id="first", mode="resume"
        ).read_text()
    )
    assert replay["state"] == "ready"
    assert replay["attempts"] == 0
    assert ws.asset("sample")["preparation_state"] == "generating"
