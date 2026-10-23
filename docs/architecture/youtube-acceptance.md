# YouTube capability evidence and continuous acceptance

The Web preparation page shows subscription and caption capability separately
from service liveness. A quiet persistent wait becomes an in-site alert after
24 continuous hours; shared recovery probes continue. Explicit verification,
reauthorization and maintenance faults alert immediately. Unknown invalid grants
are reauthorization faults, never a Google security-verification exception.

`AcceptanceRecord` stores evidence in the existing workspace SQLite database.
Task-stage evidence is written in the task's own transaction and survives console
trimming and later asset deletion. The host samples capability outcomes,
maintenance starts/ends, handoff episodes, successful token refreshes, session
updates and resources independently of open browser pages. Resources aggregate
hourly, with each peak saved immediately; a crash does not discard the current
hour. Aggregates split at observation-period and controlled/natural boundaries.
Kernel OOM counters are counted by increments so old failures remain in their
original periods. Missing counters remain unknown.

Evidence contains domain identities, timestamps, safe reasons and measurements,
never provider responses or credential files. Export applies credential-pattern
redaction as another guard. Evidence is not limited to the last 500 console
lines and has no automatic retention expiry.

## Observation controls

The Web maintenance tools start continuous acceptance, mark explicit fault drills
and download a JSON evidence export. They display the current verdict, elapsed
days, missing daily records, critical coverage, intervention reasons and counts,
and retained earlier-period verdicts. Starting a period does not authorize any
YouTube work or turn Auto Update on.

The authenticated host capability endpoint also supports:

| Capability | Arguments | Result |
| --- | --- | --- |
| `acceptance.status` | none | Current verdict, coverage and earlier verdicts |
| `acceptance.export` | none | Status, periods and complete event evidence |
| `acceptance.begin` | none | Start only when no active period exists |
| `acceptance.drill` | `active`, optional `reason` | Separately account for a deliberate fault drill |
| `acceptance.record_reading` | `baseline_p95_ms`, `maintenance_p95_ms`, `samples`, `load` | Retain a measured reading comparison |

Reading comparison inputs are operator-supplied measurements, not automatic
benchmarks. Use the same client, manuscript and background load in both
conditions, and record samples per condition and load. The accepted increase is
`max(baseline P95 * 20%, 200 ms)`. The resource measurement procedure is in
[resource admission](youtube-resource-admission.md). Resource evidence includes
the enforced memory, swap and CPU contract, isolation status and measured peaks;
missing isolation/counter evidence cannot establish resource coverage.

Evidence sampling and mutating controls use the existing cutover admission
lease. A hold freezes sampling; health reads use the last admitted content
snapshot, leaving a drained backup free of untracked evidence writers. Status
and export remain readable, while mutating acceptance controls are refused.

## Verdict rules and evidence boundaries

At least 14 uninterrupted days **and** all critical events are required. Every
completed day in a still-active period is audited, including days after day 14.
Coverage includes both capabilities' daily outcomes, an automatic subscription
to transcript to manuscript chain, token refresh, session update, natural
response-cookie rotation maintenance, planned service restart, kernel reboot,
unattended capability recovery, resources and a reading comparison. Fourteen
calendar days alone produce `unverified`, with missing events listed.

Ordinary manual credential movement or clicking recovery checks/retries fails
the period. Up to three unprompted Google security handoff episodes are allowed;
the fourth fails. Reauthorization is separately classified and fails, rather
than consuming that exception. Swap, OOM, memory-budget violations, unsafe
isolation and a failed reading comparison also fail.

Planned restarts and recoverable faults preserve the current period. A completed
reconnection, rollback to a previously observed build, or identity/recovery/
resource-policy change ends it and starts a new one. Earlier failures and
interventions stay in the export. Set `EDISON_BUILD_ID` to the immutable release
identity for each deployment and rollback. The identity always includes a
deterministic digest of the deployed Python packages, so even releases with the
same package version or release label remain distinct. If the running code
cannot be identified, starting acceptance is refused. Linux kernel boot
identity distinguishes service restart from host reboot.

Fault drills and any deployment with `EDISON_ACCEPTANCE_CONTROLLED=1` mark events
as controlled. They cannot supply natural coverage, consume natural security
exceptions or silently establish a natural pass. The real Web/host acceptance
E2Es use substituted external services and time, and therefore prove functional
behavior only. Natural rotation and long-running production acceptance remain
unverified until their actual evidence exists.
