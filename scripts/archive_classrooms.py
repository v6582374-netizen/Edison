#!/usr/bin/env python3
"""One-off authorized semester backfill. No background scheduler or browser credentials.

Run from the Edison repository with its Python environment. Sources were matched against
user-visible course pages, including the 物联网2601 switch (53514, not 53398).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coworker.curriculum import SOURCES, CourseArchive, platform_segments, source_gaps, timestamp
from coworker.secrets import write_private_text

HOST = "https://classroom.hzcu.edu.cn"
PERIOD_ENDS = [
    "08:45",
    "09:35",
    "10:35",
    "11:25",
    "12:15",
    "14:15",
    "15:05",
    "16:05",
    "16:55",
    "19:15",
    "20:05",
    "20:55",
]


def fetch_json(url):
    result = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--retry", "2", "--max-time", "45", url],
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def collect(store, through):
    cutoff = min(
        datetime.now(ZoneInfo("Asia/Shanghai")), datetime.fromisoformat(through + "T23:59:59+08:00")
    )
    existing_coverage = store.listing()["coverage"]
    for key, (name, cid) in SOURCES.items():
        if existing_coverage.get(key, {}).get("excluded"):
            print(f"{key}: excluded by user", flush=True)
            continue
        coverage = {
            "from": "2026-09-14",
            "through": through,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "complete": False,
            "platform_course_id": cid,
        }
        try:
            catalogue = fetch_json(f"{HOST}/courseapi/v2/course/catalogue?course_id={cid}")
            if not catalogue.get("success") or not isinstance(
                catalogue.get("result", {}).get("data"), list
            ):
                raise ValueError("课程目录未成功返回")
            items = catalogue["result"]["data"]
            matched = 0
            catalogued = []
            for item in items:
                title = item["title"]
                match = re.search(r"(\d{4}-\d{2}-\d{2})第(\d+)[-–](\d+)节", title)
                if not match:
                    raise ValueError("存在无法确认日期或节次的课次，目录覆盖待核对")
                date, first, last = match[1], int(match[2]), int(match[3])
                if first < 1 or last > 12 or first > last:
                    raise ValueError("平台节次无效")
                if date < coverage["from"] or date > through:
                    continue
                ends = datetime.fromisoformat(date + "T" + PERIOD_ENDS[last - 1] + ":00+08:00")
                content = json.loads(item.get("content") or "{}")
                video_url = (content.get("save_playback") or {}).get("contents") or (
                    content.get("playback") or {}
                ).get("url")
                media = {"url": video_url, "duration": 0}
                # Platform duration is in 100-nanosecond units (verified against player time).
                duration = (
                    float((content.get("save_playback") or {}).get("contents_duration") or 0)
                    / 10_000_000
                )
                media["duration"] = duration
                rid = str(item["sub_id"])
                meta = {
                    "id": rid,
                    "course": key,
                    "platform_course_id": cid,
                    "name": name,
                    "title": title,
                    "date": date,
                    "first": first,
                    "last": last,
                    "teacher": item.get("lecturer_name", ""),
                    "room": content.get("address", ""),
                    "source_url": f"{HOST}/livingroom?course_id={cid}&sub_id={rid}&tenant_code=21",
                    "warnings": [],
                    "duration": duration,
                }
                catalogued.append(
                    {**meta, "platform_status": str(item.get("status")), "ended": ends <= cutoff}
                )
                if ends > cutoff:
                    continue
                # A scheduled entry is not proof of a finished recording.
                if str(item.get("status")) != "6":
                    meta["warnings"] = ["平台未提供可用回放，不能生成课堂内容"]
                    store.import_recording(meta)
                    store.set_state(rid, "unavailable", "平台未提供可用回放")
                    continue
                store.import_recording(meta)
                write_private_text(
                    store.root / "sources" / rid / "media.json",
                    json.dumps(media, ensure_ascii=False),
                )
                try:
                    raw_path = store.root / "sources" / rid / "platform.json"
                    raw = (
                        json.loads(raw_path.read_text())
                        if raw_path.exists()
                        else fetch_json(
                            f"{HOST}/courseapi/v3/web-socket/search-trans-result?sub_id={rid}&format=json"
                        )
                    )
                    store._write_source(rid, "platform", raw)
                    segments = platform_segments(raw)
                    gaps = source_gaps(segments, duration)
                    meta["gaps"] = gaps
                    if gaps:
                        meta["warnings"].append(
                            "平台字幕存在较长空档；空档原因尚不能仅由字幕判断。"
                        )
                    if not segments:
                        meta["warnings"].append("平台没有可用字幕，需要录播音频补转写。")
                    store.import_recording(meta, raw)
                    matched += 1
                    print(
                        f"{key} {date} {first}-{last}: {len(segments)} segments; {len(gaps)} gaps",
                        flush=True,
                    )
                except Exception as e:
                    store.set_state(
                        rid, "needs_transcript", "平台字幕获取未成功，需重试或音频补转写"
                    )
                    print(f"{key} {rid}: transcript failed ({type(e).__name__})", flush=True)
                time.sleep(0.2)
            write_private_text(
                store.root / "catalogues" / f"{cid}.json",
                json.dumps(catalogued, ensure_ascii=False, indent=2),
            )
            coverage.update(complete=True, total=len(catalogued), transcript_acquired=matched)
        except Exception as e:
            coverage["error"] = str(e)[:120]
            print(f"{key}: catalogue failed ({type(e).__name__})", flush=True)
        store.save_coverage(key, coverage)


def supplement(store, only=None):
    """Recognize only native gaps or wholly absent transcripts; no paid audio API."""
    import wave

    import mlx_whisper
    import numpy as np
    from silero_vad import get_speech_timestamps, load_silero_vad

    engine = "mlx-community/whisper-large-v3-turbo"
    profile = "rms-v1"
    provenance = engine + " + 10s RMS normalization + Silero VAD 0.35"
    vad = load_silero_vad(onnx=True)
    for record in store.listing()["recordings"]:
        rid = record["id"]
        if only and rid not in only:
            continue
        if (
            record["state"] == "unavailable"
            or (store.root / "sources" / rid / "supplement.json").exists()
        ):
            continue
        native = store._read_source(rid, "platform-segments", [])
        media_path = store.root / "sources" / rid / "media.json"
        if not media_path.exists():
            continue
        media = json.loads(media_path.read_text())
        # An English class incorrectly recognized as Chinese needs a full audio pass.
        text = "".join(s["text"] for s in native)
        wrong_language = (
            record["course"] == "tourism"
            and bool(text)
            and sum("\u4e00" <= c <= "\u9fff" for c in text) > len(text) * 0.2
        )
        source_ranges = (
            source_gaps(native, media["duration"])
            if native and not wrong_language
            else [[0, media["duration"]]]
        )
        ranges = []
        for start, end in source_ranges:
            while end - start > 600:
                ranges.append([start, start + 600])
                start += 600
            ranges.append([start, end])
        if not ranges:
            continue
        if not media["url"] or not media["duration"]:
            store.set_state(rid, "needs_transcript", "录播音频来源不可用，待核对")
            continue
        local_audio = store.root / "audio-only" / rid / "audio.m4a"
        media_input = str(local_audio) if local_audio.exists() else media["url"]
        segments, reviewed = [], []
        try:
            for start, end in ranges:
                range_result = (
                    store.root
                    / "asr-checkpoints"
                    / rid
                    / profile
                    / f"{start:.3f}-{end:.3f}"
                    / "result.json"
                )
                if range_result.exists():
                    saved = json.loads(range_result.read_text())
                    if saved.get("engine") == provenance:
                        segments.extend(saved["segments"])
                        reviewed.append([start, end])
                        continue
                range_segments = []
                cached_audio = store.root / "audio-cache" / rid / f"{start:.3f}-{end:.3f}.wav"
                wav = (
                    cached_audio
                    if cached_audio.exists()
                    else store.root / "sources" / rid / "working-audio.wav"
                )
                if not cached_audio.exists():
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-nostdin",
                            "-loglevel",
                            "error",
                            "-y",
                            "-ss",
                            str(start),
                            "-i",
                            media_input,
                            "-t",
                            str(end - start),
                            "-vn",
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            str(wav),
                        ],
                        check=True,
                        timeout=1200,
                    )
                with wave.open(str(wav)) as audio_file:
                    if audio_file.getnframes() / audio_file.getframerate() < end - start - 2:
                        raise ValueError("音频分段不完整，保留断点等待重试")
                    audio = (
                        np.frombuffer(
                            audio_file.readframes(audio_file.getnframes()), dtype=np.int16
                        ).astype(np.float32)
                        / 32768
                    )
                # Some classroom microphones are audible but about 60 dB below full scale.
                # Normalize short blocks before VAD; a door slam must not suppress an entire
                # lecture. Bound gain and keep actual silence near zero.
                for offset in range(0, len(audio), 160000):
                    block = audio[offset : offset + 160000]
                    rms = float(np.sqrt(np.mean(block * block)))
                    gain = min(300.0, max(1.0, 0.05 / max(rms, 1e-8)))
                    block[:] = np.clip(block * gain, -0.98, 0.98)
                speech = get_speech_timestamps(
                    audio,
                    vad,
                    sampling_rate=16000,
                    return_seconds=True,
                    threshold=0.35,
                    min_speech_duration_ms=500,
                    min_silence_duration_ms=700,
                    speech_pad_ms=300,
                )
                # Keep silence out of recognition and bound decoding to short speech windows.
                windows = []
                for span in speech:
                    a, b = span["start"], span["end"]
                    if windows and a - windows[-1][1] < 1 and b - windows[-1][0] < 28:
                        windows[-1][1] = b
                    else:
                        while b - a > 28:
                            windows.append([a, a + 28])
                            a += 28
                        windows.append([a, b])
                for window_index, (a, b) in enumerate(windows):
                    checkpoint = (
                        store.root
                        / "asr-checkpoints"
                        / rid
                        / profile
                        / f"{start:.3f}-{end:.3f}"
                        / f"{window_index}.json"
                    )
                    if checkpoint.exists():
                        recognized = json.loads(checkpoint.read_text())
                    else:
                        result = mlx_whisper.transcribe(
                            audio[int(a * 16000) : int(b * 16000)],
                            path_or_hf_repo=engine,
                            language="en" if record["course"] == "tourism" else "zh",
                            condition_on_previous_text=False,
                            temperature=0.0,
                            verbose=None,
                            word_timestamps=False,
                            no_speech_threshold=0.6,
                            logprob_threshold=-1.0,
                        )
                        recognized = []
                        for s in result["segments"]:
                            if s.get("no_speech_prob", 0) > 0.6 or s.get("avg_logprob", 0) < -1.0:
                                continue
                            text = s["text"].strip()
                            if text:
                                absolute_start = start + a + max(0, s["start"])
                                absolute_end = min(end, start + b, start + a + s["end"])
                                if absolute_end <= absolute_start:
                                    continue
                                recognized.append(
                                    {
                                        "start": absolute_start,
                                        "end": absolute_end,
                                        "text": text,
                                    }
                                )
                        write_private_text(checkpoint, json.dumps(recognized, ensure_ascii=False))
                    range_segments.extend(recognized)
                    if window_index % 20 == 0:
                        print(f"{rid} ASR speech {window_index + 1}/{len(windows)}", flush=True)
                write_private_text(
                    range_result,
                    json.dumps(
                        {"engine": provenance, "segments": range_segments}, ensure_ascii=False
                    ),
                )
                segments.extend(range_segments)
                reviewed.append([start, end])
                wav.unlink(missing_ok=True)
                print(f"{rid} ASR {timestamp(start)}-{timestamp(end)} done", flush=True)
            store.save_supplement(
                rid,
                segments,
                engine=provenance,
                reviewed_ranges=reviewed,
                replaces_native=wrong_language or not native,
            )
            meta = {k: v for k, v in record.items() if k not in {"documents", "state", "detail"}}
            meta["warnings"] = [
                w
                for w in meta.get("warnings", [])
                if not w.startswith("平台字幕存在") and not w.startswith("平台没有")
            ]
            meta["warnings"].append(
                "平台字幕空档或缺失部分已用本机音频识别补充；补转写仍可能有误，未人工逐字校对。"
                if segments
                else (
                    "已检查平台字幕空档的录播音频，未获得可用补转写；不能据此断定空档没有讲话。"
                    if native
                    else "平台没有字幕；已检查全长录播音轨，未获得可用讲话转写，无法生成可靠笔记。不能据此断言课堂没有讲话。"
                )
            )
            store.import_recording(meta)
            if not native and not segments:
                store.set_state(rid, "needs_transcript", meta["warnings"][-1])
        except Exception as e:
            store.set_state(rid, "needs_transcript", "音频补转写未完成，原始平台响应与处理断点已保留，可重试")
            print(f"{rid} ASR failed ({type(e).__name__}): {str(e)[:150]}", flush=True)
            (store.root / "sources" / rid / "working-audio.wav").unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--through", default="2026-10-10")
    parser.add_argument("--supplement", action="store_true")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    store = CourseArchive(args.root)
    if args.supplement:
        supplement(store, args.only)
    else:
        collect(store, args.through)
