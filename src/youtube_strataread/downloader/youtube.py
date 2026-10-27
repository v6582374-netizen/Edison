"""YouTube subtitle fetcher backed by yt-dlp.

Given a URL we:
1. query video info (title, id, available subtitle languages)
2. pick the best subtitle (preferred lang -> official -> auto-generated)
3. download the SRT bytes and return them in-memory plus metadata
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from youtube_strataread.downloader.request_policy import (
    YouTubeError,
    YouTubeRequestPolicy,
    rate_limit_error,
)

_URL_PATTERNS = [
    re.compile(r"^https?://(?:www\.)?youtube\.com/watch\?[^#]*v=[\w-]+"),
    re.compile(r"^https?://youtu\.be/[\w-]+"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/shorts/[\w-]+"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/live/[\w-]+"),
]

_PREFERRED_LANGS_OFFICIAL = ["zh-Hans", "zh-CN", "zh", "en", "en-US", "en-GB"]
# Auto-generated captions: prefer the original source (usually English).
# YouTube tends to rate-limit translated auto-captions (e.g. zh-Hans),
# so we grab the source and let Step1 translate locally.
_PREFERRED_LANGS_AUTO = ["en", "en-US", "en-GB", "zh-Hans", "zh-CN", "zh"]
_LIVE_CHAT_LANG = "live_chat"


@dataclass
class SubtitleResult:
    video_id: str
    title: str
    language: str
    is_auto: bool
    srt_text: str
    duration_seconds: float | None = None


def validate_url(url: str) -> None:
    if not any(p.match(url) for p in _URL_PATTERNS):
        raise YouTubeError(
            "URL does not look like a YouTube video/shorts/live link: " + url
        )


def youtube_cookie_file() -> Path | None:
    """Signed-in YouTube session for hosts whose address YouTube treats as a bot."""
    path = os.environ.get("YOUTUBE_COOKIES_FILE")
    return Path(path) if path else None


def download_subtitles(
    url: str,
    preferred_lang: str | None = None,
    *,
    cookies_from_browser: str | None = None,
    cookiefile: Path | None = None,
    request_policy: YouTubeRequestPolicy | None = None,
    on_metadata: Callable[[str, float | None], None] | None = None,
    before_subtitles: Callable[[dict], None] | None = None,
    speech_only: bool = False,
) -> SubtitleResult:
    """Fetch the best-available SRT subtitle for ``url``.

    Strategy (to avoid 429 from trying non-existent languages):
        1. Probe video info (no download) to see which subtitles exist.
        2. Pick a single language per the preference order below.
        3. Download that language using the metadata already obtained.

    Preference order:
        1. ``preferred_lang`` (if supplied)
        2. simplified Chinese (``zh-Hans`` / ``zh-CN`` / ``zh``)
        3. English (``en`` / ``en-US`` / ``en-GB``)
        4. first official subtitle advertised
        5. any available auto-generated subtitle
    """
    validate_url(url)

    from yt_dlp import YoutubeDL  # imported lazily to speed up CLI startup
    from yt_dlp.utils import DownloadError

    downloading_subtitles = False
    commenced = False

    class PacedYoutubeDL(YoutubeDL):
        def __init__(self, opts):
            if request_policy:
                # The workbench owns retry timing; preserve normal yt-dlp defaults for the CLI.
                opts = {**opts, "retries": 0, "fragment_retries": 0, "extractor_retries": 0}
            super().__init__(opts)

        def urlopen(self, request):
            nonlocal commenced
            if request_policy:
                request_policy.before_request()
            if downloading_subtitles and not commenced:
                if before_subtitles:
                    before_subtitles(info)
                commenced = True
            try:
                return super().urlopen(request)
            except Exception as error:
                limited = rate_limit_error(error)
                if limited:
                    raise (request_policy.limit(limited) if request_policy else limited) from error
                raise

    if request_policy:
        request_policy.before_video()

    # --- phase 1: probe ----------------------------------------------------
    probe_opts = _build_ytdlp_opts(
        cookies_from_browser=cookies_from_browser,
        cookiefile=cookiefile,
        quiet=True,
        no_warnings=True,
        ignore_no_formats_error=True,
        skip_download=True,
        listsubtitles=False,
    )
    try:
        with PacedYoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        limited = rate_limit_error(e)
        if limited:
            raise (request_policy.limit(limited) if request_policy else limited) from e
        raise YouTubeError(_format_ytdlp_error(str(e))) from e

    video_id = str(info.get("id"))
    title = str(info.get("title") or video_id)
    duration = info.get("duration")
    duration_seconds = (
        float(duration)
        if type(duration) in (int, float) and math.isfinite(duration) and duration > 0
        and not info.get("is_live") and info.get("live_status") not in {"is_live", "is_upcoming"}
        else None
    )
    if on_metadata:
        # Keep source facts even if the subsequent subtitle request fails.
        on_metadata(video_id, duration_seconds)
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    if speech_only:
        # Opt-in experiment policy: chat is audience discussion, never speech.
        subs = {key: value for key, value in subs.items() if key != _LIVE_CHAT_LANG}
        auto = {key: value for key, value in auto.items() if key != _LIVE_CHAT_LANG}
    lang, is_auto = _pick_language(subs, auto, preferred_lang)
    if lang is None:
        if speech_only:
            raise YouTubeError("暂无可用字幕，未处理")
        raise YouTubeError(
            "no subtitles (official, auto-generated, or live_chat fallback) were available for this video"
        )

    downloading_subtitles = True
    # --- phase 2: download just that one language --------------------------
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        dl_opts = _build_ytdlp_opts(
            cookies_from_browser=cookies_from_browser,
            cookiefile=cookiefile,
            quiet=True,
            no_warnings=True,
            ignore_no_formats_error=True,
            skip_download=True,
            writesubtitles=not is_auto,
            writeautomaticsub=is_auto,
            subtitlesformat="srt",
            subtitleslangs=[lang],
            outtmpl=str(tmp / "%(id)s.%(ext)s"),
            convertsubtitles="srt",
        )
        try:
            with PacedYoutubeDL(dl_opts) as ydl:
                ydl.process_ie_result(info, download=True)
        except DownloadError as e:
            limited = rate_limit_error(e)
            if limited:
                raise (request_policy.limit(limited) if request_policy else limited) from e
            raise YouTubeError(_format_ytdlp_error(str(e))) from e

        srt_path = _locate_srt(tmp, video_id, lang)
        if srt_path is not None:
            srt_text = srt_path.read_text(encoding="utf-8")
        elif lang == _LIVE_CHAT_LANG:
            live_chat_path = _locate_live_chat_file(tmp, video_id)
            if live_chat_path is None:
                raise YouTubeError(
                    "yt-dlp reported live_chat but did not produce any readable chat transcript file"
                )
            srt_text = _live_chat_to_srt(live_chat_path)
            if not srt_text.strip():
                raise YouTubeError(
                    "yt-dlp downloaded live_chat but it did not contain any readable chat messages"
                )
        else:
            raise YouTubeError(
                f"yt-dlp reported subtitle language '{lang}' but the .srt file is missing"
            )
        return SubtitleResult(
            video_id=video_id,
            title=title,
            language=lang,
            is_auto=is_auto,
            srt_text=srt_text,
            duration_seconds=duration_seconds,
        )


def _build_ytdlp_opts(
    *,
    cookies_from_browser: str | None,
    cookiefile: Path | None,
    **opts: object,
) -> dict[str, object]:
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = _parse_cookies_from_browser(cookies_from_browser)
    if cookiefile is not None:
        opts["cookiefile"] = str(cookiefile)
    return opts


def _parse_cookies_from_browser(spec: str) -> tuple[str, str | None, str | None, str | None]:
    match = re.fullmatch(
        r"""(?x)
        (?P<name>[^+:]+)
        (?:\s*\+\s*(?P<keyring>[^:]+))?
        (?:\s*:\s*(?!:)(?P<profile>.+?))?
        (?:\s*::\s*(?P<container>.+))?
    """,
        spec.strip(),
    )
    if match is None:
        raise YouTubeError(
            "invalid --cookies-from-browser value; expected "
            "'BROWSER[+KEYRING][:PROFILE][::CONTAINER]'"
        )
    browser_name, keyring, profile, container = match.group(
        "name", "keyring", "profile", "container"
    )
    return (
        browser_name.lower(),
        profile,
        keyring.upper() if keyring else None,
        container,
    )


def _format_ytdlp_error(message: str) -> str:
    if "Sign in to confirm you're not a bot" not in message and (
        "Sign in to confirm you’re not a bot" not in message
    ):
        return message
    return (
        f"{message}\n\n"
        "YouTube blocked the anonymous subtitle probe for this video. "
        "Retry with a logged-in browser session, for example:\n"
        "  by fetch <url> --cookies-from-browser safari\n"
        "  by fetch <url> --cookies /path/to/cookies.txt\n"
        "The same --cookies-from-browser / --cookies options also work on "
        "'by process' and 'by run'."
    )


def _build_lang_pref(preferred: str | None, *, is_auto: bool = False) -> list[str]:
    base = _PREFERRED_LANGS_AUTO if is_auto else _PREFERRED_LANGS_OFFICIAL
    langs = list(base)
    if preferred:
        langs = [preferred, *[lc for lc in langs if lc != preferred]]
    return langs


def _pick_language(
    subs: dict[str, list[dict]],
    auto: dict[str, list[dict]],
    preferred: str | None,
) -> tuple[str | None, bool]:
    """Return (lang_code, is_auto)."""
    has_live_chat_subs = _LIVE_CHAT_LANG in subs
    has_live_chat_auto = _LIVE_CHAT_LANG in auto
    subs = {lang: tracks for lang, tracks in subs.items() if lang != _LIVE_CHAT_LANG}
    auto = {lang: tracks for lang, tracks in auto.items() if lang != _LIVE_CHAT_LANG}
    # Official subs first, with CJK-biased preference.
    for candidate in _build_lang_pref(preferred, is_auto=False):
        if candidate in subs:
            return candidate, False
    if subs:
        return next(iter(subs)), False
    # Fall back to auto-captions, biased toward the English *source*.
    for candidate in _build_lang_pref(preferred, is_auto=True):
        if candidate in auto:
            return candidate, True
    if auto:
        return next(iter(auto)), True
    if has_live_chat_auto:
        return _LIVE_CHAT_LANG, True
    if has_live_chat_subs:
        return _LIVE_CHAT_LANG, False
    return None, False


def _locate_srt(tmp: Path, video_id: str, lang: str) -> Path | None:
    direct = tmp / f"{video_id}.{lang}.srt"
    if direct.exists():
        return direct
    # fallback: any .srt that matches the language
    for p in tmp.glob(f"{video_id}.*.srt"):
        if f".{lang}." in p.name:
            return p
    # last resort: any srt file in the dir
    for p in tmp.glob("*.srt"):
        return p
    return None


def _locate_live_chat_file(tmp: Path, video_id: str) -> Path | None:
    for pattern in (
        f"{video_id}*.live_chat*.json*",
        f"{video_id}*.json*",
        "*.live_chat*.json*",
        "*.json*",
    ):
        for path in sorted(tmp.glob(pattern)):
            if not path.is_file():
                continue
            if path.suffix in {".part", ".ytdl"}:
                continue
            if path.name.endswith((".part", ".ytdl")):
                continue
            return path
    return None


def _live_chat_to_srt(path: Path) -> str:
    messages: list[tuple[int, str]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        offset_ms = _offset_ms(payload)
        for action in _iter_chat_actions(payload):
            text = _chat_action_text(action)
            if text:
                messages.append((offset_ms, text))

    if not messages:
        return ""

    parts: list[str] = []
    for idx, (start_ms, text) in enumerate(messages, start=1):
        next_ms = messages[idx][0] if idx < len(messages) else None
        end_ms = _cue_end_ms(start_ms, next_ms)
        parts.append(
            f"{idx}\n"
            f"{_format_srt_timestamp(start_ms)} --> {_format_srt_timestamp(end_ms)}\n"
            f"{text}\n"
        )
    return "\n".join(parts)


def _iter_chat_actions(payload: dict) -> list[dict]:
    wrapper = payload.get("replayChatItemAction")
    if isinstance(wrapper, dict):
        actions = wrapper.get("actions")
        if isinstance(actions, list):
            return [action for action in actions if isinstance(action, dict)]
    return [payload]


def _offset_ms(payload: dict) -> int:
    raw = payload.get("videoOffsetTimeMsec")
    try:
        return max(int(str(raw)), 0)
    except (TypeError, ValueError):
        return 0


def _chat_action_text(action: dict) -> str | None:
    renderer = _chat_renderer(action)
    if renderer is None:
        return None

    text = (
        _runs_text(renderer.get("message"))
        or _runs_text(renderer.get("headerSubtext"))
        or _runs_text(renderer.get("primaryText"))
        or _runs_text(renderer.get("purchaseAmountText"))
    )
    if not text:
        return None

    author = _runs_text(renderer.get("authorName"))
    if author:
        return f"{author}: {text}"
    return text


def _chat_renderer(action: dict) -> dict | None:
    candidates: list[dict | None] = []

    add_chat = action.get("addChatItemAction")
    if isinstance(add_chat, dict):
        candidates.append(_renderer_from_item(add_chat.get("item")))

    add_ticker = action.get("addLiveChatTickerItemAction")
    if isinstance(add_ticker, dict):
        candidates.append(_renderer_from_item(add_ticker.get("item")))

    add_banner = action.get("addBannerToLiveChatCommand")
    if isinstance(add_banner, dict):
        banner = add_banner.get("bannerRenderer")
        if isinstance(banner, dict):
            candidates.append(_renderer_from_item(banner.get("contents")))

    for renderer in candidates:
        if renderer is not None:
            return renderer
    return None


def _renderer_from_item(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    for key in (
        "liveChatTextMessageRenderer",
        "liveChatPaidMessageRenderer",
        "liveChatMembershipItemRenderer",
    ):
        renderer = item.get(key)
        if isinstance(renderer, dict):
            return renderer
    return None


def _runs_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    simple = value.get("simpleText")
    if isinstance(simple, str):
        return simple.strip()
    runs = value.get("runs")
    if not isinstance(runs, list):
        return ""
    out: list[str] = []
    for part in runs:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            out.append(text)
    return "".join(out).strip()


def _cue_end_ms(start_ms: int, next_ms: int | None) -> int:
    if next_ms is None or next_ms <= start_ms:
        return start_ms + 2000
    return min(next_ms, start_ms + 4000)


def _format_srt_timestamp(ms: int) -> str:
    td = timedelta(milliseconds=max(ms, 0))
    total_ms = int(td.total_seconds() * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
