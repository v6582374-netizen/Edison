# Share a durable cooldown across YouTube acquisition

**Status: accepted; recovery policy amended 2026-10-19.** The shared recovery and finite caption waiting policies below are implemented at the Web host and persistent workspace boundary; production and continuous-operation acceptance remain separate. A subtitle HTTP 429 previously marked one video unavailable and immediately allowed the next video to make requests. The workbench shares persistent pacing and cooldown across Shorts classification and yt-dlp metadata/subtitle acquisition, because an access restriction can affect the entire session or network exit rather than an individual video.

Top-level requests are spaced by at least two seconds and video acquisitions by at least fifteen seconds. After acquisition-side HTTP 429, the first three recovery attempts wait fifteen, thirty and sixty minutes respectively; subsequent shared checks wait six hours. A later server `Retry-After` deadline always takes precedence, including HTTP-date values. Acquisition-side transient network failures use ten, twenty and forty minutes before the same six-hour shared-check interval. These are conservative product defaults, not official YouTube quotas or guarantees. Only a successful caption acquisition resets the throttle streak; successful metadata requests do not.

Cooldown is retained across restart and cannot be bypassed by refresh, resume, regeneration, or retry. The former rule requiring manual action after three automatic retries is superseded for transient network failures and throttling: one shared capability check runs at a time, and recovery resumes eligible work without exhausting every waiting video's budget. Pauses, exclusions, cancellation, age eligibility and manuscript retention still apply; recovery never enables Auto Update or reconnects a deliberately disconnected identity. Migration of historical exhausted records is a separate decision, not permission to reset all failures.

Genuine per-video caption absence has a separate finite wait: check one, six and twenty-four hours after the first confirmed absence, then retain the video with automatic checks ended until an explicit retry. The 24-hour instant is inclusive; after it, an unperformed automatic check expires before acquisition. Pause, restart and request pacing do not extend this window. The host rechecks both when claiming work and immediately before each paced external request. This classification requires ruling out network, throttling and authentication barriers; an empty result alone is insufficient. Confirmed deletion or access denial is not caption waiting.

Metadata is extracted once and reused to download the chosen subtitle language. The workbench owns retry timing rather than allowing hidden immediate yt-dlp retries. The scope is this local workspace's workbench traffic; it cannot coordinate unrelated applications sharing the same network exit.

Official Data API discovery has a separate persistent project-quota/service gate and local request ledger. Caption cooldown cannot delay subscription discovery. See [official discovery](../architecture/youtube-discovery.md).

The canonical amendment and acceptance contract is [什么情况下自动恢复、什么情况下才打断用户](https://github.com/v6582374-netizen/Edison/issues/45#issuecomment-6258398691). It separates controlled recovery E2E from at least fourteen days of real operation; neither document changes nor short probes establish that the contract has passed.

## Recovery ownership

The existing request policy stores acquisition fault kind, first/last failure time,
shared next-check deadline and separate network/throttle streaks. Workspace claims
serialize the single recovery probe while retained-transcript tasks bypass that
acquisition gate. Metadata alone does not clear recovery. The workspace stores
confirmed caption absence and check progress separately from the retained model
retry budget. An uncertain empty result is terminal for automatic work until
investigated, not proof of missing captions. Explicit retry can reopen finite
caption checks without bypassing shared cooldown or changing queue order.

The host starts another bounded preparation round after its batch limit; it does
not depend on a new discovery pass to resume due work. Every new claim still
checks execution permission, cancellation, source scope and freshness. Historical
exhausted records are not bulk reset by this change; migration remains explicit.

### Consumer contract

`activity.snapshot.youtube_requests` exposes `cooldown_until`, throttle `strikes`,
`network_failures`, `failure_kind`, `first_failure_at`, `last_failure_at`, `reason`
and monotonic `failure_sequence`. A real caption request captures the sequence;
its success cannot clear a later failure even if that later deadline has elapsed.
`activity.list` exposes each video's `acquisition_failure_kind`, `retry_at`,
`caption_first_absent_at`, `caption_checks` and `caption_checks_ended_at`. Missing
historical evidence stays missing; the schema upgrade does not classify old errors.

Consumers outside the host must use `YouTubeRequestPolicy.acquisition()` around
both acquisition and recording its outcome. The public subtitle downloader also
uses this scope. Recovery probes share an advisory file lock beside the workspace
database; ownership is released on scope exit or process death, rather than by an
expiring timer that could overlap a still-running request. POSIX uses `flock` and
Windows uses a nonblocking one-byte CRT lock; platform modules are imported only
when that platform acquires ownership. No unlocked fallback is permitted. Acquisition on another
capability's retained transcript does not acquire this lock. A probe that only
confirms absent/inaccessible captions retains recovery and schedules a later
shared check; metadata alone is not a successful caption probe.
