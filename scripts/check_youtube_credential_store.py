#!/usr/bin/env python3
"""Verify synthetic credentials under the actual nonroot invoking identity.

Creates a NEW directory only. Never inspects configured/production credentials.
Run with PYTHONPATH=src using the target service UID and intended filesystem.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from youtube_strataread.workbench.keychain import NativeKeychainVault, ServiceAccountFile

REFRESH = 'YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN'
ACCESS = 'YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--phase', choices=['write', 'rotate', 'read'])
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit('Run as the actual nonprivileged service identity.')
    root = args.directory.resolve()
    path = root / 'credentials.json'
    if args.phase:
        vault = NativeKeychainVault(namespace=str(root / 'library'), backend=ServiceAccountFile(path))
        if args.phase == 'write':
            vault.save_many({REFRESH: 'synthetic-original', ACCESS: 'synthetic-access'})
        elif args.phase == 'rotate':
            assert vault.load(REFRESH) == 'synthetic-original'
            vault.save_many({REFRESH: 'synthetic-rotated'})
            os._exit(0)  # abrupt process exit after durable replacement
        else:
            assert vault.load(REFRESH) == 'synthetic-rotated'
        assert vault.load(ACCESS) is None or args.phase == 'write'
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_uid == os.geteuid()
        return
    root.mkdir(mode=0o700)  # refuse reuse of any existing directory
    command = [sys.executable, str(Path(__file__).resolve()), str(root), '--phase']
    for phase in ['write', 'rotate', 'read']:
        subprocess.run(command + [phase], check=True)
    assert not list(root.glob('.edison-credentials-*'))
    print(json.dumps({'uid': os.geteuid(), 'gid': os.getegid(), 'platform': sys.platform,
                      'owner_only': True, 'atomic_replace': True, 'restart_read': True,
                      'abrupt_exit_read': True, 'synthetic_only': True}))


if __name__ == '__main__':
    main()
