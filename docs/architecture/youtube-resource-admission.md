# Managed YouTube resource admission

The Web host uses one ResourceGovernor for content browser requests and new
caption acquisition. Queue claims skip acquisition work under pressure while
retained-transcript translation and composition remain runnable. The acquisition
boundary rechecks before spawning the consumer and before subtitle I/O, so a
pressure change after claim returns the task to its queue without charging its
retry budget. Recovery never changes Auto Update, cancellation or task eligibility.
Reading and already executing model work are never terminated by this governor.

## Linux deployment contract

Provision a dedicated cgroup v2 child for the entire native browser/display tree:
`memory.max=805306368`, `memory.high=603979776`, `memory.swap.max=0` (768/576/0 MiB).
Set `EDISON_CONTENT_CGROUP_DIR` to its absolute path under `/sys/fs/cgroup` and
retain `EDISON_CONTENT_BROWSER_ENABLED=1` only for an admitted installation.
The host must remain outside that child. Its service identity may write only the
child's `cgroup.procs`; limit files remain operator-owned. The product verifies
limits; it does not elevate privilege or write resource settings. A smaller
budget cannot reuse the fixed release headroom and is rejected too.

The driver joins this child **before** starting Xvfb, x11vnc or Chrome. Existing
service wrappers which already place the driver there remain supported. Every
native descendant inherits the cgroup even if it creates another POSIX session.
The configured memory.max and zero swap bound RAM + swap continuously, rather
than merely measuring launch memory. Operators must retain control-group cleanup
on service stop. A private profile is not permission to run without kernel limits.

Launch needs actual `/proc/meminfo` MemAvailable >= 1280 MiB. Once running,
MemAvailable below 512 MiB or browser group memory >= 608 MiB triggers orderly
browser close before the 768 MiB hard ceiling. The driver samples cheap memory
observations every 100 ms between desktop operations; the host also checks on its
heartbeat. Desktop listener inspection retains its separate one-second cadence.
The 576 MiB kernel high watermark adds reclaim/throttling before the release
threshold. It is not proof against every burst allocation: measured peak, high
pressure, OOM counters and full close latency must be checked on the target host.
A burst or unresponsive browser can still reach the hard ceiling; such a run is
not acceptable evidence of safe coexistence.

Resource closure does not extract or publish a Cookie snapshot. The last valid
snapshot remains stored. The UI says resources are waiting, not that Google needs
a new login. A pending explicit browser request can resume while its original
15-minute input lease remains live. Resource-triggered closes back off 60, 120,
240, then at most 300 seconds; automatic reopening does not extend the lease.
Explicit close/disconnect cancels pending opening, and host restart does not
recreate it. New caption tasks can resume once pressure clears even during the
browser-only retry delay. No recovery path grants new execution permission.

## Deterministic and real-machine verification

`youtube-resources.spec.ts` drives real Web -> host background loop -> temporary
persistent library. Only external Google/YouTube/model responses, time and kernel
resource observations are controlled. It covers deferred opening and recovery,
active-model persistence, retained-work yielding, late pressure before caption
I/O, cancellation, unchanged Auto Update, and UI resource closure. These results
are scheduling evidence, not measured RAM or reading latency.

For real measurements use `youtube-content-server.py ROOT native-measurement`
with a **new synthetic** content/profile/HOME/library and actual Linux wrappers.
This opt-in mode leaves kernel observations real and provides authenticated
`/_measurement/native` operations through the real ServerBrowser public adapter.
Its actual browser only loads a local, credential-free page. Its separately
labelled external content fixture supplies synthetic snapshots/captions for the
real host queue; this does not test Google login or real YouTube page demand.

Run `youtube-resource-measurement.spec.ts` with
`EDISON_RESOURCE_MEASUREMENT_URL` set to that private forwarded fixture and
`EDISON_RESOURCE_MEASUREMENT_OUTPUT` set to a new output path. The test prepares a
fixed reading manuscript through the real pipeline, then repeatedly regenerates
a separate fixed retained transcript through actual translation, checkpoints,
composition and persistence. The external model has fixed 150 ms response
latency; actual call counts, input hashes and manuscript versions establish work
advancement, rather than treating sleep as busy-work evidence. Four A-B-B-A
blocks each exclude ten warmups and collect fifty client action-to-render samples
with a fresh documents.get per opening and two animation frames after content.
The client uses its normal browser fetch/token/CORS transport directly; Node
route.fetch is not in the measured request path. Each opening records its actual
request count (development StrictMode can issue two effect reads). Use the same
production frontend build for both arms when interpreting production reading.
A sidecar `.events.jsonl` preserves every render sample and transport failure;
partial output is marked incomplete and never supplies an accepted P95. No
measurement-only network retry hides failures.
Native maintenance arms use actual screenshot reads every 1.2 seconds. Raw
per-block times, startup latency, calls/versions, queue status and observed launch
admission are written out. The launch-admission probe never starts a second
browser or creates artificial host pressure.

Run an independent whole-cgroup sampler at <=250 ms alongside this harness;
include kernel memory.peak, swap, memory.events (including OOM), pressure,
process membership, host MemAvailable and swap-in/out. Verify populated=0 and
no native descendants after close. Compute nearest-rank P95 separately by block
and pooled condition; delta must be <= max(20% baseline P95, 200 ms). Failures,
insufficient samples, nonadvancing background work, sustained swapping or OOM
remain failed/incomplete evidence. The steady-state samples exclude browser
startup; report startup separately and do not call this startup-overlap proof.
If natural host memory never drops below 1.25 GiB, real low-memory denial remains
unverified; do not allocate pressure on a shared production host to manufacture it.
These measurements cannot establish real Google page coexistence, natural Cookie
rotation, production admission or 14-day reliability.
