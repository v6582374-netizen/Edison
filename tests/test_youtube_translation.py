"""Translation workflow safety and durability, exercised through actual host adapters."""

import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from translation_fixture import EchoModel

from coworker.providers.base import AssistantTurn, TokenUsage
from coworker.server.youtube import HostManuscripts, YouTubeWorkbench, _bounded_summary
from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench.cutover import CutoverHold
from youtube_strataread.workbench.discovery import Candidate
from youtube_strataread.workbench.library import PreparationService
from youtube_strataread.workbench.translation import (
    DEFAULTS,
    BudgetExceeded,
    TranslationError,
    TranslationPipeline,
    check_completeness,
    chinese_only,
    settings,
    validate_settings,
)
from youtube_strataread.workbench.workspace import LocalWorkspace


def pipeline(model=None, config=None, path=None):
    return TranslationPipeline(model or EchoModel(), "host-model", config or DEFAULTS, path)


def test_long_input_calls_upstream_translate_and_preserves_plain_text(monkeypatch):
    from unittest.mock import Mock

    import translation_agent
    from translation_agent import utils

    text = "\n".join(f"Sentence {i}: " + "a detailed statement. " * 20 for i in range(70))
    original = translation_agent.translate
    spy = Mock(wraps=original)
    monkeypatch.setattr(translation_agent, "translate", spy)
    multi = Mock(wraps=utils.multichunk_translation)
    monkeypatch.setattr(utils, "multichunk_translation", multi)
    model = EchoModel()
    result = pipeline(model).run(text)
    spy.assert_called_once()
    multi.assert_called_once()
    assert len(model.calls) > 10
    assert result.translation
    assert result.provenance["upstream"] == "e0fc605acbb5d78cb7a58a98bc8bd8f0056df49c"
    assert any("<SOURCE_TEXT>" in call[1][-1]["content"] for call in model.calls)
    assert all('"units"' not in call[1][-1]["content"] for call in model.calls)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("这是完整的简体中文。我们保留所有论点。", "这是完整的简体中文。我们保留所有论点。"),
        ("這是完整的繁體中文。我們保留所有論點。", "这是完整的繁体中文。我们保留所有论点。"),
    ],
)
def test_chinese_bypasses_three_translation_stages(text, expected):
    model = EchoModel()
    result = pipeline(model).run(text)
    assert len(model.calls) == 1
    assert result.translation == expected
    assert result.provenance["translation_skipped"] is True


def test_mixed_language_is_not_misclassified_as_chinese():
    assert not chinese_only("我们讨论 inference scaling 的限制。")
    model = EchoModel()
    pipeline(model).run("我们讨论 inference scaling 的限制。")
    assert len(model.calls) == 4


@pytest.mark.parametrize("bad", ["", "42", "A summary"])
def test_gross_shortening_is_rejected_at_publication_boundary(bad):
    with pytest.raises(TranslationError):
        check_completeness("There were 42 people. " * 10, bad)


def test_truncation_is_not_published_and_manual_retry_resumes_snapshot(tmp_path):
    path = tmp_path / "attempt.json"
    model = EchoModel()
    model.truncate_at = 3
    with pytest.raises(TranslationError, match="截断"):
        pipeline(model, path=path).run("Full source with 42 people.")
    state = json.loads(path.read_text())
    assert set(state["outputs"]) == {
        "upstream:0:one_chunk_initial_translation",
        "upstream:1:one_chunk_reflect_on_translation",
    }
    changed = copy.deepcopy(DEFAULTS)
    changed["prompts"]["initial"]["system"] = "Future instruction"
    model.truncate_at = None
    result = TranslationPipeline(model, "new-model", changed, path).run(
        "Full source with 42 people."
    )
    assert len(model.calls) == 5
    assert all(call[0] == "host-model" for call in model.calls)
    assert result.provenance["calls"] == 5
    assert result.provenance["settings"] == DEFAULTS


def test_transient_retry_is_bounded_and_counts_against_budget(monkeypatch):
    monkeypatch.setattr("youtube_strataread.workbench.translation.time.sleep", lambda _: None)
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args)
        raise TimeoutError("secret provider internals")

    with pytest.raises(TranslationError, match="Edison") as error:
        pipeline(unavailable).run("Source")
    assert len(calls) == 3
    assert "secret" not in str(error.value)


def test_budget_discards_only_current_attempt_and_does_not_reset_on_retry(tmp_path):
    path = tmp_path / "attempt.json"
    old = tmp_path / "manuscript-v1.md"
    old.write_text("Previous document")
    config = copy.deepcopy(DEFAULTS)
    config["max_calls"] = 2
    model = EchoModel()
    model.truncate_at = 2
    with pytest.raises(TranslationError, match="截断"):
        pipeline(model, config, path).run("Source")
    assert path.exists()
    with pytest.raises(BudgetExceeded):
        pipeline(model, config, path).run("Source")
    assert len(model.calls) == 2
    assert not path.exists()
    assert old.read_text() == "Previous document"


def test_actual_usage_over_budget_discards_even_final_response(tmp_path):
    model = EchoModel()
    model.usage = TokenUsage(input=2000000, output=1)
    path = tmp_path / "attempt.json"
    with pytest.raises(BudgetExceeded):
        pipeline(model, path=path).run("只有中文。")
    assert not path.exists()


def test_prompt_validation_and_legacy_migration_share_one_store(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("generation_prompt", "保留美元 $100，不扩写。")
    cfg = settings(ws)
    assert "$100" in cfg["prompts"]["composition"]["system"]
    assert settings(ws) == cfg
    assert ws.meta("generation_prompt") == "保留美元 $100，不扩写。"
    cfg["prompts"]["initial"]["user"] = "Missing source"
    with pytest.raises(ValueError, match="占位符"):
        validate_settings(cfg)
    cfg = copy.deepcopy(DEFAULTS)
    cfg["prompts"]["review"]["system"] = "{unknown}"
    with pytest.raises(ValueError, match="未知"):
        validate_settings(cfg)


def test_preparation_retains_full_translation_and_old_version_on_budget_discard(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    from test_workbench_library import candidate
    ws.set_meta("drain_paused", "0")
    ws.add_candidate(candidate("one"))
    downloads = []

    def acquire(url):
        downloads.append(url)
        return SubtitleResult(
            "one",
            "Title",
            "en",
            False,
            "1\n00:00:00,000 --> 00:00:02,000\nSource with 42 people.\n",
        )

    model = EchoModel()
    manager = SimpleNamespace(model="host-model", provider_complete=model)
    preparation = PreparationService(
        ws, SimpleNamespace(acquire=acquire), HostManuscripts(manager, ws)
    )
    from shorts_fixture import RegularVideos
    preparation.shorts = RegularVideos()
    assert preparation.run_next()
    first = ws.document("one")
    assert Path(first["translation_path"]).read_text().strip() == "Source with 42 people."
    assert Path(first["path"]).with_name("translation-run.json").exists()
    cfg = settings(ws)
    cfg["max_calls"] = 1
    ws.set_meta("youtube_translation", json.dumps(cfg))
    ws.queue_regeneration("one")
    assert preparation.run_next()
    assert ws.asset("one")["preparation_state"] == "failed"
    assert "超出处理预算" in ws.asset("one")["failure_reason"]
    assert ws.document("one") == first
    assert ws.retained_transcript("one")
    assert not ws.translation_checkpoint("one").exists()
    assert len(downloads) == 1
    assert not preparation.run_next()


def test_settings_capability_validation_and_restart_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_WORKBENCH_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(YouTubeWorkbench, "_run", lambda self: None)
    ws = LocalWorkspace.open(tmp_path)
    ws.add_candidate(Candidate("one", "c", "C", "Title", "url", "2026-09-19", 1))
    ws.set_preparation_state("one", "generating")
    workbench = YouTubeWorkbench(SimpleNamespace())
    try:
        response = workbench.dispatch("translation.settings", {})
        config = response["result"]["settings"]
        config["max_calls"] = 50
        assert workbench.dispatch("translation.set_settings", {
            "settings": config, "summary_prompt": "概括关键结论，不照抄开头。"
        })["ok"]
        assert settings(LocalWorkspace.open(tmp_path))["max_calls"] == 50
        assert LocalWorkspace.open(tmp_path).meta("youtube_summary_prompt") == "概括关键结论，不照抄开头。"
        config["max_calls"] = 0
        assert not workbench.dispatch("translation.set_settings", {"settings": config})["ok"]
        # An interruption is not a task failure: it requeues without spending budget.
        assert ws.asset("one")["preparation_state"] == "queued"
        assert ws.activity_items("queued")["items"][0]["automatic_attempts"] == 0
    finally:
        workbench.close()


def test_summary_is_generated_once_per_video_and_survives_prompt_and_manuscript_changes(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.add_candidate(Candidate("one", "c", "C", "Title", "url", "2026-09-19", 1))
    ws.save_manuscript("one", "# First version\nA complete article.", generator="test", transcript_characters=20)
    complete = Mock(return_value=AssistantTurn(text="核心主题与重要结论。", finish_reason="stop"))
    manager = SimpleNamespace(model="host-model", provider_complete=complete,
                              get_settings=lambda: {"model_ready": True})
    workbench = object.__new__(YouTubeWorkbench)
    workbench.workspace = ws
    workbench.cutover = CutoverHold(ws)
    workbench.managed_content = False
    workbench.manager = manager
    workbench.summary_locks = {}
    workbench.summary_locks_guard = threading.Lock()

    config = settings(ws)
    saved = workbench.dispatch("translation.set_settings", {
        "settings": config, "summary_prompt": "突出关键结论。"
    })
    assert saved["ok"]
    assert saved["result"]["summary_prompt"] == "突出关键结论。"
    assert workbench.dispatch("summary.ensure", {"video_id": "one"}) == {
        "ok": True, "result": {"summary": "核心主题与重要结论。"}
    }
    assert "突出关键结论。" in complete.call_args.args[1][0]["content"]
    assert "# First version\nA complete article." in complete.call_args.args[1][1]["content"]
    ws.set_meta("youtube_summary_prompt", "后来修改的提示词。")
    ws.save_manuscript("one", "# Second version\nDifferent article.", generator="test", transcript_characters=20)
    assert workbench.dispatch("summary.ensure", {"video_id": "one"})["result"]["summary"] == "核心主题与重要结论。"
    assert LocalWorkspace.open(tmp_path).summary("one") == "核心主题与重要结论。"
    assert complete.call_count == 1
    ws.delete_asset("one")
    assert ws.summary("one") is None


def test_failed_summary_can_retry_without_saving_and_length_is_bounded(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.add_candidate(Candidate("one", "c", "C", "Title", "url", "2026-09-19", 1))
    ws.save_manuscript("one", "# Complete article", generator="test", transcript_characters=20)
    complete = Mock(side_effect=[
        TimeoutError(),
        AssistantTurn(text="概览：" + "要" * 140, finish_reason="stop"),
        AssistantTurn(text="这是压缩后的完整概览。", finish_reason="stop"),
    ])
    manager = SimpleNamespace(model="host-model", provider_complete=complete,
                              get_settings=lambda: {"model_ready": True})
    workbench = object.__new__(YouTubeWorkbench)
    workbench.workspace = ws
    workbench.cutover = CutoverHold(ws)
    workbench.managed_content = False
    workbench.manager = manager
    workbench.summary_locks = {}
    workbench.summary_locks_guard = threading.Lock()

    assert not workbench.dispatch("summary.ensure", {"video_id": "one"})["ok"]
    assert ws.summary("one") is None
    response = workbench.dispatch("summary.ensure", {"video_id": "one"})
    assert response["ok"]
    assert response["result"]["summary"] == "这是压缩后的完整概览。"
    assert ws.summary("one") == response["result"]["summary"]
    assert complete.call_count == 3
    with pytest.raises(ValueError, match="超过"):
        _bounded_summary("这是主题。" * 30)


def test_simultaneous_summary_requests_share_one_generation(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.add_candidate(Candidate("one", "c", "C", "Title", "url", "2026-09-19", 1))
    ws.save_manuscript("one", "# Complete article", generator="test", transcript_characters=20)

    def complete(*_args, **_kwargs):
        time.sleep(0.05)
        return AssistantTurn(text="一段已保存的概览。", finish_reason="stop")

    manager = SimpleNamespace(model="host-model", provider_complete=Mock(side_effect=complete),
                              get_settings=lambda: {"model_ready": True})
    workbench = object.__new__(YouTubeWorkbench)
    workbench.workspace = ws
    workbench.cutover = CutoverHold(ws)
    workbench.managed_content = False
    workbench.manager = manager
    workbench.summary_locks = {}
    workbench.summary_locks_guard = threading.Lock()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: workbench.ensure_summary("one"), range(2)))
    assert results == ["一段已保存的概览。"] * 2
    assert manager.provider_complete.call_count == 1


def test_regeneration_cannot_erase_an_active_checkpoint(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.add_candidate(Candidate("one", "c", "C", "Title", "url", "2026-09-19", 1))
    ws.set_preparation_state("one", "generating")
    checkpoint = ws.translation_checkpoint("one")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("retained")
    with pytest.raises(ValueError, match="正在生成"):
        ws.queue_regeneration("one")
    assert checkpoint.read_text() == "retained"
    assert ws.asset("one")["preparation_state"] == "generating"


def test_billing_error_is_visible_without_retry_or_secret_exposure():
    class BillingFailure(Exception):
        status_code = 402

    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise BillingFailure("secret")

    with pytest.raises(TranslationError, match="HTTP 402") as error:
        pipeline(fail).run("source")
    assert len(calls) == 1
    assert "secret" not in str(error.value)


def test_account_rate_limit_leaves_the_job_immediately_for_the_shared_gate():
    class RateLimited(Exception):
        status_code = 429

    calls = []

    def limited(*args, **kwargs):
        calls.append(args)
        raise RateLimited("secret")

    with pytest.raises(TranslationError) as error:
        pipeline(limited).run("source")
    assert len(calls) == 1  # no in-job sleeping against a limited account
    assert error.value.__cause__.status_code == 429


def test_host_manuscripts_identify_the_provider_account():
    manager = SimpleNamespace(model="openai/gpt-x", provider_complete=None,
                              _model_provider=lambda model: "openai")
    assert HostManuscripts(manager).account == "openai"
    assert HostManuscripts(SimpleNamespace(model="plain")).account == "plain"


def test_v1_attempt_is_retained_and_requires_explicit_regeneration(tmp_path):
    path = tmp_path / "attempt.json"
    path.write_text(
        json.dumps({"source": "old", "outputs": {"0:initial": "old output"}, "calls": 5})
    )
    original = path.read_text()
    with pytest.raises(TranslationError, match="旧版任务"):
        pipeline(path=path).run("Source")
    assert path.read_text() == original


def test_v1_prompts_are_archived_without_reusing_the_incompatible_json_protocol(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    old = {
        "prompts": {
            "initial": {"system": "Custom JSON translation", "user": "$units"},
            "composition": {"system": "保留全部观点"},
        },
        "max_calls": 33,
        "max_tokens": 900000,
    }
    ws.set_meta("youtube_translation", json.dumps(old))
    migrated = settings(ws)
    assert json.loads(ws.meta("youtube_translation_v1_archive")) == old
    assert migrated["prompts"]["initial"] == DEFAULTS["prompts"]["initial"]
    assert migrated["prompts"]["composition"]["system"] == "保留全部观点"
    assert migrated["max_calls"] == 33


@pytest.mark.parametrize("country", ["", "中国大陆"])
@pytest.mark.parametrize("long", [False, True])
def test_every_selected_prompt_override_reaches_upstream_model_calls(country, long):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["country"] = country
    for stage, pair in cfg["prompts"].items():
        for role in pair:
            pair[role] = f"OVERRIDE-{stage}-{role}\n" + pair[role]
    source = "A detailed statement about the project. " * (220 if long else 1)
    model = EchoModel()
    pipeline(model, cfg).run(source)
    seen = set()
    for _, messages, _ in model.calls:
        first = messages[-1]["content"].splitlines()[0]
        seen.add(first)
        assert messages[0]["content"].startswith("OVERRIDE-")
        assert "{tagged_text}" not in messages[-1]["content"]
        assert "{translation_1_chunk}" not in messages[-1]["content"]
        assert "{reflection_chunk}" not in messages[-1]["content"]
    role = "multichunk_user" if long else "user"
    assert seen == {
        f"OVERRIDE-initial-{role}",
        f"OVERRIDE-review-{role}" + ("_region" if country else ""),
        f"OVERRIDE-revision-{role}",
        "OVERRIDE-composition-user",
    }


def test_chinese_templates_keep_all_upstream_placeholders():
    from youtube_strataread.workbench.translation import UPSTREAM_PROMPTS, placeholders

    for stage, pair in UPSTREAM_PROMPTS.items():
        for role, original in pair.items():
            assert placeholders(DEFAULTS["prompts"][stage][role]) == placeholders(original)


def test_saved_english_defaults_upgrade_without_losing_custom_settings(tmp_path):
    from youtube_strataread.workbench.translation import UPSTREAM_PROMPTS

    ws = LocalWorkspace.open(tmp_path)
    old = copy.deepcopy(DEFAULTS)
    old["prompts"].update(copy.deepcopy(UPSTREAM_PROMPTS))
    old["prompts"]["review"]["system"] = "我的审校指令"
    old["max_calls"] = 120
    old["country"] = "中国大陆"
    ws.set_meta("youtube_translation", json.dumps(old))
    migrated = settings(ws)
    assert migrated["prompts"]["initial"] == DEFAULTS["prompts"]["initial"]
    assert migrated["prompts"]["revision"] == DEFAULTS["prompts"]["revision"]
    assert migrated["prompts"]["review"]["system"] == "我的审校指令"
    for role in UPSTREAM_PROMPTS["review"].keys() - {"system"}:
        assert migrated["prompts"]["review"][role] == DEFAULTS["prompts"]["review"][role]
    assert migrated["max_calls"] == 120
    assert migrated["country"] == "中国大陆"
    assert settings(LocalWorkspace.open(tmp_path)) == migrated
    assert json.loads(ws.meta("youtube_translation")) == migrated


def test_console_reports_actual_requests_retries_and_checkpoints(tmp_path):
    logs = []
    model = EchoModel()
    attempts = 0

    def complete(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        assert "request started" in logs[-1]
        if attempts == 1:
            raise TimeoutError("private upstream details")
        return model(*args, **kwargs)

    runner = TranslationPipeline(
        complete, "host-model", DEFAULTS, tmp_path / "checkpoint.json", on_console=logs.append
    )
    runner.run("A complete source for reading.")
    assert any("request failed; TimeoutError" in line for line in logs)
    assert any("retry in 1s" in line for line in logs)
    assert any("response received" in line for line in logs)
    assert any("validated and checkpoint saved" in line for line in logs)
    assert not any("private upstream details" in line for line in logs)
    previous = attempts
    runner.run("A complete source for reading.")
    assert attempts == previous
    assert "cache hit" in logs[-1]
