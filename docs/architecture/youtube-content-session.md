# Server YouTube content session

The Web host owns a `ContentSession` separately from Google official authorization.
Its private state directory is outside the reading library. A kernel ownership
lease fixes one host/browser writer to that directory and records the non-root
service UID. Changing the UID is a maintenance error, not a sign-in instruction.
A dedicated Chromium profile is the durable source. No prototype profile, desktop
Cookie export, or production Cookie file is imported into this path.

The Linux adapter starts headed, sandboxed Chromium and a private authenticated
Xvfb display in one process group. The Web UI relays screenshot, pointer and
keyboard operations over the existing authenticated capability endpoint. It
opens no CDP, VNC, or other public browser listener. Screens and typed input are
not persisted; handoff responses have `Cache-Control: no-store`. Operators must
also keep request-body tracing disabled at reverse proxies and observability
layers. Passwords and security challenges are completed by the person on the
actual Google page, followed by the YouTube site sign-in. The screen origin is
visible. Screenshot polling does not reset the 15-minute idle timer; user input
does. Closing the Web page does not disconnect the content session.

The person explicitly confirms that this connection and official authorization
use the same Google account. The UI and durable state identify this as **user
confirmed, not automatically verified**. Channel or Brand Account IDs are not
used as account identity evidence. The browser checks YouTube's signed-in page
state before releasing a snapshot. That alone never marks captions available.

## Acquisition and invalidation

A browser snapshot is atomically installed with a new generation. Every
caption or Shorts consumer receives its own private, randomly named directory
and copy of the current Cookie snapshot. No consumer receives the profile or
shared source file. Response Cookie changes are discarded on completion.
Caption acquisition runs in a fresh Python subprocess. Typed private-pipe events
let the host retain request pacing, cooldown, metadata and commencement checks;
worker-local downloader retries are disabled through the existing request policy.

A content check uses a supplied video URL and always obtains a new response. It
never reads a retained transcript. Only identity-matched, parseable, nonempty
subtitles establish availability; live chat does not qualify. Every host restart
reports the last success timestamp but current capability is unverified until a
new actual response succeeds. A stopped browser does not prevent fresh workers
from acquiring with the saved snapshot.

Snapshot replacement invalidates older consumers. Old successes and failures
cannot overwrite a newer verified session. `ContentSession.clear()` stops the
browser, increments the generation, and removes profile/snapshot/consumer
material. Removing each consumer's parent directory prevents a late CookieJar
save from recreating a deleted credential file. It does not delete reading
assets, change Auto Update, or restore cancelled work. Unified Google + content
disconnection is a separate caller of this boundary. `shutdown()` preserves the
profile/snapshot for service restart and invalidates that host's in-flight results.

Browser close first requests graceful profile flushing, then terminates and, if
needed, kills the entire process group even if the driver has already exited.
The inherited ownership descriptor prevents a new owner from racing the live
browser driver during normal host exit. Chromium's native profile lock also
rejects an overlapping browser. Whole-cgroup resource enforcement and abnormal
host/OS lifecycle admission remain separate runtime requirements.

## Isolated setup and connection procedure

This implementation does **not** grant production admission. Browser startup is
opt-in and off by default. Provision and test a constrained browser process group
before enabling it in production; a process group alone does not enforce the
768 MiB combined memory/swap requirement. Do not set the enable flag on the live
instance until resource admission, recovery drills and the controlled maintenance
window are approved. Automatic maintenance is not part of this slice.

For an isolated test deployment using a fixed non-root service identity:

1. Install the project's `browser` extra in the service Python environment.
   Install a matching Playwright Chromium build or set `EDISON_CONTENT_CHROMIUM`
   to the absolute executable path of a sandbox-capable Chromium installation.
   On Linux, install `Xvfb` and `xauth`. Do not disable Chromium's sandbox to make
   startup pass. Verify the service identity can execute all three programs.
2. Create a fresh stable directory owned by that service identity, mode `0700`,
   outside the reading library and release tree. Set
   `EDISON_CONTENT_SESSION_DIR=/absolute/stable/state/youtube-content-session`.
   The directory selects the managed path for **both** captions and Shorts;
   there is no fallback to `YOUTUBE_COOKIES_FILE` in that selected path.
   Authentication snapshots must stay out of asset backups and exports.
3. In the isolated environment only, set `EDISON_CONTENT_BROWSER_ENABLED=1`.
   Restart the host normally. This flag enables the adapter, not resource or
   sustained-availability acceptance. If the state-directory setting is absent,
   the host retains its pre-cutover acquisition behavior; the new UI cannot start
   a browser. Retiring old backend credential endpoints belongs to cutover work.
4. Open YouTube → subscription channels → Connect YouTube. Complete official
   authorization using its existing trusted-device handoff. In **YouTube content
   session**, open the server browser; click/type or paste directly into the
   displayed Google page. Complete Google sign-in, then use **enter YouTube** and
   finish the YouTube site handoff. Do not send credentials to logs or tickets.
5. Confirm account consistency, paste a YouTube video link known to have source
   subtitles, and select **complete login and check captions**. Availability is
   reported only after a new matching subtitle response.
6. Close the server browser and select **fetch captions again**. Then restart the
   service, return through Edison sign-in, and repeat the actual caption check.
   Retain non-secret evidence of these fresh-process checks. This sequence must
   be repeated on the formally provisioned fresh profile during production
   admission; copying or changing ownership of an old prototype is not evidence.

The browser adapter exposes `process.pid` as its process-group identity for the
resource controller, and all starts pass through `ServerBrowser.start`. The
session exposes `open`, `browser_command`, `confirm`, `verify`, `tick`,
`close_browser`, `snapshot`, `accept`, `clear`, and `shutdown`; later maintenance
and disconnect callers must use these boundaries rather than edit credential
files or launch another writer.

## Evidence boundaries

`youtube-content.spec.ts` uses the real Web capability endpoint, host background
loops, subprocess caption protocol and persistent temporary library. Only the
external Google/YouTube/browser/model responses and time are controlled. It
covers incomplete site login, empty/wrong-video results, successful fresh
captions, browser close, different worker PIDs, service restart, a Web-submitted
manual video reaching a manuscript with Auto Update off, failure reason and idle
close. Focused tests cover single ownership, generation races, secret-copy
cleanup, typed failure categories, and orphaned process-group cleanup. A real
Chromium protocol test exercises screenshots, input, close/reopen against a local
page; its explicit headless mode is confined to that test.

These are controlled functional and local-browser evidence. No genuine Google
login or authenticated external subtitle smoke was performed for this change.
The headed Linux deployment, natural Cookie rotation, service-account production
permissions, whole-host crash/reboot, real busy resource coexistence, reading P95
and 14-day trial remain unverified. Existing historical prototype success does
not fill those gaps or establish acceptance of this fresh managed profile.

To run the optional real-browser protocol coverage, install the `browser` extra
in the test interpreter, then run `python -m playwright install chromium` (or set
`EDISON_CONTENT_CHROMIUM` to an installed executable) before
`PYTHONPATH=src:. python -m pytest tests/test_session_browser.py`. If the extra or
browser executable is absent, only that real-browser test reports **skipped**;
pipe-timeout and process-group tests still run. A skip is missing evidence, not
a browser acceptance result. The Web E2E verifies native keyboard clipboard paste
in Chromium; WebKit runs the other content-session scenarios without claiming
clipboard coverage.
