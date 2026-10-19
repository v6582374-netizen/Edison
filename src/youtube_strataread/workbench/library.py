"""Library, document, and one-at-a-time preparation capabilities."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from youtube_strataread.ai.base import get_provider
from youtube_strataread.ai.prompts import load_prompt
from youtube_strataread.config import resolve_provider_config
from youtube_strataread.downloader import cues_to_lines, download_subtitles, load_cues
from youtube_strataread.downloader.request_policy import (
    YouTubeError,
    YouTubeRequestPolicy,
    YouTubeRequestsStopped,
    rate_limit_error,
)
from youtube_strataread.downloader.youtube import SubtitleResult, youtube_cookie_file
from youtube_strataread.workbench import session_cookies
from youtube_strataread.workbench.retry import Failure, classify_caption, classify_model
from youtube_strataread.workbench.shorts import (
    ShortsClassifier,
    VideoTimingUnverified,
    YouTubeShortsClassifier,
)
from youtube_strataread.workbench.workspace import LocalWorkspace


class PreparationDeferred(Exception):
    """Prerequisites changed before subtitle I/O; the workspace already explains why."""


def _accepts_completed_event(classify: Callable[..., object]) -> bool:
    """Replacements of the platform classifier predate the completion flag."""
    try:
        signature = inspect.signature(classify)
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "completed_event" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


class CaptionAcquirer(Protocol):
    def acquire(self, url: str) -> SubtitleResult: ...


class ManuscriptGenerator(Protocol):
    def generate(self, transcript: str) -> str: ...


class YtDlpCaptions:
    def __init__(self, requests: YouTubeRequestPolicy | None = None, *, content_session=None) -> None:
        self.requests = requests
        self.content_session = content_session
        self.on_metadata: Callable[[str, float | None], None] | None = None

    def acquire(self, url: str, *, before_subtitles: Callable[[dict[str, Any]], None] | None = None) -> SubtitleResult:
        if self.content_session is not None:
            video_id = parse_qs(urlsplit(url).query).get('v', [''])[0]
            return self.content_session.acquire(video_id, fetch=lambda canonical_url, path:
                self.fetch_snapshot(canonical_url, path, before_subtitles=before_subtitles))
        cookies = youtube_cookie_file()
        try:
            return download_subtitles(url, cookiefile=cookies, request_policy=self.requests,
                                      on_metadata=self.on_metadata, before_subtitles=before_subtitles)
        except YouTubeError as error:
            # yt-dlp can report an empty subtitle list when YouTube has served
            # a bot check instead of video metadata. Verify before saying the
            # creator has not provided captions.
            video_id = parse_qs(urlsplit(url).query).get('v', [None])[0]
            if (cookies is not None and video_id and 'no subtitles' in str(error).lower()
                    and session_cookies.verify(cookies, video_id).get('blocked')):
                raise YouTubeError(session_cookies.SESSION_BLOCKED_REASON) from error
            raise

    def fetch_snapshot(self, url, path, *, before_subtitles=None):
        from youtube_strataread.workbench.caption_process import CaptionProcess
        return CaptionProcess().acquire(url, path, requests=self.requests,
            on_metadata=self.on_metadata, before_subtitles=before_subtitles,
            can_use=(lambda: self.content_session.snapshot_active(path))
                if self.content_session is not None else path.exists)


class ConfiguredManuscripts:
    """Reuse the existing user-configured provider and faithful Chinese prompt."""

    def generate(self, transcript: str) -> str:
        provider = get_provider(resolve_provider_config(None))
        return provider.chat(system=load_prompt(), user=transcript, temperature=0.3)


@dataclass
class PreparationService:
    workspace: LocalWorkspace
    captions: CaptionAcquirer
    manuscripts: ManuscriptGenerator
    shorts: ShortsClassifier = field(default_factory=YouTubeShortsClassifier)
    resources: object = None
    _claim_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _stopping: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.captions, YtDlpCaptions):
            self.captions.requests = self.workspace.youtube_requests
            self.captions.on_metadata = self.workspace.record_video_duration
        if isinstance(self.shorts, YouTubeShortsClassifier):
            self.shorts.requests = self.workspace.youtube_requests

    def stop(self) -> None:
        """Prevent new claims while allowing an already claimed asset to finish."""
        with self._claim_lock:
            self._stopping = True
            self.workspace.youtube_requests.stopped.set()

    def _defer_request(self, video_id: str, error: Exception) -> bool:
        if not self.workspace.caption_check_allowed(video_id):
            return True
        if isinstance(error, YouTubeRequestsStopped) or getattr(error, "kind", None) == "invalidated":
            self.workspace.set_preparation_state(video_id, "queued")
            return True
        if getattr(error, "kind", None) == "resource":
            self.workspace.set_preparation_state(video_id, "queued")
            return True
        limited = rate_limit_error(error, now=self.workspace.now())
        if limited is None:
            return False
        self.workspace.record_acquisition_failure(video_id, "rate_limited")
        self.workspace.youtube_requests.limit(limited)
        self.workspace.defer_rate_limited(video_id, attempted=limited.response_received)
        return True

    @property
    def model_account(self) -> str | None:
        """The provider account whose limits apply to manuscript requests."""
        account = getattr(self.manuscripts, "account", None)
        return str(account) if account else type(self.manuscripts).__name__

    def _defer_caption(self, video_id: str, error: Exception) -> bool:
        failure = classify_caption(error, self.workspace.now())
        self.workspace.record_acquisition_failure(video_id, failure.category or failure.kind)
        if failure.kind in {"fatal", "caption_pending"}:
            self.workspace.youtube_requests.probe_unproven()
        if failure.kind == "fatal":
            return False
        if failure.kind == "transient":
            until = self.workspace.youtube_requests.network_failed(failure.reason, failure.retry_at)
            self.workspace.defer_retry(video_id, Failure(failure.kind, failure.reason, until), charge=False)
        else:
            self.workspace.defer_retry(video_id, failure)
        return True

    def request_drain_pause(self) -> dict[str, object]:
        self.workspace.set_meta("drain_paused", "1")
        return self.workspace.activity()

    def resume(self) -> dict[str, object]:
        self.workspace.set_meta("drain_paused", "0")
        return self.workspace.activity()

    def run_next(self) -> bool:
        with self._claim_lock:
            if self._stopping:
                return False
            acquisition_allowed = not self.resources or not self.resources.status()["captions_waiting"]
            asset = self.workspace.claim_next_queued_asset(self.model_account, acquisition_allowed=acquisition_allowed)
        if asset is None:
            return False
        needs_acquisition = not self.workspace.retained_transcript(str(asset["video_id"]))
        def before_request() -> None:
            if not self.workspace.caption_check_allowed(str(asset["video_id"])):
                raise PreparationDeferred()
        context = (self.workspace.youtube_requests.acquisition(before_request=before_request)
                   if needs_acquisition else nullcontext())
        try:
            with context:
                try:
                    return self._prepare_claimed(asset)
                finally:
                    if needs_acquisition:
                        self.workspace.youtube_requests.probe_unproven()
        except PreparationDeferred:
            return True
        except Exception as error:
            if self._defer_request(str(asset["video_id"]), error):
                return True
            raise

    def _prepare_claimed(self, asset: dict[str, object]) -> bool:
        video_id = str(asset["video_id"])
        timing = self.workspace.asset(video_id)
        completed_event = timing['timing_status'] == 'event' and timing['completion_status'] == 'ended'
        if asset["manuscript_version"] is None and asset["shorts_status"] != "video":
            try:
                classify = self.shorts.classify
                if isinstance(self.shorts, YouTubeShortsClassifier) and _accepts_completed_event(classify):
                    is_short = classify(video_id, completed_event=completed_event)
                else:
                    is_short = classify(video_id)
                if type(is_short) is not bool:
                    is_short = None
            except VideoTimingUnverified:
                self.workspace.await_video_timing(video_id)
                return True
            except Exception as error:
                if self._defer_request(video_id, error) or self._defer_caption(video_id, error):
                    return True
                # An unavailable detector is uncertainty, never permission to generate.
                is_short = None
            self.workspace.record_shorts_classification(video_id, is_short)
            if is_short is not False:
                return True
        try:
            retained = self.workspace.retained_transcript(video_id)
            if retained:
                srt_text = retained["content"]
                source_language = retained["language"]
            else:
                acquisition_sequence = self.workspace.youtube_requests.snapshot().get("failure_sequence", 0)
                if isinstance(self.captions, YtDlpCaptions):
                    def before_subtitles(info: dict[str, Any]) -> None:
                        if self.resources:
                            self.resources.require_captions()
                        if info.get('id') != video_id:
                            raise YouTubeError('Subtitle identity does not match the requested video')
                        allowed = {'not_live', 'was_live', 'post_live'} if completed_event else {'not_live'}
                        if (info.get('live_status') not in allowed or info.get('is_live') or info.get('is_upcoming')
                                or (info.get('was_live') and not completed_event)):
                            self.workspace.await_video_timing(video_id)
                            raise PreparationDeferred()
                        if not self.workspace.commence_preparation(video_id):
                            raise PreparationDeferred()
                    subtitles = self.captions.acquire(str(asset['url']), before_subtitles=before_subtitles)
                else:
                    if self.workspace.youtube_requests.cooling_down() or self.workspace.youtube_requests.stopped.is_set():
                        self.workspace.youtube_requests.before_request()
                    if not self.workspace.commence_preparation(video_id):
                        return True
                    subtitles = self.captions.acquire(str(asset['url']))
                if subtitles.video_id != video_id:
                    raise YouTubeError('Subtitle identity does not match the requested video')
                self.workspace.record_video_duration(video_id, subtitles.duration_seconds)
                srt_text = subtitles.srt_text
                source_language = subtitles.language
                if not "\n".join(cues_to_lines(load_cues(srt_text))).strip():
                    raise YouTubeError("字幕结果为空，尚无法确认原因；请检查内容访问状态。")
                self.workspace.store_transcript(
                    video_id, language=subtitles.language, srt_text=srt_text
                )
                self.workspace.youtube_requests.caption_succeeded(observed_sequence=acquisition_sequence)
            transcript = "\n".join(cues_to_lines(load_cues(srt_text))).strip()
            if not transcript:
                raise YouTubeError("subtitle was empty after cleanup")
        except PreparationDeferred:
            return True
        except YouTubeError as error:
            if self._defer_request(video_id, error) or self._defer_caption(video_id, error):
                return True
            self.workspace.set_preparation_state(video_id, "unavailable", str(error))
            return True
        except Exception as error:  # acquisition may have safe adapter errors
            if self._defer_request(video_id, error) or self._defer_caption(video_id, error):
                return True
            self.workspace.set_preparation_state(video_id, "failed", _safe_error(error))
            return True
        account = self.model_account
        limit = self.workspace.model_account_limit(account)
        if limit is not None:
            # The retained transcript waits; the account limit is not this task's failure.
            self.workspace.defer_retry(video_id, Failure("account", limit["reason"], limit["until"]), charge=False)
            return True
        try:
            if not self.workspace.commence_preparation(video_id):
                return True
            self.workspace.set_preparation_state(video_id, "generating")
            generate_result = getattr(self.manuscripts, "generate_result", None)
            result = (
                generate_result(transcript, video_id, source_language=source_language)
                if generate_result
                else None
            )
            markdown = (
                result.markdown if result else self.manuscripts.generate(transcript)
            ).strip()
            if not markdown:
                raise RuntimeError("manuscript generator returned no Markdown")
            self.workspace.save_manuscript(
                video_id,
                markdown,
                generator=type(self.manuscripts).__name__,
                transcript_characters=len(transcript),
                translation=result.translation if result else None,
                provenance=result.provenance if result else None,
            )
            self.workspace.translation_checkpoint(video_id).unlink(missing_ok=True)
            self.workspace.set_preparation_state(video_id, "ready")
            self.workspace.model_account_succeeded(account)
        except Exception as error:
            failure = classify_model(error, self.workspace.now())
            if failure.kind == "account":
                failure = self.workspace.limit_model_account(account, failure)
                self.workspace.defer_retry(video_id, failure, charge=False)
            elif failure.kind == "transient":
                self.workspace.defer_retry(video_id, failure)
            else:
                self.workspace.set_preparation_state(video_id, "failed", _safe_error(error))
        return True


@dataclass
class AutomaticBatch:
    """A bounded serial batch; its state is also the activity-center progress."""

    preparation: PreparationService
    limit: int = 100
    completed: int = 0

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 100:
            raise ValueError("batch limit must be between 1 and 100")
        self._publish()

    def reset(self) -> None:
        self.completed = 0
        self._publish()

    def run_one(self) -> bool:
        if self.completed >= self.limit or not self.preparation.run_next():
            return False
        self.completed += 1
        self._publish()
        return True

    def _publish(self) -> None:
        self.preparation.workspace.set_batch_progress(limit=self.limit, completed=self.completed)


@dataclass
class LibraryService:
    workspace: LocalWorkspace
    preparation: PreparationService

    def list_assets(self, **filters: object) -> dict[str, object]:
        assets = self.workspace.assets(**filters)  # type: ignore[arg-type]
        return {"assets": assets, "total": len(assets)}

    def inspect(self, video_id: str) -> dict[str, object]:
        asset = self.workspace.asset(video_id)
        return {
            **asset,
            "summary": self.workspace.summary(video_id),
            "stage_timings": self.workspace.stage_timings(video_id),
            "generation_records": self.workspace.generation_records(video_id),
            "source_trace": {
                "video_url": asset["url"],
                "transcript_available": bool(asset["transcript_path"]),
                "transcript_path": asset["transcript_path"],
                "translation_available": bool(
                    self.workspace.document(video_id).get("translation_path")
                )
                if asset["manuscript_version"]
                else False,
            },
        }

    def document(self, video_id: str) -> dict[str, object]:
        return self.workspace.document(video_id)

    def set_reading_state(self, video_id: str, state: str) -> dict[str, object]:
        self.workspace.set_reading_state(video_id, state)
        return self.inspect(video_id)

    def regenerate(self, video_id: str) -> dict[str, object]:
        self.workspace.queue_regeneration(video_id)
        return self.inspect(video_id)

    def delete(self, video_id: str) -> dict[str, object]:
        self.workspace.delete_asset(video_id)
        return {"deleted": video_id}

    def search(self, **filters: object) -> dict[str, object]:
        return self.list_assets(**filters)

    def activity(self) -> dict[str, object]:
        return self.workspace.activity()

    def retry(self, video_id: str) -> dict[str, object]:
        return {"queued": self.workspace.retry_failed(video_id)}

    def retry_all_failed(self) -> dict[str, object]:
        return {"queued": self.workspace.retry_failed()}

    def drain_pause(self) -> dict[str, object]:
        return self.preparation.request_drain_pause()

    def resume(self) -> dict[str, object]:
        return self.preparation.resume()


def _safe_error(error: Exception) -> str:
    return str(error).strip() or error.__class__.__name__
