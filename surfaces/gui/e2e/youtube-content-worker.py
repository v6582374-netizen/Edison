"""Controlled external captions in a genuinely fresh worker process."""
import json
import os
import sys
import time
from pathlib import Path
root = Path(sys.argv[1])
payload = json.loads(sys.stdin.readline())
video_id = payload['url'].split('=')[-1]
path = Path(payload['cookies'])
assert 'fixture-session' in path.read_text()
with (root / 'caption-requests.jsonl').open('a') as log:
    log.write(json.dumps({'video_id':video_id, 'pid':os.getpid(), 'path':str(path)})+'\n')
# Simulates yt-dlp's CookieJar.save on close: only its task copy is changed.
path.write_text('response cookie mutation')
for event in [{'event':'video'}, {'event':'request'}, {'event':'subtitles','info':{'id':video_id,'live_status':'not_live'}}]:
    if event["event"] == "subtitles" and (root / "caption-metadata-hold").exists():
        (root / "caption-metadata-active").write_text(video_id)
        deadline = time.monotonic() + 30
        while (root / "caption-metadata-hold").exists() and time.monotonic() < deadline:
            time.sleep(.02)
    print(json.dumps(event), flush=True)
    assert sys.stdin.readline()
source = 'The full original source is retained.'
if (root / 'measurement').exists():
    source = ' '.join(f'Section {index}. A lasting library preserves the original evidence and the complete reading manuscript. Readers can compare the source with the translation and return to their saved place.' for index in range(180))
mode = (root / 'caption-mode').read_text() if (root / 'caption-mode').exists() else 'good'
if mode == 'hold':
    import time
    (root / 'caption-held').write_text(str(os.getpid()))
    time.sleep(30)
if mode in {'network','user','unknown'}:
    print(json.dumps({'event':'error', 'kind':mode}), flush=True)
else:
    print(json.dumps({'event':'result','result': {'video_id':'wrong-video' if mode=='mismatch' else video_id,
        'title':'Content session sample', 'language':'en', 'is_auto':False,
        'srt_text':'' if mode=='empty' else f'1\n00:00:00,000 --> 00:00:02,000\n{source}\n'}}), flush=True)
