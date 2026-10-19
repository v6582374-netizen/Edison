from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from youtube_strataread.workbench.connection import (
    ConnectionService,
    OAuthClientConfiguration,
    OAuthCredentials,
    SubscriptionSource,
)
from youtube_strataread.workbench.workspace import LocalWorkspace


@dataclass
class MemoryVault:
    values: dict[str, str]

    def save_many(self, values: dict[str, str]) -> None:
        self.values.update(values)

    def keys(self) -> set[str]:
        return {key for key, value in self.values.items() if value}

    def save(self, key: str, value: str) -> None:
        self.values[key] = value

    def load(self, key: str) -> str | None:
        return self.values.get(key)


@dataclass
class FakeGoogleOAuth:
    subscriptions: list[SubscriptionSource]

    def authorize(self, configuration: OAuthClientConfiguration) -> OAuthCredentials:
        assert configuration.client_id == "desktop-client"
        return OAuthCredentials(access_token="short-lived", refresh_token="long-lived")

    def list_subscriptions(self, credentials: OAuthCredentials) -> list[SubscriptionSource]:
        assert credentials.access_token in {"short-lived", "refreshed-token"}
        return self.subscriptions


def test_connection_configuration_and_authorization_imports_subscription_sources(
    tmp_path: Path,
) -> None:
    workspace = LocalWorkspace.open(tmp_path / "workspace")
    vault = MemoryVault(values={})
    service = ConnectionService(
        workspace=workspace,
        vault=vault,
        oauth=FakeGoogleOAuth(
            subscriptions=[
                SubscriptionSource(channel_id="alpha", title="Alpha"),
                SubscriptionSource(channel_id="beta", title="Beta"),
            ]
        ),
    )

    configured = service.configure(client_id="desktop-client", client_secret="not-a-real-secret")
    connected = service.authorize_and_import()
    disconnected = service.disconnect()

    assert {key: value for key, value in configured.as_result().items() if key != "subscription_capability"} == {
        "configured": True,
        "authorized": False,
        "subscription_count": 0,
    }
    assert {key: value for key, value in connected.as_result().items() if key != "subscription_capability"} == {
        "configured": True,
        "authorized": True,
        "subscription_count": 2,
    }
    assert {key: value for key, value in disconnected.as_result().items() if key != "subscription_capability"} == {
        "configured": True,
        "authorized": False,
        "subscription_count": 2,
    }
    assert [source["title"] for source in workspace.subscription_sources()] == ["Alpha", "Beta"]
    assert "not-a-real-secret" not in str(connected.as_result())


def test_expired_credentials_refresh_before_subscription_import(tmp_path: Path) -> None:
    workspace = LocalWorkspace.open(tmp_path / "workspace")
    vault = MemoryVault(
        values={
            "YOUTUBE_WORKBENCH_OAUTH_CLIENT_ID": "desktop-client",
            "YOUTUBE_WORKBENCH_OAUTH_CLIENT_SECRET": "not-a-real-secret",
            "YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN": "expired-token",
            "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN": "refresh-token",
            "YOUTUBE_WORKBENCH_OAUTH_EXPIRES_AT": "0",
        }
    )
    oauth = RefreshingGoogleOAuth(
        subscriptions=[SubscriptionSource(channel_id="alpha", title="Alpha")]
    )
    service = ConnectionService(workspace=workspace, vault=vault, oauth=oauth)

    status = service.refresh_subscription_sources()

    assert {key: value for key, value in status.as_result().items() if key != "subscription_capability"} == {
        "configured": True,
        "authorized": True,
        "subscription_count": 1,
    }
    assert oauth.refreshed is True
    assert vault.values["YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN"] == "refreshed-token"


@dataclass
class RefreshingGoogleOAuth(FakeGoogleOAuth):
    refreshed: bool = False

    def refresh(
        self,
        configuration: OAuthClientConfiguration,
        refresh_token: str,
    ) -> OAuthCredentials:
        assert configuration.client_id == "desktop-client"
        assert refresh_token == "refresh-token"
        self.refreshed = True
        return OAuthCredentials(
            access_token="refreshed-token",
            refresh_token=refresh_token,
            expires_at=2_000_000_000,
        )


@dataclass
class BrokenVault:
    def keys(self) -> set[str]:
        from youtube_strataread.workbench.vault import VaultError

        raise VaultError("vault unavailable")

    def save(self, key: str, value: str) -> None:
        raise AssertionError("save should not be called")

    def load(self, key: str) -> str | None:
        from youtube_strataread.workbench.vault import VaultError

        raise VaultError("vault unavailable")


def test_vault_unavailability_becomes_a_safe_connection_error(tmp_path: Path) -> None:
    from youtube_strataread.workbench.connection import ConnectionError

    service = ConnectionService(
        workspace=LocalWorkspace.open(tmp_path / "workspace"),
        vault=BrokenVault(),
        oauth=FakeGoogleOAuth(subscriptions=[]),
    )

    assert service.status().authorized is False  # Navigation never opens the credential store.
    with pytest.raises(ConnectionError, match="credential storage is unavailable"):
        service.refresh_subscription_sources()


def test_invalid_youtube_response_shape_becomes_a_safe_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from io import BytesIO

    from youtube_strataread.workbench.connection import ConnectionError, GoogleOAuthGateway

    class InvalidResponse:
        def __enter__(self) -> BytesIO:
            return BytesIO(b'{"items": null}')

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
            return False

    monkeypatch.setattr(
        "youtube_strataread.workbench.youtube_api.urlopen",
        lambda *args, **kwargs: InvalidResponse(),
    )

    with pytest.raises(ConnectionError, match="官方 API"):
        GoogleOAuthGateway().list_subscriptions(
            OAuthCredentials(access_token="not-a-real-token", refresh_token=None)
        )


def test_repeated_subscription_page_token_becomes_a_safe_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from io import BytesIO

    from youtube_strataread.workbench.connection import ConnectionError, GoogleOAuthGateway

    payloads = iter(
        [b'{"items": [], "nextPageToken": "again"}', b'{"items": [], "nextPageToken": "again"}']
    )

    class RepeatedResponse:
        def __enter__(self) -> BytesIO:
            return BytesIO(next(payloads))

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
            return False

    monkeypatch.setattr(
        "youtube_strataread.workbench.youtube_api.urlopen",
        lambda *args, **kwargs: RepeatedResponse(),
    )

    with pytest.raises(ConnectionError, match="repeated a page token"):
        GoogleOAuthGateway().list_subscriptions(
            OAuthCredentials(access_token="not-a-real-token", refresh_token=None)
        )


def test_duplicate_subscription_channel_ids_are_imported_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from io import BytesIO

    from youtube_strataread.workbench.connection import GoogleOAuthGateway

    payload = b'{"items": [{"snippet": {"resourceId": {"channelId": "alpha"}, "title": "Alpha"}}, {"snippet": {"resourceId": {"channelId": "alpha"}, "title": "Alpha duplicate"}}]}'

    class DuplicateResponse:
        def __enter__(self) -> BytesIO:
            return BytesIO(payload)

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
            return False

    monkeypatch.setattr(
        "youtube_strataread.workbench.youtube_api.urlopen",
        lambda *args, **kwargs: DuplicateResponse(),
    )

    sources = GoogleOAuthGateway().list_subscriptions(
        OAuthCredentials(access_token="not-a-real-token", refresh_token=None)
    )

    assert [source.channel_id for source in sources] == ["alpha"]


def test_refresh_token_without_access_token_recovers_subscription_import(tmp_path: Path) -> None:
    workspace = LocalWorkspace.open(tmp_path / "workspace")
    vault = MemoryVault(
        values={
            "YOUTUBE_WORKBENCH_OAUTH_CLIENT_ID": "desktop-client",
            "YOUTUBE_WORKBENCH_OAUTH_CLIENT_SECRET": "not-a-real-secret",
            "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN": "refresh-token",
        }
    )
    oauth = RefreshingGoogleOAuth(
        subscriptions=[SubscriptionSource(channel_id="alpha", title="Alpha")]
    )

    status = ConnectionService(
        workspace=workspace, vault=vault, oauth=oauth
    ).refresh_subscription_sources()

    assert status.authorized is True
    assert oauth.refreshed is True


def test_saving_client_does_not_read_secrets_back_for_confirmation(tmp_path):
    class WriteOnlyVault(MemoryVault):
        def load(self, key):
            raise AssertionError("Saving must not prompt for a second secret read")

    vault = WriteOnlyVault(values={})
    service = ConnectionService(
        workspace=LocalWorkspace.open(tmp_path),
        vault=vault,
        oauth=FakeGoogleOAuth(subscriptions=[]),
    )
    assert service.configure(client_id="fixture", client_secret="fixture").configured


def test_status_never_reads_credentials_even_after_restart(tmp_path):
    workspace = LocalWorkspace.open(tmp_path)
    workspace.set_meta("youtube_oauth_configured", "1")
    workspace.set_meta("youtube_oauth_authorized", "1")
    service = ConnectionService(workspace=workspace, vault=BrokenVault(), oauth=FakeGoogleOAuth([]))
    for _ in range(3):
        assert service.status().configured
        assert service.status().authorized


def test_unfollow_cancels_only_unstarted_work_and_rejects_stale_feed_results(tmp_path):
    from youtube_strataread.workbench.discovery import Candidate

    workspace = LocalWorkspace.open(tmp_path)
    workspace.replace_subscription_sources(
        [SubscriptionSource("a", "A"), SubscriptionSource("b", "B")]
    )
    for key in ["queued", "active", "saved"]:
        workspace.add_candidate(
            Candidate(key, "b", "B", key, "https://youtube.com/watch?v=" + key, "2026-09-20", 1)
        )
    workspace.save_manuscript("saved", "# Saved", generator="fixture", transcript_characters=0)
    workspace.set_preparation_state("saved", "ready")
    workspace.set_preparation_state("active", "generating")
    workspace.replace_subscription_sources([SubscriptionSource("a", "A")])
    assert workspace.asset("queued")["preparation_state"] == "cancelled"
    assert workspace.asset("active")["preparation_state"] == "generating"
    assert workspace.asset("saved")["preparation_state"] == "ready"
    assert not workspace.add_candidate(
        Candidate("late", "b", "B", "Late", "https://youtube.com/watch?v=late", "2026-09-20", 1)
    )
    workspace.replace_subscription_sources([SubscriptionSource("b", "B")])
    assert workspace.add_candidate(
        Candidate("new", "b", "B", "New", "https://youtube.com/watch?v=new", "2026-09-20", 1)
    )


def test_sync_preserves_membership_on_incomplete_pages_and_transient_failure(tmp_path):
    from youtube_strataread.workbench.connection import ConnectionError

    workspace = LocalWorkspace.open(tmp_path)
    workspace.replace_subscription_sources([SubscriptionSource("keep", "Keep")])
    workspace.set_meta("youtube_oauth_authorized", "1")
    workspace.set_meta("youtube_subscriptions_synced_at", "42")
    vault = MemoryVault(
        {
            "YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN": "token",
            "YOUTUBE_WORKBENCH_OAUTH_EXPIRES_AT": "9999999999",
        }
    )

    class Offline:
        def list_subscriptions(self, credentials):
            raise ConnectionError("temporary network failure")

    service = ConnectionService(workspace=workspace, vault=vault, oauth=Offline())
    with pytest.raises(ConnectionError):
        service.refresh_subscription_sources()
    assert workspace.subscription_source_count() == 1
    assert service.status().authorized
    assert workspace.meta("youtube_subscriptions_synced_at") == "42"


def test_server_rejected_access_token_refreshes_once_then_requires_login(tmp_path):
    from youtube_strataread.workbench.connection import AuthorizationRequired

    workspace = LocalWorkspace.open(tmp_path)
    vault = MemoryVault(
        {
            "YOUTUBE_WORKBENCH_OAUTH_CLIENT_ID": "desktop-client",
            "YOUTUBE_WORKBENCH_OAUTH_CLIENT_SECRET": "client-secret",
            "YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN": "stale",
            "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN": "refresh-token",
            "YOUTUBE_WORKBENCH_OAUTH_EXPIRES_AT": "9999999999",
        }
    )

    class Google(RefreshingGoogleOAuth):
        calls = 0

        def list_subscriptions(self, credentials):
            self.calls += 1
            if self.calls == 1:
                raise AuthorizationRequired("expired")
            return [SubscriptionSource("a", "A")]

    google = Google([])
    service = ConnectionService(workspace=workspace, vault=vault, oauth=google)
    assert service.refresh_subscription_sources().authorized
    assert google.calls == 2 and google.refreshed

    class Revoked(Google):
        def list_subscriptions(self, credentials):
            raise AuthorizationRequired("revoked")

    service.oauth = Revoked([])
    with pytest.raises(AuthorizationRequired):
        service.refresh_subscription_sources()
    assert not service.status().authorized
    assert workspace.meta("youtube_reconnect_required") == "1"
    assert workspace.subscription_source_count() == 1


def test_failed_second_google_page_never_commits_a_partial_membership(tmp_path, monkeypatch):
    from io import BytesIO

    from youtube_strataread.workbench.connection import ConnectionError, GoogleOAuthGateway

    workspace = LocalWorkspace.open(tmp_path)
    workspace.replace_subscription_sources([SubscriptionSource("keep", "Keep")])
    pages = iter(
        [
            b'{"items":[{"snippet":{"resourceId":{"channelId":"new"}}}],"nextPageToken":"second"}',
            None,
        ]
    )

    def request(*args, **kwargs):
        payload = next(pages)
        if payload is None:
            raise OSError("offline")
        return BytesIO(payload)

    monkeypatch.setattr("youtube_strataread.workbench.youtube_api.urlopen", request)
    vault = MemoryVault(
        {
            "YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN": "token",
            "YOUTUBE_WORKBENCH_OAUTH_EXPIRES_AT": "9999999999",
        }
    )
    service = ConnectionService(workspace=workspace, vault=vault, oauth=GoogleOAuthGateway())
    with pytest.raises(ConnectionError):
        service.refresh_subscription_sources()
    assert [row["channel_id"] for row in workspace.subscription_sources()] == ["keep"]


def test_developer_client_enables_sign_in_without_reading_keychain(tmp_path, monkeypatch):
    monkeypatch.setenv("EDISON_GOOGLE_OAUTH_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("EDISON_GOOGLE_OAUTH_CLIENT_SECRET", "fixture-secret")
    service = ConnectionService(
        workspace=LocalWorkspace.open(tmp_path), vault=BrokenVault(), oauth=FakeGoogleOAuth([])
    )
    assert service.status().configured and not service.status().authorized


@pytest.mark.parametrize(
    "reason,requires_login", [("invalid_grant", True), ("temporarily_unavailable", False)]
)
def test_token_endpoint_distinguishes_revocation_from_transient_errors(
    monkeypatch, reason, requires_login
):
    import json
    from io import BytesIO
    from urllib.error import HTTPError

    from youtube_strataread.workbench.connection import (
        AuthorizationRequired,
        ConnectionError,
        GoogleOAuthGateway,
    )

    def request(*args, **kwargs):
        raise HTTPError(
            "https://oauth2.googleapis.com/token",
            400,
            "fixture",
            {},
            BytesIO(json.dumps({"error": reason}).encode()),
        )

    monkeypatch.setattr("youtube_strataread.workbench.connection.urlopen", request)
    with pytest.raises(ConnectionError) as error:
        GoogleOAuthGateway().refresh(OAuthClientConfiguration("fixture", "fixture"), "fixture")
    assert isinstance(error.value, AuthorizationRequired) == requires_login
