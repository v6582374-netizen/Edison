"""Waiting work yields, retries are bounded, and service limits stay isolated."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_workbench_library import candidate, ready_service

from youtube_strataread.downloader.request_policy import ConfirmedCaptionAbsence
from youtube_strataread.downloader.youtube import YouTubeError
from youtube_strataread.workbench import workspace as workspace_module
from youtube_strataread.workbench.library import LibraryService
from youtube_strataread.workbench.workspace import LocalWorkspace


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(workspace_module, "time", SimpleNamespace(time=lambda: now[0]))
    return now


def open_workspace(tmp_path: Path, clock) -> LocalWorkspace:
    ws = LocalWorkspace.open(tmp_path / "workspace")
    ws.set_meta("drain_paused", "0")
    ws.youtube_requests.now = lambda: clock[0]
    return ws


def fresh(video_id: str, clock):
    item = candidate(video_id)
    from datetime import datetime, timezone
    published = clock[0] - 60
    return replace(item, published_ts=published,
                   published_at=datetime.fromtimestamp(published, timezone.utc).isoformat())


class SwitchableCaptions:
    """External subtitle service whose per-video outcome the test controls."""

    def __init__(self, base) -> None:
        self.base = base
        self.failures: dict[str, Exception] = {}
        self.calls: list[str] = []

    def acquire(self, url: str):
        video_id = url.split("=")[-1]
        self.calls.append(video_id)
        if video_id in self.failures:
            raise self.failures[video_id]
        return self.base.acquire(url)


def service_with(ws: LocalWorkspace):
    service = ready_service(ws)
    service.captions = SwitchableCaptions(service.captions)
    return service


def test_pending_subtitles_yield_to_runnable_work_and_back_off(tmp_path, clock):
    ws = open_workspace(tmp_path, clock)
    ws.add_candidate(fresh("pending", clock))
    clock[0] += 1
    ws.add_candidate(fresh("healthy", clock))
    service = service_with(ws)
    service.captions.failures["pending"] = ConfirmedCaptionAbsence(
        "no subtitles (official, auto-generated, or live_chat fallback) were available for this video")

    assert service.run_next()
    waiting = ws.activity_items("waiting_retry")["items"]
    assert [item["video_id"] for item in waiting] == ["pending"]
    first_retry = waiting[0]["retry_at"]
    first_delay = first_retry - clock[0]
    assert first_delay > 0
    # The waiting video does not hold the pipeline: the next video proceeds.
    assert service.run_next()
    assert ws.asset("healthy")["preparation_state"] == "ready"
    assert not service.run_next()

    clock[0] = first_retry
    assert service.run_next()
    second_retry = ws.activity_items("waiting_retry")["items"][0]["retry_at"]
    assert second_retry - clock[0] > first_delay




class ProviderError(Exception):
    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.response = SimpleNamespace(headers={"Retry-After": retry_after} if retry_after else {})


class ScriptedModel:
    """External model account whose responses the test scripts per call."""

    account = "fixture-provider"

    def __init__(self) -> None:
        self.outcomes: list[Exception] = []
        self.calls: list[str] = []

    def generate(self, transcript: str) -> str:
        self.calls.append(transcript)
        if self.outcomes:
            raise self.outcomes.pop(0)
        return "# 中文稿件\n\n正文。\n"


def model_service(ws: LocalWorkspace):
    service = service_with(ws)
    service.manuscripts = ScriptedModel()
    return service


def test_account_limit_pauses_model_across_videos_but_not_subtitles(tmp_path, clock):
    ws = open_workspace(tmp_path, clock)
    ws.add_candidate(fresh("first", clock))
    clock[0] += 1
    ws.add_candidate(fresh("second", clock))
    service = model_service(ws)
    service.manuscripts.outcomes = [ProviderError(429, retry_after="900")]

    assert service.run_next()
    limited = ws.asset("first")
    assert limited["preparation_state"] == "waiting_retry"
    assert ws.activity()["model_limits"][0]["until"] == clock[0] + 900
    # Another video's subtitles are still acquired, but it cannot reach the limited account.
    assert service.run_next()
    assert service.captions.calls == ["first", "second"]
    assert len(service.manuscripts.calls) == 1
    assert ws.asset("second")["preparation_state"] == "waiting_retry"
    assert not service.run_next()

    clock[0] += 900
    assert service.run_next() and service.run_next()
    assert {ws.asset(v)["preparation_state"] for v in ("first", "second")} == {"ready"}
    # The account wait never spent either task's automatic budget or refetched subtitles.
    assert service.captions.calls == ["first", "second"]
    assert ws.activity()["model_limits"] == []


def test_subtitle_cooldown_does_not_stop_work_that_already_has_its_transcript(tmp_path, clock):
    ws = open_workspace(tmp_path, clock)
    ws.add_candidate(fresh("has-transcript", clock))
    clock[0] += 1
    ws.add_candidate(fresh("throttled", clock))
    service = model_service(ws)
    service.manuscripts.outcomes = [ProviderError(503, retry_after="120")]
    assert service.run_next()
    retry_at = ws.activity_items("waiting_retry")["items"][0]["retry_at"]
    assert retry_at >= clock[0] + 120

    service.captions.failures["throttled"] = YouTubeError("HTTP Error 429: Too Many Requests")
    assert service.run_next()
    assert ws.youtube_requests.cooling_down()
    clock[0] = retry_at
    assert ws.youtube_requests.cooling_down()
    assert service.run_next()
    assert ws.asset("has-transcript")["preparation_state"] == "ready"
    assert ws.asset("throttled")["preparation_state"] == "rate_limited"
    assert service.captions.calls == ["has-transcript", "throttled"]


def test_unrecoverable_model_errors_are_not_retried(tmp_path, clock):
    ws = open_workspace(tmp_path, clock)
    ws.add_candidate(fresh("bad-request", clock))
    service = model_service(ws)
    service.manuscripts.outcomes = [ProviderError(400)]
    assert service.run_next()
    assert ws.asset("bad-request")["preparation_state"] == "failed"
    assert ws.activity()["model_limits"] == []
    clock[0] += 86400
    assert not service.run_next()
    assert len(service.manuscripts.calls) == 1


def test_retry_budget_deadlines_and_account_limits_survive_restart(tmp_path, clock):
    ws = open_workspace(tmp_path, clock)
    ws.add_candidate(fresh("pending", clock))
    clock[0] += 1
    ws.add_candidate(fresh("model", clock))
    service = model_service(ws)
    service.captions.failures["pending"] = ConfirmedCaptionAbsence("confirmed absent")
    service.manuscripts.outcomes = [ProviderError(429, retry_after="3600")]
    assert service.run_next() and service.run_next()
    before = {item["video_id"]: item for item in ws.activity_items("waiting_retry")["items"]}
    limits = ws.activity()["model_limits"]

    # A crash: reopen the same workspace and recover as the host does on startup.
    reopened = open_workspace(tmp_path, clock)
    reopened.recover_interrupted_preparations()
    after = {item["video_id"]: item for item in reopened.activity_items("waiting_retry")["items"]}
    for video_id in before:
        assert after[video_id]["retry_at"] == before[video_id]["retry_at"]
        assert after[video_id]["automatic_attempts"] == before[video_id]["automatic_attempts"]
    assert reopened.activity()["model_limits"] == limits
    assert not model_service(reopened).run_next()


def test_pause_and_interrupted_work_do_not_spend_the_failure_budget(tmp_path, clock):
    ws = open_workspace(tmp_path, clock)
    ws.add_candidate(fresh("interrupted", clock))
    assert ws.claim_next_queued_asset()
    ws.record_shorts_classification("interrupted", False)  # crash during subtitle work
    reopened = open_workspace(tmp_path, clock)
    reopened.recover_interrupted_preparations()
    item = reopened.activity_items("queued")["items"][0]
    assert item["video_id"] == "interrupted" and item["automatic_attempts"] == 0
    service = service_with(reopened)
    service.request_drain_pause()
    assert not service.run_next()
    assert reopened.activity_items("queued")["items"][0]["automatic_attempts"] == 0
    service.resume()
    assert service.run_next()
    assert reopened.asset("interrupted")["preparation_state"] == "ready"


def test_stage_timings_separate_caption_waiting_processing_and_readability(tmp_path, clock):
    ws = open_workspace(tmp_path, clock)
    ws.add_candidate(fresh("timed", clock))
    discovered = clock[0]
    service = service_with(ws)
    service.captions.failures["timed"] = ConfirmedCaptionAbsence("no subtitles were available for this video")
    clock[0] += 5
    assert service.run_next()
    retry_at = ws.activity_items("waiting_retry")["items"][0]["retry_at"]
    clock[0] = retry_at
    service.captions.failures.clear()
    assert service.run_next()

    timings = LibraryService(workspace=ws, preparation=service).inspect("timed")["stage_timings"]
    assert timings["discovered_at"] == discovered
    assert timings["transcript_ready_at"] == retry_at
    assert timings["readable_at"] == retry_at
    # Five seconds queued before the first attempt plus the whole backoff wait.
    assert timings["caption_wait_seconds"] == retry_at - discovered
    assert timings["processing_seconds"] == 0  # the fixture clock does not advance during work


def test_unrecoverable_subtitle_errors_are_not_retried(tmp_path, clock):
    ws = open_workspace(tmp_path, clock)
    ws.add_candidate(fresh("private", clock))
    service = service_with(ws)
    service.captions.failures["private"] = YouTubeError("Private video. Sign in if you've been granted access")
    assert service.run_next()
    assert ws.asset("private")["preparation_state"] == "unavailable"
    clock[0] += 86400
    assert not service.run_next()
    assert service.captions.calls == ["private"]
