"""User-owned Google OAuth and YouTube subscription-source behavior."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from youtube_strataread.workbench.cutover import admitted
from youtube_strataread.workbench.vault import SecretVault, VaultError
from youtube_strataread.workbench.workspace import LocalWorkspace
from youtube_strataread.workbench.youtube_api import (
    SUBSCRIPTION_SYNC_SECONDS,
    AuthorizationRequired,
    ConnectionError,
    YouTubeDataAPI,
)

_CLIENT_ID = "YOUTUBE_WORKBENCH_OAUTH_CLIENT_ID"
_CLIENT_SECRET = "YOUTUBE_WORKBENCH_OAUTH_CLIENT_SECRET"
_ACCESS_TOKEN = "YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN"
_REFRESH_TOKEN = "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN"
_EXPIRES_AT = "YOUTUBE_WORKBENCH_OAUTH_EXPIRES_AT"
_YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class OAuthClientConfiguration:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class OAuthCredentials:
    access_token: str
    refresh_token: str | None
    expires_at: float | None = None


@dataclass(frozen=True)
class SubscriptionSource:
    channel_id: str
    title: str
    description: str = ""
    thumbnail_url: str | None = None
    subscribed_at: str | None = None


@dataclass(frozen=True)
class ConnectionStatus:
    configured: bool
    authorized: bool
    subscription_count: int
    subscription_capability: dict[str, object] = field(default_factory=dict)
    disconnection: dict[str, str] = field(default_factory=dict)

    def as_result(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "authorized": self.authorized,
            "subscription_count": self.subscription_count,
            "subscription_capability": self.subscription_capability,
            **({"disconnection": self.disconnection} if any(self.disconnection.values()) else {}),
        }


class GoogleOAuth(Protocol):
    def revoke(self, token: str) -> None: ...

    def authorize(self, configuration: OAuthClientConfiguration) -> OAuthCredentials: ...

    def refresh(
        self,
        configuration: OAuthClientConfiguration,
        refresh_token: str,
    ) -> OAuthCredentials: ...

    def list_subscriptions(self, credentials: OAuthCredentials, *, grant_check: bool = False, before_request: Callable[[], None] | None = None) -> list[SubscriptionSource]: ...


class OfficialConnectionError(ConnectionError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class GoogleOAuthGateway:
    """Small standard-library client for a user-owned Google desktop OAuth client."""

    def __init__(self) -> None:
        self.api = YouTubeDataAPI()

    def authorize(self, configuration: OAuthClientConfiguration) -> OAuthCredentials:
        callback = _CallbackServer()
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        try:
            query = urlencode(
                {
                    "client_id": configuration.client_id,
                    "redirect_uri": callback.redirect_uri,
                    "response_type": "code",
                    "scope": _YOUTUBE_SCOPE,
                    "access_type": "offline",
                    "prompt": "consent",
                    "state": state,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
            if not webbrowser.open(f"https://accounts.google.com/o/oauth2/v2/auth?{query}"):
                raise ConnectionError("Could not open a browser for Google authorization")
            code, returned_state, provider_error = callback.wait()
            if provider_error:
                raise ConnectionError("Google authorization was not completed")
            if returned_state != state or not code:
                raise ConnectionError("Google authorization callback could not be verified")
            return self._exchange(
                {
                    "code": code,
                    "client_id": configuration.client_id,
                    "client_secret": configuration.client_secret,
                    "redirect_uri": callback.redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                }
            )
        finally:
            callback.close()

    def refresh(
        self,
        configuration: OAuthClientConfiguration,
        refresh_token: str,
    ) -> OAuthCredentials:
        refreshed = self._exchange(
            {
                "client_id": configuration.client_id,
                "client_secret": configuration.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        )
        return OAuthCredentials(
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or refresh_token,
            expires_at=refreshed.expires_at,
        )

    def list_subscriptions(self, credentials: OAuthCredentials, *, grant_check: bool = False, before_request: Callable[[], None] | None = None) -> list[SubscriptionSource]:
        sources: list[SubscriptionSource] = []
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        seen_channel_ids: set[str] = set()
        while True:
            query: dict[str, str] = {
                "part": "snippet",
                "mine": "true",
                "maxResults": "50",
            }
            if page_token:
                if page_token in seen_page_tokens:
                    raise ConnectionError("YouTube subscriptions response repeated a page token")
                seen_page_tokens.add(page_token)
                query["pageToken"] = page_token
            if before_request is not None:
                before_request()
            payload = self.api.get('subscriptions', query, credentials.access_token, grant_check=grant_check)
            if not isinstance(payload, dict):
                raise ConnectionError("YouTube subscriptions response was invalid")
            items = payload.get("items")
            if not isinstance(items, list):
                raise ConnectionError("YouTube subscriptions response was invalid")
            for item in items:
                if not isinstance(item, dict):
                    raise ConnectionError("YouTube subscriptions response was invalid")
                snippet = item.get("snippet", {})
                if not isinstance(snippet, dict):
                    raise ConnectionError("YouTube subscriptions response was invalid")
                resource = snippet.get("resourceId", {})
                if not isinstance(resource, dict):
                    raise ConnectionError("YouTube subscriptions response was invalid")
                channel_value = resource.get("channelId")
                if not isinstance(channel_value, str) or not channel_value.strip():
                    raise ConnectionError("YouTube subscriptions response was invalid")
                channel_id = channel_value.strip()
                if channel_id in seen_channel_ids:
                    continue
                seen_channel_ids.add(channel_id)
                thumbnails = snippet.get("thumbnails", {})
                default_thumbnail = (
                    thumbnails.get("default", {}) if isinstance(thumbnails, dict) else {}
                )
                sources.append(
                    SubscriptionSource(
                        channel_id=channel_id,
                        title=str(snippet.get("title") or channel_id),
                        description=str(snippet.get("description") or ""),
                        thumbnail_url=(
                            str(default_thumbnail.get("url") or "")
                            if isinstance(default_thumbnail, dict)
                            else ""
                        )
                        or None,
                        subscribed_at=str(snippet.get("publishedAt") or "") or None,
                    )
                )
            page_token = str(payload.get("nextPageToken") or "") or None
            if not page_token:
                return sources

    def revoke(self, token: str) -> None:
        # Google's revoke endpoint succeeds with an empty body; never log tokens
        # or propagate a provider response containing authentication material.
        request = Request("https://oauth2.googleapis.com/revoke", data=urlencode({"token": token}).encode(),
                          headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise ConnectionError("Google platform revocation failed")
        except Exception:
            raise ConnectionError("Google platform revocation failed") from None

    def _exchange(self, values: dict[str, str]) -> OAuthCredentials:
        response = _post_form(_TOKEN_ENDPOINT, values)
        access_token = str(response.get("access_token") or "")
        if not access_token:
            raise ConnectionError("Google authorization did not return an access token")
        expires_in = response.get("expires_in")
        expires_at = (
            time.time() + float(expires_in) if isinstance(expires_in, int | float) else None
        )
        return OAuthCredentials(
            access_token=access_token,
            refresh_token=str(response.get("refresh_token") or "") or None,
            expires_at=expires_at,
        )


class ConnectionService:
    """Owns connection state while keeping credential values outside workspace data."""

    def __init__(
        self, *, workspace: LocalWorkspace, vault: SecretVault, oauth: GoogleOAuth
    ) -> None:
        self.workspace = workspace
        self.vault = vault
        self.oauth = oauth
        self.api = YouTubeDataAPI(workspace)
        if isinstance(oauth, GoogleOAuthGateway):
            oauth.api = self.api
        self._lock = threading.RLock()
        self._identity_lock = threading.RLock()
        self._generation = 0
        self._credentials: OAuthCredentials | None = None
        self._sync_finished = 0.0
        self._application_client = application_client()
        self._owner_handle = None
        self._server_retired = False
        self._verified_this_process = False

    def claim_server_ownership(self) -> None:
        """Own the canonical namespace for this Web or independent CLI lifetime."""
        from youtube_strataread.utils.process_lock import acquire_process_lock

        with self._identity_lock:
            if self._server_retired:
                raise OfficialConnectionError("storage", "This credential owner has retired; create a new connection")
            if self._owner_handle is not None:
                return
            try:
                self._owner_handle = acquire_process_lock(self.workspace.root / ".youtube-official-owner.lock")
            except OSError:
                raise OfficialConnectionError("storage", "Another process owns this library, or the service account cannot acquire its owner lease") from None

    def release_server_ownership(self) -> None:
        with self._identity_lock:
            self._server_retired = True
            if self._owner_handle is not None:
                self._owner_handle.close()
                self._owner_handle = None

    def _require_active_owner(self) -> None:
        if self._server_retired:
            raise OfficialConnectionError("storage", "This server's credential owner has retired; refresh the page")
        # Status reads stay available without claiming ownership. Every operation
        # that can write or refresh credentials must first join the same lease.
        self.claim_server_ownership()

    @contextmanager
    def _operation(self):
        with admitted(self.workspace):
            # Capture intent before joining serialized network work. Disconnect does
            # not wait for that work; every publication checks this generation.
            with self._identity_lock:
                if self.workspace.meta("youtube_disconnect_local") in {"pending", "failed"}:
                    raise OfficialConnectionError("disconnect_pending", "凭据清理尚未完成，请再次断开后再重新连接。")
                if self.workspace.meta("youtube_disconnect_google") == "pending":
                    raise OfficialConnectionError("disconnect_pending", "正在撤销 Google 授权，请等待断开完成后再重新连接。")
                generation = self._generation
            with self._lock:
                self._require_active_owner()
                with self._publication(generation):
                    pass
                yield generation

    @contextmanager
    def _publication(self, generation):
        with self._identity_lock:
            if generation != self._generation or self._server_retired:
                raise OfficialConnectionError("invalidated", "YouTube 已断开或连接已更新；旧请求结果已丢弃。")
            yield

    def watch_subscriptions(self, stop: threading.Event, *, now: Callable[[], float] = time.time,
                            wake: threading.Event | None = None) -> None:
        """Low-frequency membership sync, independent of discovery and preparation."""
        due = 0.0
        while not stop.wait(1):
            if now() < due or not self.status().authorized or self.workspace.meta('drain_paused') == '1':
                continue
            try:
                last = float(self.workspace.meta('youtube_subscriptions_synced_at') or 0)
                if self._verified_this_process and now() - last < SUBSCRIPTION_SYNC_SECONDS:
                    due = last + SUBSCRIPTION_SYNC_SECONDS
                    continue
                self.refresh_subscription_sources()
                if wake:
                    wake.set()
                due = now() + SUBSCRIPTION_SYNC_SECONDS
            except ConnectionError:
                due = now() + 300

    def status(self) -> ConnectionStatus:
        # Navigation reads only local, non-secret state. It never opens Keychain,
        # Vault or a browser, even after the application has restarted.
        return ConnectionStatus(
            configured=bool(self._application_client)
            or self.workspace.meta("youtube_oauth_configured") == "1",
            authorized=self.workspace.meta("youtube_oauth_authorized") == "1",
            subscription_count=self.workspace.subscription_source_count(),
            subscription_capability=self.subscription_capability(),
            disconnection={"local": self.workspace.meta("youtube_disconnect_local") or "",
                           "google": self.workspace.meta("youtube_disconnect_google") or ""},
        )

    def subscription_capability(self) -> dict[str, object]:
        last_success = _float_or_none(self.workspace.meta("youtube_subscriptions_synced_at"))
        last_failure = _float_or_none(self.workspace.meta("youtube_subscriptions_failed_at"))
        kind = self.workspace.meta("youtube_subscriptions_failure_kind") or ""
        error = self.workspace.meta("youtube_subscriptions_error") or ""
        available = self._verified_this_process and not error and self.workspace.meta("youtube_oauth_authorized") == "1"
        return {"state": "available" if available else "interrupted" if error else "unverified",
                "last_success_at": last_success, "last_failure_at": last_failure,
                "failure_kind": kind or None, "reason": error or None,
                "last_failure_kind": self.workspace.meta("youtube_subscriptions_last_failure_kind") or None,
                "last_failure_reason": self.workspace.meta("youtube_subscriptions_last_failure_reason") or None}

    def _failed(self, error: Exception, generation: int) -> None:
        with self._publication(generation):
            self.workspace.set_meta("youtube_subscriptions_failed_at", str(self.workspace.now()))
            kind = getattr(error, "kind", "reauthorization" if isinstance(error, AuthorizationRequired) else "service")
            self.workspace.set_meta("youtube_subscriptions_failure_kind", kind)
            self.workspace.set_meta("youtube_subscriptions_last_failure_kind", kind)
            self.workspace.set_meta("youtube_subscriptions_last_failure_reason", str(error))
            self.workspace.set_meta("youtube_subscriptions_error", str(error))

    def import_authorization(self, *, client_id: str, client_secret: str, refresh_token: str) -> ConnectionStatus:
        """Explicit trusted-device handoff; persist the grant with its matching client."""
        if not all(isinstance(value, str) and value.strip() for value in (client_id, client_secret, refresh_token)):
            raise ConnectionError("Trusted-device authorization requires its matching client and refresh token")
        with self._operation() as generation:
            self._require_active_owner()
            had_authorization = self.status().authorized
            try:
                # A new grant may prove authorization, but never bypass quota or Retry-After.
                self.api.ensure_available(grant_check=True)
                configuration = OAuthClientConfiguration(client_id, client_secret)
                credentials = self.oauth.refresh(configuration, refresh_token)
                credentials = OAuthCredentials(credentials.access_token, credentials.refresh_token or refresh_token, credentials.expires_at)
                # Do not continue an old handoff after a concurrent withdrawal.
                with self._publication(generation):
                    pass
                # Prove the supplied client/grant pairing before replacing a working connection.
                sources = self._list_subscriptions(credentials, generation, grant_check=True)
                with self._publication(generation):
                    self.vault.save_many({_CLIENT_ID: client_id, _CLIENT_SECRET: client_secret,
                        _ACCESS_TOKEN: credentials.access_token, _REFRESH_TOKEN: credentials.refresh_token or "",
                        _EXPIRES_AT: str(credentials.expires_at or "")})
                    self._credentials = credentials
                    self.workspace.replace_subscription_sources(sources)
                    self._connected(True)
                    self._subscription_success()
                return self.status()
            except VaultError:
                error = OfficialConnectionError("storage", "Server credential storage could not save the connection; repair service-account access")
                if not had_authorization:
                    self._failed(error, generation)
                raise error from None
            except ConnectionError as error:
                if not had_authorization:
                    self._failed(error, generation)
                raise

    def _subscription_success(self) -> None:
        self.workspace.set_meta("youtube_subscriptions_synced_at", str(self.workspace.now()))
        self.workspace.set_meta("youtube_subscriptions_error", "")
        self.workspace.set_meta("youtube_subscriptions_failure_kind", "")
        self._verified_this_process = True

    def _connected(self, authorized: bool) -> None:
        if authorized:
            self.workspace.set_meta("youtube_disconnect_local", "")
            self.workspace.set_meta("youtube_disconnect_google", "")
            self.api.authorized()
        self.workspace.set_meta("youtube_oauth_configured", "1")
        self.workspace.set_meta("youtube_oauth_authorized", "1" if authorized else "0")
        self.workspace.set_meta("youtube_reconnect_required", "0")

    def configure(self, *, client_id: str, client_secret: str) -> ConnectionStatus:
        if not client_id.strip() or not client_secret.strip():
            raise ConnectionError("Google OAuth client ID and secret are required")
        with self._operation() as generation:
            self._require_active_owner()
            try:
                with self._publication(generation):
                    self.vault.save_many(
                        {
                            _CLIENT_ID: client_id.strip(),
                            _CLIENT_SECRET: client_secret.strip(),
                            _ACCESS_TOKEN: "",
                            _REFRESH_TOKEN: "",
                            _EXPIRES_AT: "",
                        }
                    )
            except VaultError as error:
                raise OfficialConnectionError("storage", "Server credential storage is unavailable; repair service-account access") from error
            with self._publication(generation):
                self._credentials = None
                self._connected(False)
            return self.status()

    def migrate_credentials(self) -> ConnectionStatus:
        # This is explicit and idempotent. Normal startup/sync never invokes av.
        with self._operation() as generation:
            self._require_active_owner()
            if self.workspace.meta("youtube_oauth_configured") == "1":
                return self.status()
            from youtube_strataread.workbench.vault import AutomicVault

            try:
                values = AutomicVault(
                    namespace=str(self.workspace.root.resolve())
                ).export_credentials(
                    [_CLIENT_ID, _CLIENT_SECRET, _ACCESS_TOKEN, _REFRESH_TOKEN, _EXPIRES_AT]
                )
                if not values.get(_CLIENT_ID) or not values.get(_CLIENT_SECRET):
                    raise ConnectionError("No existing Google client configuration was found")
                with self._publication(generation):
                    self.vault.save_many(values)
            except VaultError as error:
                raise OfficialConnectionError("storage", "Server credential storage is unavailable; repair service-account access") from error
            with self._publication(generation):
                self._connected(bool(values.get(_ACCESS_TOKEN) or values.get(_REFRESH_TOKEN)))
            return self.status()

    def authorize_and_import(self) -> ConnectionStatus:
        with self._operation() as generation:
            self._require_active_owner()
            credentials = self.oauth.authorize(self._require_configuration())
            self._store_credentials(credentials, generation)
            with self._publication(generation):
                self._connected(True)
            return self.refresh_subscription_sources()

    def refresh_subscription_sources(self) -> ConnectionStatus:
        requested = time.monotonic()
        with self._operation() as generation:
            self._require_active_owner()
            # A refresh queued before an explicit disconnect must not revive it.
            if (
                self.workspace.meta("youtube_oauth_authorized") == "0"
                or self._sync_finished >= requested
            ):
                return self.status()
            try:
                credentials = self._active_credentials(generation)
                try:
                    sources = self._list_subscriptions(credentials, generation)
                except AuthorizationRequired:
                    # A server-rejected access token gets one silent refresh, not
                    # an immediate browser prompt. A second rejection needs login.
                    credentials = self._active_credentials(generation, force_refresh=True)
                    sources = self._list_subscriptions(credentials, generation)
                with self._publication(generation):
                    self.workspace.replace_subscription_sources(sources)
                    self._connected(True)
                    self._subscription_success()
                    self._sync_finished = time.monotonic()
                return self.status()
            except AuthorizationRequired as error:
                with self._publication(generation):
                    self.api.block('authorization')
                    self._credentials = None
                    self.workspace.set_meta("youtube_oauth_authorized", "0")
                    self.workspace.set_meta("youtube_reconnect_required", "1")
                    self._failed(error, generation)
                raise
            except (ConnectionError, VaultError) as error:
                # Transient failures preserve membership and the last good sync.
                self._failed(error, generation)
                raise

    def youtube_get(self, endpoint: str, parameters: dict[str, str]) -> dict:
        """Signed official reads share the existing refresh token and native store."""
        with self._operation() as generation:
            self._require_active_owner()
            self.api.ensure_available()
            if not self.status().authorized:
                raise AuthorizationRequired('YouTube 授权失效，请重新连接。')
            try:
                credentials = self._active_credentials(generation)
                try:
                    result = self.api.get(endpoint, parameters, credentials.access_token)
                    with self._publication(generation):
                        return result
                except AuthorizationRequired:
                    credentials = self._active_credentials(generation, force_refresh=True)
                    result = self.api.get(endpoint, parameters, credentials.access_token)
                    with self._publication(generation):
                        return result
            except AuthorizationRequired as error:
                with self._publication(generation):
                    self._failed(error, generation)
                    self._credentials = None
                    self.workspace.set_meta('youtube_oauth_authorized', '0')
                    self.workspace.set_meta('youtube_reconnect_required', '1')
                    self.api.block('authorization')
                raise
            except ConnectionError as error:
                self._failed(error, generation)
                raise

    def _list_subscriptions(self, credentials, generation, *, grant_check=False):
        def permitted():
            with self._publication(generation):
                pass

        permitted()
        options = {"grant_check": True} if grant_check else {}
        if isinstance(self.oauth, GoogleOAuthGateway):
            # Each page is a new use of the bearer. A fence only after the whole
            # response would still allow late page tokens to spend a withdrawn grant.
            options["before_request"] = permitted
        return self.oauth.list_subscriptions(credentials, **options)

    def subscription_sources(self) -> list[dict[str, str | None]]:
        return self.workspace.subscription_sources()

    def resume_disconnection(self, *, clear_content) -> None:
        """Finish a durably requested local withdrawal before starting host loops.

        A crash during remote revocation leaves an unknown result; startup never
        claims success or retries a grant already removed from this instance.
        """
        if self.workspace.meta("youtube_disconnect_local") in {"pending", "failed"}:
            self.disconnect(clear_content=clear_content, revoke=False)
        if self.workspace.meta("youtube_disconnect_google") == "pending":
            self.workspace.set_meta("youtube_disconnect_google", "unknown")

    def disconnect(self, *, clear_content=None, revoke=True) -> ConnectionStatus:
        with self._identity_lock:
            self._require_active_owner()
            self._generation += 1
            self.workspace.set_meta("youtube_oauth_authorized", "0")
            self.workspace.set_meta("youtube_disconnect_local", "pending")
            token = None
            local = "cleared"
            try:
                token = self.vault.load(_REFRESH_TOKEN) or self.vault.load(_ACCESS_TOKEN)
                self.vault.save_many({key: "" for key in (
                    _CLIENT_ID, _CLIENT_SECRET, _ACCESS_TOKEN, _REFRESH_TOKEN, _EXPIRES_AT)})
            except VaultError:
                local = "failed"
            self._credentials = None
            self._connected(False)
            self.workspace.set_meta("youtube_oauth_configured", "0")
            self._verified_this_process = False
            self.workspace.set_meta("youtube_subscriptions_error", "")
            if clear_content is not None:
                try:
                    clear_content()
                except Exception:
                    local = "failed"
            self.workspace.set_meta("youtube_disconnect_local", local)
            previous_remote = self.workspace.meta("youtube_disconnect_google")
            remote = "pending" if token and revoke else previous_remote or "not_available"
            self.workspace.set_meta("youtube_disconnect_google", remote)
        if token and revoke:
            try:
                self.oauth.revoke(token)
                remote = "revoked"
            except Exception:
                remote = "failed"
            with self._identity_lock:
                # Repeated withdrawal may advance the local generation, but
                # must not lose this still-pending platform result. New grants
                # are blocked until this revocation has completed.
                if self.workspace.meta("youtube_disconnect_google") == "pending":
                    self.workspace.set_meta("youtube_disconnect_google", remote)
        return self.status()

    def _active_credentials(self, generation: int, *, force_refresh: bool = False) -> OAuthCredentials:
        with self._publication(generation):
            if self._credentials is None:
                try:
                    self._credentials = OAuthCredentials(
                        access_token=self.vault.load(_ACCESS_TOKEN) or "",
                        refresh_token=self.vault.load(_REFRESH_TOKEN),
                        expires_at=_float_or_none(self.vault.load(_EXPIRES_AT)),
                    )
                except VaultError as error:
                    raise OfficialConnectionError("storage", "Server credential storage is unavailable; repair service-account access") from error
            credentials = self._credentials
        if (
            force_refresh
            or not credentials.access_token
            or (credentials.expires_at is not None and credentials.expires_at <= time.time() + 60)
        ):
            if not credentials.refresh_token:
                raise AuthorizationRequired("YouTube authorization expired; reconnect the account")
            previous = credentials
            credentials = self.oauth.refresh(
                self._require_configuration(), credentials.refresh_token
            )
            # Some providers omit refresh_token on rotation; preserve the current one.
            credentials = OAuthCredentials(
                credentials.access_token,
                credentials.refresh_token or previous.refresh_token,
                credentials.expires_at,
            )
            self._store_credentials(credentials, generation)
            # Acceptance evidence of an ordinary refresh: a time, never a token.
            self.workspace.set_meta("youtube_oauth_refreshed_at", str(self.workspace.now()))
        return credentials

    def _store_credentials(self, credentials: OAuthCredentials, generation: int) -> None:
        with self._publication(generation):
            try:
                self.vault.save_many(
                    {
                        _ACCESS_TOKEN: credentials.access_token,
                        _REFRESH_TOKEN: credentials.refresh_token or "",
                        _EXPIRES_AT: str(credentials.expires_at or ""),
                    }
                )
            except VaultError as error:
                raise OfficialConnectionError("storage", "Server credential storage is unavailable; repair service-account access") from error
            self._credentials = credentials

    def _require_configuration(self) -> OAuthClientConfiguration:
        try:
            client_id = self.vault.load(_CLIENT_ID)
            client_secret = self.vault.load(_CLIENT_SECRET)
        except VaultError as error:
            raise OfficialConnectionError("storage", "Server credential storage is unavailable; repair service-account access") from error
        if client_id and client_secret:
            return OAuthClientConfiguration(client_id, client_secret)
        if self._application_client:
            return self._application_client
        raise OfficialConnectionError("configuration", "No matching Google OAuth client is configured")


def application_client() -> OAuthClientConfiguration | None:
    """Developer-owned installed-app client. Never contains user account tokens."""
    client_id = os.environ.get("EDISON_GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("EDISON_GOOGLE_OAUTH_CLIENT_SECRET")
    if client_id and client_secret:
        return OAuthClientConfiguration(client_id, client_secret)
    try:
        payload = json.loads(
            files("youtube_strataread.workbench").joinpath("google-oauth-client.json").read_text()
        )
        installed = payload.get("installed", payload)
        if installed.get("client_id") and installed.get("client_secret"):
            return OAuthClientConfiguration(installed["client_id"], installed["client_secret"])
    except FileNotFoundError:
        pass
    return None


class _CallbackServer:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._query: dict[str, list[str]] = {}
        server = HTTPServer(("127.0.0.1", 0), self._handler())
        self._server = server
        self.redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth/callback"
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self) -> tuple[str | None, str | None, str | None]:
        if not self._event.wait(timeout=300):
            raise ConnectionError("Google authorization timed out")
        return (
            _first(self._query, "code"),
            _first(self._query, "state"),
            _first(self._query, "error"),
        )

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if urlparse(self.path).path != "/oauth/callback":
                    self.send_error(404)
                    return
                outer._query = parse_qs(urlparse(self.path).query)
                outer._event.set()
                body = "Google authorization received. You can return to Edison."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, format: str, *args: object) -> None:
                return

        return CallbackHandler


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if values else None


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _post_form(url: str, values: dict[str, str]) -> dict[str, object]:
    request = Request(url, data=urlencode(values).encode("utf-8"), method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            payload: object = json.load(response)
    except HTTPError as error:
        try:
            reason = json.load(error).get("error")
        except (ValueError, OSError, AttributeError):
            reason = None
        if reason == "invalid_grant":
            raise AuthorizationRequired(
                "Google rejected this authorization (invalid_grant); authorize again and check the OAuth application configuration. The cause is not a verified security challenge."
            ) from None
        if reason in {"invalid_client", "unauthorized_client", "invalid_scope"}:
            raise OfficialConnectionError("configuration", "Google OAuth client configuration was rejected") from None
        raise OfficialConnectionError("service", "Google token service is unavailable; retry later") from None
    except (OSError, json.JSONDecodeError):
        raise OfficialConnectionError("network", "Google token service could not be reached; retry later") from None
    if not isinstance(payload, dict):
        raise ConnectionError("Google authorization token response was invalid")
    return {str(key): value for key, value in payload.items()}
