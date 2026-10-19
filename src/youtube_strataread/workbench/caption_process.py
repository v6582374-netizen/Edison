"""Fresh subtitle subprocess, disposable cookies and host-owned request pacing.

Only typed events cross the private pipe. External error strings never enter
logs or the Web response; the host retains scheduling and commencement checks.
"""

from __future__ import annotations

import json
import select
import subprocess
import sys
import time
from contextlib import nullcontext
from dataclasses import asdict

from youtube_strataread.downloader.request_policy import (
    ConfirmedCaptionAbsence,
    YouTubeRateLimited,
)
from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench.content_session import ContentSessionError


class CaptionProcess:
    def __init__(self, command=None):
        self.command = command or [sys.executable, "-m", __name__]

    def acquire(self, url, cookies, *, requests=None, on_metadata=None, before_subtitles=None):
        with requests.acquisition() if requests else nullcontext():
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            try:
                process.stdin.write(json.dumps({"url": url, "cookies": str(cookies)}) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + 180
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                        raise ContentSessionError("network", "字幕服务请求超时，稍后检查。")
                    try:
                        payload = json.loads(process.stdout.readline())
                    except (ValueError, OSError):
                        raise ContentSessionError(
                            "maintenance", "字幕工作进程未返回有效结果。"
                        ) from None
                    event = payload.get("event")
                    if event == "result":
                        try:
                            return SubtitleResult(**payload["result"])
                        except (TypeError, KeyError):
                            raise ContentSessionError(
                                "unknown", "字幕工作进程结果格式无效。"
                            ) from None
                    if event == "error":
                        kind = payload.get("kind")
                        if kind == "rate_limit":
                            error = YouTubeRateLimited(payload.get("retry_at"))
                            raise requests.limit(error) if requests else error
                        if kind == "no_captions":
                            raise ConfirmedCaptionAbsence("该视频已确认暂无字幕。")
                        raise ContentSessionError(
                            kind
                            if kind
                            in {
                                "user",
                                "network",
                                "maintenance",
                                "unknown",
                                "private",
                                "deleted",
                                "access_denied",
                            }
                            else "unknown",
                            {
                                "user": "YouTube 要求完成真人验证，请打开服务器浏览器。",
                                "network": "字幕服务网络暂时不可用，稍后检查。",
                                "private": "该视频已设为私有。",
                                "deleted": "该视频已删除。",
                                "access_denied": "当前账号无权访问该视频。",
                                "unknown": "字幕获取失败，原因尚未确认。",
                            }.get(kind, "字幕工作进程不可用，请检查服务配置。"),
                        )
                    if event == "request" and requests:
                        requests.before_request()
                    elif event == "video" and requests:
                        requests.before_video()
                    elif event == "metadata" and on_metadata:
                        on_metadata(payload["video_id"], payload.get("duration"))
                    elif event == "subtitles" and before_subtitles:
                        before_subtitles(payload["info"])
                    elif event not in {"request", "video", "metadata", "subtitles"}:
                        raise ContentSessionError("maintenance", "字幕工作进程协议无效。")
                    process.stdin.write("{}\n")
                    process.stdin.flush()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                process.stdin.close()
                process.stdout.close()


def _run():
    from pathlib import Path

    from youtube_strataread.downloader.youtube import download_subtitles
    from youtube_strataread.workbench.retry import classify_caption

    def emit(event, **values):
        print(json.dumps({"event": event, **values}), flush=True)

    def event(name, **values):
        emit(name, **values)
        if not sys.stdin.readline():
            raise RuntimeError("owner exited")

    class Policy:
        now = staticmethod(time.time)

        def acquisition(self):
            return nullcontext()

        def before_request(self):
            event("request")

        def before_video(self):
            event("video")

        def limit(self, error):
            return error

    payload = json.loads(sys.stdin.readline())
    try:
        result = download_subtitles(
            payload["url"],
            cookiefile=Path(payload["cookies"]),
            request_policy=Policy(),
            on_metadata=lambda video, duration: event(
                "metadata", video_id=video, duration=duration
            ),
            before_subtitles=lambda info: event("subtitles", info=info),
        )
        emit("result", result=asdict(result))
    except YouTubeRateLimited as error:
        emit("error", kind="rate_limit", retry_at=error.retry_at)
    except ConfirmedCaptionAbsence:
        emit("error", kind="no_captions")
    except Exception as error:
        failure = classify_caption(error, time.time())
        emit(
            "error",
            kind="network"
            if failure.kind == "transient"
            else failure.category
            if failure.category in {"private", "deleted", "access_denied"}
            else "user"
            if any(
                text in str(error).lower() for text in ("sign in", "not a bot", "login required")
            )
            else failure.category or "unknown",
        )


if __name__ == "__main__":
    _run()
