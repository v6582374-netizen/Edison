#!/usr/bin/env python3
"""One-time trusted-device Google consent; upload the private output in Edison Web.

Client configuration is read from a Google downloaded installed-app JSON file.
No credentials are printed, accepted in argv, or written to library assets.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from youtube_strataread.workbench.connection import GoogleOAuthGateway, OAuthClientConfiguration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.client_file.read_text())['installed']
    client = OAuthClientConfiguration(config['client_id'], config['client_secret'])
    credentials = GoogleOAuthGateway().authorize(client)
    if not credentials.refresh_token:
        raise SystemExit('Google did not grant offline access; no handoff was saved.')
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump({'client_id': client.client_id, 'client_secret': client.client_secret,
                   'refresh_token': credentials.refresh_token}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    print('Private handoff saved. Upload it in Edison → YouTube → Google authorization; delete it after successful handoff.')


if __name__ == '__main__':
    main()
