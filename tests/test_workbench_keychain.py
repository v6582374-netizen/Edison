from __future__ import annotations

import json

import pytest

from youtube_strataread.workbench.keychain import NativeKeychainVault
from youtube_strataread.workbench.vault import VaultError


class Backend:
    def __init__(self):
        self.values = {}
        self.reads = 0
        self.writes = 0
        self.fail = False

    def get_password(self, service, account):
        self.reads += 1
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        if self.fail:
            raise RuntimeError("locked")
        self.writes += 1
        self.values[service, account] = value


def test_profile_isolation_atomic_write_and_memory_only_access_token():
    backend = Backend()
    vault = NativeKeychainVault(namespace="/release", backend=backend)
    refresh = "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN"
    access = "YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN"
    vault.save_many({refresh: "refresh", access: "access"})
    assert backend.writes == 1
    for _ in range(5):
        assert vault.load(access) == "access"
        assert vault.load(refresh) == "refresh"
    assert backend.reads == 1
    stored = json.loads(next(iter(backend.values.values())))
    assert stored == {refresh: "refresh"}
    restart = NativeKeychainVault(namespace="/release", backend=backend)
    assert restart.load(access) is None
    assert restart.load(refresh) == "refresh"
    assert NativeKeychainVault(namespace="/development", backend=backend).keys() == set()
    vault.save_many({refresh: "", access: ""})
    assert NativeKeychainVault(namespace="/release", backend=backend).keys() == set()


def test_failed_keychain_write_does_not_replace_working_credentials():
    backend = Backend()
    vault = NativeKeychainVault(namespace="/release", backend=backend)
    vault.save("refresh", "old")
    backend.fail = True
    with pytest.raises(VaultError):
        vault.save("refresh", "new")
    assert vault.load("refresh") == "old"


def test_locked_keychain_fails_closed_without_caching_a_missing_record():
    class Locked(Backend):
        def get_password(self, service, account):
            raise RuntimeError("locked")

    vault = NativeKeychainVault(namespace="/release", backend=Locked())
    with pytest.raises(VaultError):
        vault.load("refresh")
    assert vault._values is None


def test_service_account_file_is_opt_in_owner_only_and_survives_restart(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    monkeypatch.setenv("EDISON_CREDENTIAL_FILE", str(path))
    refresh = "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN"
    NativeKeychainVault(namespace="/srv").save_many(
        {refresh: "refresh", "YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN": "access"}
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert "access" not in path.read_text()
    assert NativeKeychainVault(namespace="/srv").load(refresh) == "refresh"
    assert NativeKeychainVault(namespace="/other").keys() == set()


def test_service_account_file_rejects_foreign_readable_or_symlinked_credentials(tmp_path):
    from youtube_strataread.workbench.keychain import ServiceAccountFile
    path = tmp_path / 'credentials.json'
    store = ServiceAccountFile(path)
    store.set_password('service', 'account', 'original')
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        store.get_password('service', 'account')
    path.chmod(0o600)
    alias = tmp_path / 'alias.json'
    alias.symlink_to(path)
    with pytest.raises(PermissionError):
        ServiceAccountFile(alias).set_password('service', 'account', 'replacement')
    assert store.get_password('service', 'account') == 'original'
