# Server YouTube content session

The Web host owns a `ContentSession` separately from Google official authorization.
Its private state directory is outside the reading library. A kernel ownership
lease fixes one host/browser writer to that directory and records the non-root
service UID. Changing the UID is a maintenance error, not a sign-in instruction.
A dedicated Chromium profile is the durable source. No prototype profile, desktop
Cookie export, or production Cookie file is imported into this path.

The Linux adapter starts ordinary headed, sandboxed Chrome, a private authenticated
Xvfb display and x11vnc in one process group. Its private UNIX RFB client forwards
only explicit human input; it does not use Playwright, CDP, page scripting,
automation fingerprints, or browser security-disable flags. The Web UI relays screenshot, pointer and
keyboard operations over the existing authenticated capability endpoint. It
opens no CDP, VNC, or other public browser listener. x11vnc ignores user rc files,
explicitly disables IPv4 and both x11vnc/LibVNCServer IPv6 listeners; `-rfbport 0`
alone is insufficient for builds with IPv6 enabled. Before handoff readiness,
the driver verifies the desktop process's socket ownership against Linux proc
TCP/IPv6 and UNIX listener tables; an unexpected TCP listener, missing private
UNIX listener or unreadable inspection state fails closed. This check repeats
before every command and on a one-second idle polling interval, retiring the desktop
when its transport changes. Desktop ingress is killed and its client disconnected
before any normal Chrome shutdown wait, including snapshot confirmation, so a
blocked browser window cannot prolong an exposed listener. Screens and typed input are
not persisted; handoff responses have `Cache-Control: no-store`. Operators must
also keep request-body tracing disabled at reverse proxies and observability
layers. Passwords and security challenges are completed by the person on the
actual Google page, followed by the YouTube site sign-in. The real browser address bar is
visible; the application does not infer or report a page origin from pixels. Screenshot polling does not reset the 15-minute idle timer; user input
does. Closing the Web page does not disconnect the content session.

The person explicitly confirms that this connection and official authorization
use the same Google account. The UI and durable state identify this as **user
confirmed, not automatically verified**. Channel or Brand Account IDs are not
used as account identity evidence. Confirming closes the native browser and waits for profile flushing before
read-only extraction under the same service identity. Cookie presence does not
prove sign-in or identity; only fresh matching subtitles establish capability.
Native key-storage or partial-decryption failures are maintenance errors and
preserve the previous snapshot and generation. No insecure password-store flag
is added to make extraction succeed.

## Acquisition and invalidation

A browser snapshot is atomically installed with a new generation. Every
caption or Shorts consumer receives its own private, randomly named directory
and copy of the current Cookie snapshot. No consumer receives the profile or
shared source file. Response Cookie changes are discarded on completion.
Caption acquisition runs in a fresh Python subprocess. Typed private-pipe events
let the host retain request pacing, cooldown, metadata and commencement checks;
worker-local downloader retries are disabled through the existing request policy.
The worker keeps its JSON channel on a separate non-inheritable descriptor and
redirects incidental standard output to the null device before acquisition.
This discards yt-dlp progress, Python prints, raw descriptor writes and descendant
output without recording external diagnostics; stderr remains discarded by the
host. `quiet=True` alone does not suppress yt-dlp download progress and is not a
protocol boundary. Local HTTP subtitle regression tests exercise the real yt-dlp
download path, metadata/commencement acknowledgements and complete result delivery.

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

Browser close sends the normal X11 `WM_DELETE_WINDOW` protocol to the browser's
mapped native windows and waits for clean process exit before reading the profile.
It does not accept before-unload dialogs. Keyboard/omnibox quit proved unreliable
in the native fixture, so closing does not depend on focus or typing timing. SIGTERM did not persist
the synthetic cookie in the native Linux test, so termination is emergency
cleanup only and can never authorize snapshot extraction. Failed orderly close
preserves the old snapshot. Cleanup terminates and, if needed, kills the whole
process group even if the driver has already exited.
The inherited ownership descriptor prevents a new owner from racing the live
browser driver during normal host exit. Chromium's native profile lock also
rejects an overlapping browser. The shared [resource admission](youtube-resource-admission.md)
now verifies and enrolls the entire display/browser tree in its dedicated Linux
cgroup. Target-machine coexistence and abnormal host/OS admission remain separate
evidence requirements.

## Unattended maintenance and human handoff

The same `ContentSession` schedules maintenance; there is no second guardian or
task engine. The managed host's heartbeat advances it whether or not an Edison
page is open or its sign-in has expired. Two observations are a maintenance
basis, and only for a session that has already produced a matching caption:

- a background caption consumer is refused with a YouTube sign-in requirement
  (the worker's `user` kind) on the current snapshot generation;
- a consumer's own copy comes back with a replaced identity cookie (`SID`
  family, `*PSIDTS`, `LOGIN_INFO`; churning `*CC` values are ignored). The copy
  is still discarded; it only tells the host that Google moved the session on
  without the profile.

Network failures, 429, confirmed caption absence, storage/program failures and
unknown results never start the browser. Web-initiated checks report their
failure to the person instead of scheduling maintenance, and a refusal during
initial connection keeps the ordinary sign-in path.

A run opens the profile's own browser at youtube.com, leaves it for 45 seconds,
then closes it in order and publishes the read-only snapshot as a new
generation. Older leases are invalidated as with any snapshot replacement. Each run is cut off,
unpublished, three minutes after it was spent. At most two runs start in any
rolling 24 hours. The attempt is written to `state.json` before the browser
starts, so a normal or crashed host restart cannot refund it; an interrupted run
is cleared on the next start and its basis stays due. Disconnection keeps the
spent attempts, cancels the run and its basis; a run that loses the generation
race publishes nothing. Runs wait for the shared YouTube cooldown and for browser
resource admission; both waits report a reason and the next time they can act
(`maintenance.next_at`). Budget exhaustion reports the time the oldest attempt
leaves the window. Opening the browser by hand takes over the one profile
browser instead of launching another, and closing it or a cutover drain ends
the run.

While the current snapshot is refused, queued caption work stays queued without
spending its retry budget; retained-transcript work continues. Captions resume
from the next current snapshot and still pass through cooldown, pacing,
permission and cancellation checks. A snapshot that a maintenance run produced
and that is refused before any caption success, or a profile that is signed out
when maintenance closes it, is an explicit verification requirement: automatic
runs stop and the session reports `needs_user`. The person opens the browser
and enters the password or verification code only on the Google page. That
handoff is not bounded by the three-minute run cap; it closes after 15 minutes
without input and can be reopened. A successful caption check ends the handoff.

The 45-second dwell is an operating choice. Whether a real profile refreshes its
cookies in that time, whether YouTube actually rotates them in consumer
responses, and how often natural rotation or real Google challenges occur are
**not verified**. Controlled E2E proves the control path only.

## Isolated setup and connection procedure

This implementation does **not** grant production admission. Browser startup is
opt-in and off by default. Provision and test a constrained browser process group
before enabling it in production; a process group alone does not enforce the
1280 MiB memory ceiling with zero swap. Do not set the enable flag on the live
instance until resource admission, recovery drills and the controlled maintenance
window are approved. Unattended maintenance (below) uses the same adapter and the
same enable flag; it grants no admission either.

For an isolated test deployment using a fixed non-root service identity:

1. Install an ordinary supported stable Chrome and set `EDISON_CONTENT_CHROMIUM`
   to its absolute executable path (or provide `google-chrome-stable` on PATH).
   On Linux, install `Xvfb`, `xauth` and `x11vnc`; `EDISON_CONTENT_X11VNC` may select
   a private x11vnc executable. No Playwright dependency is required by this adapter.
   Do not disable the browser sandbox or weaken its native password store.
   Verify executable access and native cookie decryption under the service identity.
2. Create a fresh stable directory owned by that service identity, mode `0700`,
   outside the reading library and release tree. Set
   `EDISON_CONTENT_SESSION_DIR=/absolute/stable/state/youtube-content-session`.
   The directory selects the managed path for **both** captions and Shorts;
   there is no fallback to `YOUTUBE_COOKIES_FILE` in that selected path.
   Authentication snapshots must stay out of asset backups and exports.
3. Provision the dedicated cgroup and `EDISON_CONTENT_CGROUP_DIR` according to
   [resource admission](youtube-resource-admission.md), including the exact
   1280 MiB memory, 1152 MiB high and zero swap limits. In the isolated environment
   only, set `EDISON_CONTENT_BROWSER_ENABLED=1`.
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
   reported only after a new matching subtitle response. The browser closes during
   this check, including on failure; reopen it explicitly if further login is needed.
6. With the browser closed, select **fetch captions again**. Then restart the
   service, return through Edison sign-in, and repeat the actual caption check.
   Retain non-secret evidence of these fresh-process checks. This sequence must
   be repeated on the formally provisioned fresh profile during production
   admission; copying or changing ownership of an old prototype is not evidence.

The browser adapter exposes `process.pid` as its process-group identity for the
resource controller, and all starts pass through `ServerBrowser.start`. The
session exposes `open`, `browser_command`, `confirm`, `verify`, `tick`,
`acquisition_ready`, `close_browser`, `snapshot`, `accept`, `clear`, and `shutdown`; maintenance
and disconnect callers must use these boundaries rather than edit credential
files or launch another writer.

## Evidence boundaries

`youtube-content.spec.ts` uses the real Web capability endpoint, host background
loops, subprocess caption protocol and persistent temporary library. Only the
external Google/YouTube/browser/model responses and time are controlled. It
covers incomplete site login, empty/wrong-video results, successful fresh
captions, browser close, different worker PIDs, service restart, a Web-submitted
manual video reaching a manuscript with Auto Update off, failure reason and idle
close. `youtube-maintenance.spec.ts` uses the same boundaries for unattended
maintenance: ordinary failures that start no browser, a closed page, the
three-minute cut-off, the rolling budget across SIGTERM and SIGKILL restarts,
response rotation, handoff with an unbounded and reopenable person's browser,
resource waiting with its next time, one run for several waiting videos, and a
disconnect racing a snapshot. Focused tests cover single ownership, generation races, secret-copy
cleanup, typed failure categories, and orphaned process-group cleanup. A real
native Linux protocol test exercises screenshots, synthetic input, graceful
close, read-only profile extraction and reopen against a local HTTP page. Local
fixture cookies remain excluded from production Google/YouTube snapshots.

The native Linux transport/lifecycle suite passed nine tests, including rejection
of IPv4/IPv6 listeners at startup, late-listener retirement during idle and command
handling, and ingress revocation before delayed Chrome shutdown under `SIGSTOP`.
Normal close, cookie flushing and reopen also passed. Caption protocol validation
passed 27 local tests and 11 tests under the isolated Linux service identity.
These controlled tests establish their respective protocol and lifecycle behavior;
they do not by themselves establish external YouTube availability.

On 2026-10-19, the isolated deployment was exercised through the real Web UI in
Zen with a fresh managed profile. The person completed the native sign-in flow
and confirmed account consistency. Three fresh external subtitle acquisitions for
video `arj7oStGLkU` passed nonempty, parseability and matching-video validation,
without reading retained transcripts (times below are Asia/Shanghai):

- 18:04:50: caption worker PID `767819` succeeded.
- 18:05:35: with the browser closed, new worker PID `768110` succeeded.
- 18:06:00: a normal isolated-service restart preserved the last-success history
  and user account declaration while correctly resetting current capability to
  unverified. At 18:07:54, new worker PID `768693` acquired fresh subtitles and
  restored verified capability.

After these checks the snapshot was owned by `edison` with mode `0600`, no
consumer copies remained, and the browser cgroup was empty. This establishes
fresh external caption acquisition from this managed snapshot across browser
closure and a normal service restart. The public video does not prove that its
subtitles required authentication. Official OAuth was not configured in the
isolated deployment, so account consistency remains a user declaration rather
than a verified comparison with an authorized official account.

The earlier Playwright handoff encountered a genuine Google unsupported-browser
rejection; this is consistent with Google’s documented restrictions on automated
browsers, not a uniquely proven detection cause. The successful native handoff
above supersedes the earlier absence of fresh-profile external evidence. See
[Google supported-browser guidance](https://support.google.com/accounts/answer/7675428).

Production was unchanged. A previous real YouTube browser run exhausted its
former 768 MiB cgroup limit; later functional success does not erase that resource
failure or establish admission under the current 1280 MiB budget. Busy-workload coexistence and reading P95
acceptance remain outstanding under [#52](https://github.com/v6582374-netizen/Edison/issues/52).
Natural Cookie rotation, whole-host crash/reboot, production deployment and
service-account permissions, and the 14-day trial remain unverified. A normal
isolated-service restart is not evidence for those broader lifecycle or sustained
availability claims. Existing historical prototype success does not fill these
gaps.

To run optional real-browser coverage, provision native Chrome, Xvfb, xauth and
x11vnc and `ss` (iproute2) in an isolated Linux environment under the same resource cap. Configure
`EDISON_CONTENT_CHROMIUM` and optionally `EDISON_CONTENT_X11VNC`, then run
`PYTHONPATH=src:. python -m pytest tests/test_session_browser.py`. Without Linux
and those executables the native tests report **skipped**. Real native lifecycle
coverage inspects TCP and UNIX listeners across the browser process group after
start and reopen; neither launch-argument assertions nor a firewall establish
private transport. Deliberately misconfigured real x11vnc processes must be
rejected for either IPv4 or IPv6 listening, with their processes and ports cleaned
up. An inherited real socket activated after readiness verifies both idle
retirement without another request and rejection of subsequent input/screenshot
commands. A real Chrome process stopped with `SIGSTOP` verifies that listener
revocation finishes independently of slow browser shutdown; connection timeouts
never count as proof of port closure. UNIX RFB, pipe-timeout
and process-group tests still run. A skip is missing evidence, not acceptance.
The Web E2E verifies native keyboard clipboard paste
in Chromium; WebKit runs the other content-session scenarios without claiming
clipboard coverage.
