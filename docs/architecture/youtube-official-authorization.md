# Server-owned Google authorization

The existing `ConnectionService` is the sole official consumer credential owner. Signed discovery reads and subscription synchronization use its serialized refresh path. The Web host holds a process-lifetime advisory lease under the canonical library directory before recovering interrupted work; a competing host cannot become another writer. Shutdown releases the lease only when all host loops have stopped. The lock file is persistent and must never be unlinked to break a live owner's lease.

The workspace's resolved canonical path remains the OAuth namespace. Do not move the library to a release directory, change its matching client, scan other namespaces, or copy just a refresh token during deployment. `packaging/edison-web.service.example` documents a fixed nonroot identity and stable directories, but is **not** a migration of an existing installation. Existing canonical paths must be retained or explicitly mapped during controlled cutover.

On a headless server, explicitly configure `EDISON_CREDENTIAL_FILE` outside release directories and exports. The service identity must own and exclusively read/write that regular file (0600) and its private parent directory. No implicit file fallback is introduced. Reads reject permissive files and symlinks; writes use unique 0600 staging files, fsync and atomic replacement. Access tokens remain memory-only. Native desktop credential storage is still available to the independent CLI.

## First authorization on a trusted device

Run `PYTHONPATH=src python scripts/youtube_authorize.py --client-file /private/path/google-client.json --output /private/path/edison-handoff.json` on the trusted device. This uses the established installed-app consent flow, state, PKCE and loopback callback. In the authenticated Edison Web connection dialog, select that private handoff file. It contains the grant and matching client, never a content session. The server actually refreshes and reads subscriptions before replacing its credential record. Delete the temporary handoff after success; the helper refuses overwriting an existing file. No credential is printed or persisted to browser storage.

`connection.import_authorization` is explicit user intent. It preserves Auto Update and accepted/cancelled task intent. It does not claim content-session capability or account consistency. The normal callback flow remains available to independent CLI consumers; retirement of legacy Web configure/migrate/authorize entries is handled by the coordinated connection-flow change.

`connection.status.subscription_capability` separates observed subscription availability from the historical `authorized` marker. It records latest success, latest failure, reason/category and current state; restart starts unverified until actual access is checked. Refresh without a replacement refresh token retains the existing one. Network, quota, permission, storage and OAuth-client configuration errors remain distinct. `invalid_grant` requires renewed authorization but does **not** prove a Google security challenge. Failure evidence never stores the remote response body.

## Isolated evidence, 2026-10-19

- Authenticated Web → real host loops → temporary persistent library and file vault: trusted-device import, concurrent ordinary-expiry refresh, retained refresh token, restart refresh, permission preservation and safe failure categories.
- Real Chromium browser → same host: upload, verified subscription evidence, quota interruption, backend process restart, successful refresh; no refresh token in browser storage.
- `scripts/check_youtube_credential_store.py` runs under the invoking nonroot identity with a **new synthetic-only directory**. Linux Docker `python:3.11-slim` (Python 3.11.17), UID/GID 10001:10001, network disabled, code mounted read-only and private `/state` tmpfs passed owner-only read/write, atomic replacement, fresh-process reload and reload after abrupt writer exit. macOS UID 501 also passed the Python Web/file tests. This verifies process restart and file contracts, not host reboot or production storage durability.

To repeat the isolated Linux check: run the script with `PYTHONPATH=/repo/src`, the repository mounted read-only at `/repo`, UID/GID 10001 and a writable private `/state`; provide `/state/new-verification` as the argument. The script refuses an existing directory and root execution, so it cannot silently target a credential store.

## Still requires deployment evidence

No production credential, service configuration or real Google account was inspected or migrated. The actual production service UID, canonical path, matching client and read/write/replacement/restart checks must be recorded during authorized cutover. The container result is not evidence that the production filesystem is accessible.

Google Cloud project publishing status, actual granted scopes and offline grant lifetime must be verified by its owner. External + Testing grants for YouTube normally expire after seven days; changing a console setting does not prove an older grant has been reissued. Production publishing is not an unlimited-lifetime promise. Natural refresh-token events, revocation, actual Google challenges, production service/host restart and the fourteen-day observation remain unverified. See the [Google token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration) and the accepted [specification](https://github.com/v6582374-netizen/Edison/issues/47).
