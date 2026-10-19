from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from youtube_strataread.downloader.srt import load_cues
from youtube_strataread.downloader.youtube import YouTubeError, download_subtitles


def _install_fake_ytdlp(
    monkeypatch,
    *,
    info: dict,
    files_by_lang: dict[str, list[tuple[str, str]]],
    fail_on_probe: str | None = None,
    seen_opts: list[dict] | None = None,
) -> None:
    class FakeDownloadError(Exception):
        pass

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
            if seen_opts is not None:
                seen_opts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            if not download:
                if fail_on_probe is not None:
                    raise FakeDownloadError(fail_on_probe)
                return info
            raise AssertionError("metadata must not be extracted twice")

        def process_ie_result(self, result, download=True):
            assert result is info
            tmp = Path(self.opts["outtmpl"]).parent
            lang = self.opts["subtitleslangs"][0]
            for filename, content in files_by_lang.get(lang, []):
                (tmp / filename).write_text(content, encoding="utf-8")
            return info

    yt_dlp_module = ModuleType("yt_dlp")
    yt_dlp_module.YoutubeDL = FakeYoutubeDL
    utils_module = ModuleType("yt_dlp.utils")
    utils_module.DownloadError = FakeDownloadError
    monkeypatch.setitem(sys.modules, "yt_dlp", yt_dlp_module)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils_module)


def test_download_subtitles_prefers_real_subtitles_over_live_chat(monkeypatch) -> None:
    info = {
        "id": "vid123",
        "title": "Demo",
        "subtitles": {"live_chat": [{}], "en": [{}]},
        "automatic_captions": {},
    }
    _install_fake_ytdlp(
        monkeypatch,
        info=info,
        files_by_lang={
            "en": [("vid123.en.srt", "1\n00:00:00,000 --> 00:00:01,000\nHello world\n")],
        },
    )

    result = download_subtitles("https://youtu.be/abcdefghijk")

    assert result.language == "en"
    assert result.is_auto is False
    assert "Hello world" in result.srt_text


def test_reports_video_duration_even_when_no_captions_are_available(monkeypatch) -> None:
    _install_fake_ytdlp(
        monkeypatch,
        info={"id": "abcdefghijk", "title": "An interview", "duration": 2538},
        files_by_lang={},
    )
    metadata = []
    with pytest.raises(YouTubeError, match="尚无法确认原因"):
        download_subtitles(
            "https://youtu.be/abcdefghijk",
            on_metadata=lambda video_id, duration: metadata.append((video_id, duration)),
        )
    assert metadata == [("abcdefghijk", 2538)]


@pytest.mark.parametrize("duration, live_status, expected", [
    (2538, "not_live", 2538),
    (3661.5, "was_live", 3661.5),
    (None, "not_live", None),
    (0, "not_live", None),
    (-2, "not_live", None),
    (True, "not_live", None),
    (float("nan"), "not_live", None),
    (float("inf"), "not_live", None),
    (2538, "is_live", None),
    (2538, "is_upcoming", None),
])
def test_source_duration_is_independent_of_subtitle_length(
    monkeypatch, duration, live_status, expected,
) -> None:
    _install_fake_ytdlp(
        monkeypatch,
        info={"id": "abcdefghijk", "duration": duration, "live_status": live_status,
              "subtitles": {"en": [{}]}},
        files_by_lang={"en": [("abcdefghijk.en.srt", "1\n00:00:00,000 --> 00:00:01,000\nHello\n")]},
    )
    assert download_subtitles("https://youtu.be/abcdefghijk").duration_seconds == expected


def test_caption_wait_keeps_publication_and_duration_in_the_workspace(monkeypatch, tmp_path) -> None:
    from shorts_fixture import RegularVideos
    from test_workbench_library import candidate

    from youtube_strataread.workbench.library import PreparationService, YtDlpCaptions
    from youtube_strataread.workbench.workspace import LocalWorkspace

    _install_fake_ytdlp(
        monkeypatch,
        info={"id": "abcdefghijk", "duration": 2538, "title": "No captions",
              "availability": "public", "formats": [{"format_id": "18"}], "live_status": "not_live"},
        files_by_lang={},
    )
    workspace = LocalWorkspace.open(tmp_path)
    workspace.set_meta("drain_paused", "0")
    source = candidate("abcdefghijk", "No captions")
    workspace.add_candidate(source)

    class NoGeneration:
        def generate(self, transcript):
            raise AssertionError("must not generate without captions")

    preparation = PreparationService(
        workspace, YtDlpCaptions(), NoGeneration(), shorts=RegularVideos(),
    )
    assert preparation.run_next()
    video = LocalWorkspace.open(tmp_path).activity_items("waiting_retry")["items"][0]
    assert video["duration_seconds"] == 2538
    assert video["published_at"] == source.published_at
    assert video["commenced_at"] is None


def test_download_subtitles_falls_back_to_live_chat_and_synthesizes_srt(monkeypatch) -> None:
    info = {
        "id": "vid456",
        "title": "Chat only",
        "subtitles": {"live_chat": [{}]},
        "automatic_captions": {},
    }
    live_chat_payload = json.dumps(
        {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "1000",
                "actions": [
                    {
                        "addChatItemAction": {
                            "item": {
                                "liveChatTextMessageRenderer": {
                                    "authorName": {"simpleText": "Alice"},
                                    "message": {"runs": [{"text": "Hello "}, {"text": "world"}]},
                                }
                            }
                        }
                    }
                ],
            }
        },
        ensure_ascii=False,
    )
    _install_fake_ytdlp(
        monkeypatch,
        info=info,
        files_by_lang={
            "live_chat": [("vid456.live_chat.json", live_chat_payload + "\n")],
        },
    )

    result = download_subtitles("https://youtu.be/abcdefghijk")
    cues = load_cues(result.srt_text)

    assert result.language == "live_chat"
    assert result.is_auto is False
    assert cues
    assert cues[0].text == "Alice: Hello world"


def test_download_subtitles_errors_when_live_chat_has_no_readable_messages(monkeypatch) -> None:
    info = {
        "id": "vid789",
        "title": "Empty chat",
        "subtitles": {"live_chat": [{}]},
        "automatic_captions": {},
    }
    live_chat_payload = json.dumps(
        {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "1000",
                "actions": [
                    {
                        "addLiveChatTickerItemAction": {
                            "item": {
                                "liveChatTickerPaidMessageItemRenderer": {}
                            }
                        }
                    }
                ],
            }
        },
        ensure_ascii=False,
    )
    _install_fake_ytdlp(
        monkeypatch,
        info=info,
        files_by_lang={
            "live_chat": [("vid789.live_chat.json", live_chat_payload + "\n")],
        },
    )

    with pytest.raises(YouTubeError, match="did not contain any readable chat messages"):
        download_subtitles("https://youtu.be/abcdefghijk")


def test_download_subtitles_forwards_cookie_auth_to_ytdlp(monkeypatch, tmp_path) -> None:
    info = {
        "id": "vid999",
        "title": "Cookie video",
        "subtitles": {"en": [{}]},
        "automatic_captions": {},
    }
    seen_opts: list[dict] = []
    cookiefile = tmp_path / "cookies.txt"
    cookiefile.write_text("cookies", encoding="utf-8")
    _install_fake_ytdlp(
        monkeypatch,
        info=info,
        files_by_lang={
            "en": [("vid999.en.srt", "1\n00:00:00,000 --> 00:00:01,000\nHello world\n")],
        },
        seen_opts=seen_opts,
    )

    result = download_subtitles(
        "https://youtu.be/abcdefghijk",
        cookies_from_browser="firefox:Default",
        cookiefile=cookiefile,
    )

    assert result.language == "en"
    assert len(seen_opts) == 2
    for opts in seen_opts:
        assert opts["cookiefile"] == str(cookiefile)
        assert opts["cookiesfrombrowser"] == ("firefox", "Default", None, None)


def test_download_subtitles_surfaces_cookie_hint_on_bot_check(monkeypatch) -> None:
    _install_fake_ytdlp(
        monkeypatch,
        info={},
        files_by_lang={},
        fail_on_probe="ERROR: [youtube] abc123: Sign in to confirm you're not a bot.",
    )

    with pytest.raises(YouTubeError, match="--cookies-from-browser safari"):
        download_subtitles("https://youtu.be/abcdefghijk")


def test_download_subtitles_ignores_missing_video_formats_during_subtitle_flow(monkeypatch) -> None:
    info = {
        "id": "vid321",
        "title": "Subtitle only",
        "subtitles": {"en": [{}]},
        "automatic_captions": {},
    }
    seen_opts: list[dict] = []
    _install_fake_ytdlp(
        monkeypatch,
        info=info,
        files_by_lang={
            "en": [("vid321.en.srt", "1\n00:00:00,000 --> 00:00:01,000\nHello world\n")],
        },
        seen_opts=seen_opts,
    )

    result = download_subtitles("https://youtu.be/abcdefghijk", cookies_from_browser="brave")

    assert result.language == "en"
    assert len(seen_opts) == 2
    assert all(opts["ignore_no_formats_error"] is True for opts in seen_opts)
