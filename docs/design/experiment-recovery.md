# Isolated experiment recovery

This boundary implements [interruption and publication consistency](https://github.com/vincentshuo/Edison/issues/74) for the private A/B/C comparison. It changes neither the production workspace publisher nor production translation defaults, scheduler, discovery, settings or deployment.

## Explicit attempts

Call `run_baseline(root, snapshot, model, complete, attempt_id="sample-A-001", mode="new", ...)` once. Record its returned per-invocation `run.json`. Resume with the same arguments, stable ID and `mode="resume"`. Omitting an ID for a new invocation retains the original fresh-attempt behavior. Resume requires an ID; existing IDs cannot be overwritten through `new`.

The attempt's private `attempts/<id>/identity.json` binds raw source bytes and metadata, normalized source units and text, language, normalization annotations and policy, model ID, proxy version and complete configuration hash, user prompts/settings, direct/review prompts, variant, review limits, segmentation, capacity, consumption policy and quota contract. Identity comparison and production/link isolation run before experiment mutations. A mismatch requires a different explicit new attempt. Invalid source normalization still goes through the actual PreparationService and records an actionable failure rather than publishing an output.

Each attempt owns `attempts/<id>/checkpoint.json`. Starting another attempt preserves earlier checkpoints and successful versions. Explicitly resuming an earlier failed attempt can reuse only that attempt's responses. An interrupted different active attempt must be resumed first. The standard workspace checkpoint path is a compatibility mirror for existing offline inspection/continuation; experiment resumes always read the attempt-owned file.

Ordinary errors, truncation, absent finish metadata, unknown HTTP 402, account limits and authentication errors retain progress. A/B/C require an explicit successful finish reason. Each failed transient request has at most three internal tries. Explicit resume honors durable retry deadlines and account gates. Main runs ignore local cumulative request/token caps, including large restored counters. Only an exact, separately verified provider quota contract discards the unfinished current checkpoint. Source, other attempts and successful publications remain retained.

Cancellation uses the existing `LocalWorkspace.change_queue` public operation. Resume and new submission cannot restore cancelled work. Restoration remains a separate explicit operation. A completed attempt replay makes no model calls, uses its original publication and does not finish a newer interrupted attempt.

## Publication and interruption

The experiment-only workspace publisher creates a complete, fsynced bundle in an attempt-owned temporary directory, then atomically renames it to `attempts/<id>/publication`. It never writes into an older publication. A single `BEGIN IMMEDIATE` SQLite transaction allocates the version and commits manuscript, generation record, current pointer and a unique publication key for the attempt. SQLite serialization prevents concurrent allocation collisions. A repeat with an existing key returns the original publication and rejects differing content.

After interruption before the database commit, resume reuses cached generation and verifies/replaces only that attempt's uncommitted bundle before publishing once. After interruption following the commit but before ready state, resume recognizes the committed key and finishes without another model request or version. Complete manuscript/translation/provenance files always precede the committed current pointer. A process-wide file lock serializes runner invocations for the same experiment root; the database transaction separately serializes publication.

`publication_fault(stage)` is an experiment-only injected boundary. `stage` is `after_bundle` or `after_commit`; tests raise a simulated process-loss BaseException there. Tests assert observable documents, source bytes, preparation state and generation history through real PreparationService, HostManuscripts and isolated persistence. Only external model behavior and clock are faked. These are process-interruption checks, not a guarantee against filesystem or hardware corruption.

## Evidence and metrics

Every invocation retains raw captions and private request/response, finish, usage, elapsed and safe error-type evidence. Identity files and checkpoints are private beneath the owner-only experiment root. `run.json` distinguishes stable `attempt_id` from invocation `run_id`, invocation request count from cumulative per-attempt request evidence, cached response count present before execution, and an already committed publication. Counts of cached responses do not claim measured reused-stage duration; that duration is unknown. Failed requests can still incur provider charges, and missing usage/cost stays unknown. Candidate stage timings and per-stage request durations retain their existing distinct meanings; baseline A stage wall times remain unknown.

Validation covers A/B/C connection loss and bounded retry, explicit time gates, strict finish metadata, both publication interruption windows, concurrent completed replay, previous-version/source preservation, identity rejection without writes, cancellation, independent attempts, exact quota discard, high counters, and production path/link isolation. These synthetic checks prove recovery mechanics, not language quality, real provider limits or a fair comparison of different segmentation policies.
