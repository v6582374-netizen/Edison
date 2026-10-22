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
cookies = path.read_text()
assert 'fixture-session' in cookies
session = next(field for field in cookies.split() if field.startswith('fixture-session'))
with (root / 'caption-requests.jsonl').open('a') as log:
    log.write(json.dumps({'video_id':video_id, 'pid':os.getpid(), 'path':str(path), 'session':session})+'\n')
def current_mode():
    return (root / 'caption-mode').read_text() if (root / 'caption-mode').exists() else 'good'
# Simulates yt-dlp's CookieJar.save on close: only its task copy is changed.
if current_mode() == 'rotate':
    path.write_text(cookies.replace(session, 'rotated-by-response'))
else:
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
refused = (root / 'refused-sessions').read_text().split() if (root / 'refused-sessions').exists() else []
mode = current_mode()
if mode == 'hold':
    import time
    (root / 'caption-held').write_text(str(os.getpid()))
    deadline = time.monotonic() + 30
    while not (root / 'caption-release').exists() and time.monotonic() < deadline:
        time.sleep(.02)
if session in refused or mode == 'user':
    print(json.dumps({'event':'error', 'kind':'user'}), flush=True)
elif mode in {'network','unknown','maintenance','no_captions'}:
    print(json.dumps({'event':'error', 'kind':mode}), flush=True)
elif mode == 'rate_limit':
    print(json.dumps({'event':'error', 'kind':'rate_limit', 'retry_at':None}), flush=True)
else:
    print(json.dumps({'event':'result','result': {'video_id':'wrong-video' if mode=='mismatch' else video_id,
        'title':'Content session sample', 'language':'en', 'is_auto':False,
        'srt_text':'' if mode=='empty' else f'1\n00:00:00,000 --> 00:00:02,000\n{source}\n'}}), flush=True)
