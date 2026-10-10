"""One-off audio-only MP4 extraction using standard sample-table chunk offsets.
Retains the original audio track, codec configuration and edit list; never transcodes ASR input here.
"""

import argparse
import concurrent.futures
import json
import struct
import subprocess
import time
from pathlib import Path


def u32(d, o=0):
    return struct.unpack_from(">I", d, o)[0]


def boxes(d):
    o = 0
    while o + 8 <= len(d):
        n = u32(d, o)
        head = 8
        if n == 1:
            n = struct.unpack_from(">Q", d, o + 8)[0]
            head = 16
        if n == 0:
            n = len(d) - o
        assert n >= head and o + n <= len(d), (o, n, len(d))
        yield d[o + 4 : o + 8], d[o + head : o + n], o, head
        o += n
    assert o == len(d)


def child(d, t):
    return next(b for name, b, _, _ in boxes(d) if name == t)


def fetch(url, a, b):
    p = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "2",
            "--max-time",
            "60",
            "--max-filesize",
            str(b - a + 1),
            "--range",
            f"{a}-{b}",
            url,
        ],
        capture_output=True,
        check=True,
    )
    assert len(p.stdout) == b - a + 1, (a, b, len(p.stdout))
    return p.stdout


def index(root: Path, rid: str):
    folder = root / "audio-only" / rid
    folder.mkdir(parents=True, exist_ok=True)
    url = json.loads((root / "sources" / rid / "media.json").read_text())["url"]
    p = folder / "header.mp4"
    if not p.exists():
        first = fetch(url, 0, 63)
        n = u32(first)
        assert first[4:8] == b"ftyp" and first[n + 4 : n + 8] == b"moov"
        msize = u32(first, n)
        assert msize < 20000000
        p.write_bytes(fetch(url, 0, n + msize - 1))
        p.chmod(0o600)
    data = p.read_bytes()
    moov = child(data, b"moov")
    track = None
    for name, b, o, h in boxes(moov):
        if name == b"trak" and child(child(b, b"mdia"), b"hdlr")[8:12] == b"soun":
            track = moov[o : o + h + len(b)]
            break
    assert track
    mdia = child(track[8:], b"mdia")
    mdhd = child(mdia, b"mdhd")
    scale = u32(mdhd, 12 if mdhd[0] == 0 else 20)
    stbl = child(child(mdia, b"minf"), b"stbl")
    sizes = child(stbl, b"stsz")
    fixed = u32(sizes, 4)
    count = u32(sizes, 8)
    sizes = (
        [fixed] * count if fixed else list(struct.unpack_from(">" + str(count) + "I", sizes, 12))
    )
    stsc = child(stbl, b"stsc")
    entries = [struct.unpack_from(">III", stsc, 8 + i * 12) for i in range(u32(stsc, 4))]
    co64 = any(n == b"co64" for n, _, _, _ in boxes(stbl))
    offsetbox = child(stbl, b"co64" if co64 else b"stco")
    offsets = list(
        struct.unpack_from(">" + str(u32(offsetbox, 4)) + ("Q" if co64 else "I"), offsetbox, 8)
    )
    chunks = []
    sample = 0
    e = 0
    for i, offset in enumerate(offsets, 1):
        while e + 1 < len(entries) and i >= entries[e + 1][0]:
            e += 1
        length = sum(sizes[sample : sample + entries[e][1]])
        chunks.append((offset, length))
        sample += entries[e][1]
    assert sample == count
    print(
        rid,
        "audio chunks",
        len(chunks),
        "audio MB",
        round(sum(n for _, n in chunks) / 1e6, 1),
        "track bytes",
        len(track),
        "timescale",
        scale,
        flush=True,
    )
    return folder, url, data, moov, track, chunks, co64


def extract(root: Path, rid: str):
    import re

    folder, url, data, moov, track, chunks, co64 = index(root, rid)
    out = folder / "audio.m4a"
    if out.exists():
        return out
    batches = [chunks[i : i + 96] for i in range(0, len(chunks), 96)]

    def batch(i, items):
        p = folder / f"batch-{i:04d}.bin"
        expected = sum(n for _, n in items)
        if p.exists() and p.stat().st_size == expected:
            return p
        ranges = ",".join(f"{a}-{a + n - 1}" for a, n in items)
        result = subprocess.run(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "3",
                "--max-time",
                "45",
                "--max-filesize",
                "2097152",
                "--range",
                ranges,
                url,
            ],
            capture_output=True,
            check=True,
        ).stdout
        if len(items) == 1:
            parts = {items[0][0]: result}
        else:
            boundary = result.split(b"\r\n", 2)[1]
            parts = {}
            for part in result.split(boundary)[1:]:
                if b"\r\n\r\n" not in part:
                    continue
                head, body = part.split(b"\r\n\r\n", 1)
                m = re.search(rb"Content-Range: bytes (\d+)-(\d+)/", head, re.I)
                assert m, head
                a, b = map(int, m.groups())
                assert body[b - a + 1 :] == b"\r\n"
                parts[a] = body[: b - a + 1]
        payload = b""
        for a, n in items:
            assert len(parts[a]) == n, (a, n, len(parts.get(a, b"")))
            payload += parts[a]
        p.write_bytes(payload)
        p.chmod(0o600)
        return p

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
        fs = [pool.submit(batch, i, items) for i, items in enumerate(batches)]
        for n, f in enumerate(concurrent.futures.as_completed(fs), 1):
            f.result()
            if n % 300 == 0:
                print(
                    rid,
                    "chunks fetched",
                    n,
                    "/",
                    len(batches),
                    round(time.time() - start, 1),
                    flush=True,
                )

    def box(name, body):
        return struct.pack(">I4s", len(body) + 8, name) + body

    mvhd = box(b"mvhd", child(moov, b"mvhd"))
    ftyp = box(b"ftyp", child(data, b"ftyp"))
    newmoov = box(b"moov", mvhd + track)
    base = len(ftyp) + len(newmoov) + 8
    patched = bytearray(track)
    offset = 8
    current = track[8:]
    for key in (b"mdia", b"minf", b"stbl", b"co64" if co64 else b"stco"):
        _, body, o, h = next(x for x in boxes(current) if x[0] == key)
        offset += o + h
        current = body
    position = offset + 8
    cursor = base
    for i, (_, n) in enumerate(chunks):
        struct.pack_into(">Q" if co64 else ">I", patched, position + i * (8 if co64 else 4), cursor)
        cursor += n
    newmoov = box(b"moov", mvhd + patched)
    temp = out.with_suffix(".partial.m4a")
    with temp.open("wb") as f:
        f.write(ftyp + newmoov + struct.pack(">I4s", sum(n for _, n in chunks) + 8, b"mdat"))
        for i in range(len(batches)):
            f.write((folder / f"batch-{i:04d}.bin").read_bytes())
    temp.chmod(0o600)
    temp.replace(out)
    print(rid, "audio-only ready", round(time.time() - start, 1), out.stat().st_size, flush=True)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--only", nargs="+", required=True)
    args = parser.parse_args()
    for recording_id in args.only:
        if not recording_id.isdigit():
            raise ValueError("Invalid recording ID")
        extract(args.root, recording_id)
