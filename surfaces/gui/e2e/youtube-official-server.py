"""Real Web host, loops and file vault; only Google transport is replaced."""
# ruff: noqa: E402 -- isolated environment must precede host imports.
import json
import os
import socket
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
os.environ.update(COWORKER_STATE_DIR=str(root / 'host'), EDISON_CREDENTIAL_FILE=str(root / 'credentials.json'),
                  YOUTUBE_WORKBENCH_WORKSPACE=str(root / 'library'), COWORKER_API_TOKEN='official-e2e')

import uvicorn

from coworker.server import SessionManager, create_app
from youtube_strataread.workbench.connection import (
    GoogleOAuthGateway,
    OAuthCredentials,
    SubscriptionSource,
)
from youtube_strataread.workbench.youtube_api import YouTubeAPIError


def refresh(self, configuration, refresh_token):
    assert configuration.client_id == 'fixture-client' and refresh_token == 'fixture-refresh'
    with (root / 'requests').open('a') as record:
        record.write('refresh\n')
    return OAuthCredentials('fixture-access', None, time.time() + 3600)


def subscriptions(self, credentials, **kwargs):
    assert credentials.access_token == 'fixture-access'
    if (root / 'quota').exists():
        raise YouTubeAPIError('quota', time.time() + 3600)
    with (root / 'requests').open('a') as record:
        record.write('subscriptions\n')
    return [SubscriptionSource('fixture-channel', 'Fixture Channel')]


GoogleOAuthGateway.refresh = refresh
GoogleOAuthGateway.list_subscriptions = subscriptions
app = create_app(SessionManager(data_dir=root / 'host'))
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
print(json.dumps({'port': sock.getsockname()[1]}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
