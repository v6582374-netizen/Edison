# Isolated translation baseline replay

This is the explicit offline baseline entry for [隔离对照：建立隔离运行入口，跑通现有流程基线](https://github.com/vincentshuo/Edison/issues/69), within [the isolated comparison specification](https://github.com/vincentshuo/Edison/issues/68). It does not start a server, discovery, subscription sync, scheduling, publishing, credential lookup or production settings migration.

The implementation base is main commit `dccf993` (rather than the previously studied integration checkout). The reused translation-agent upstream is `e0fc605acbb5d78cb7a58a98bc8bd8f0056df49c`; its archive and local adaptation are documented in `docs/upstream/translation-agent/`. Each run additionally records the full running checkout commit and whether it is dirty. A dirty run is development evidence, not a reproducible released implementation.

## Explicit offline smoke

From a source checkout with the Python project dependencies installed, place a copied private speech-caption `.srt` snapshot outside production storage. The runner only reads this input. Keep private inputs and outputs outside Git, for example under the ignored `.local/` directory. Do not pass a live production asset as a snapshot. The same raw bytes, video identity, language, title and duration bind one experiment root; use a new root for another sample or a corrected snapshot.

For a minimal **synthetic**, short English example, save this as a private `source.srt`:

```srt
1
00:00:00,000 --> 00:00:02,000
There were 42 people.
```

Save a private `responses.json` containing deterministic initial translation, review, revised complete translation and reading manuscript responses:

```json
[
  {"text": "有 42 人。"},
  {"text": "保留数字和事实。"},
  {"text": "有 42 人。"},
  {"text": "# 参与者\n\n有 42 人。"}
]
```

Launch once with an explicit output root:

```sh
PYTHONPATH=src:. python -m youtube_strataread.workbench.experiment \
  --snapshot .local/translation-inputs/source.srt \
  --mock-responses .local/translation-inputs/responses.json \
  --root .local/translation-experiments/baseline-sample \
  --video-id synthetic-sample --language en --title 'Synthetic short smoke'
```

The command prints JSON containing the private `run.json` path and observed preparation state. It exits 0 only for `ready`. It consumes fixture responses without a network model client. Chinese-only input follows the existing translation bypass and needs only a composition response; multi-chunk inputs need the corresponding number of fixture responses. Optional `--duration-seconds` records independently known video duration; omit it when unknown. Caption end timestamps are not substituted for video duration.

## Reused processing and isolation

`run_baseline(root, CaptionSnapshot(...), ModelIdentity(...), complete, config=...)` is the reusable Python boundary. Its `complete(model, messages, **options)` callback has the existing host completion contract and returns `AssistantTurn`. `ModelIdentity` contains the model identifier, proxy/adapter version, a credential-free configuration SHA-256 and a `simulated` flag. Hash a redacted configuration identity; never put credentials or authenticated URLs into identity fields. There is no live-provider CLI in this ticket.

The entry drives the actual `PreparationService`, `HostManuscripts`, existing caption normalization, upstream three-stage translation, composition and `LocalWorkspace` persistence. It supplies only offline caption acquisition/classification and the injected model response boundary. It seeds validated settings in the isolated database before the host adapter reads them, avoiding global prompt-file loading or migration. Snapshot classification is an explicit offline speech-caption sample declaration, not a new YouTube classification result. A synthetic epoch publication date is used solely to admit the manual offline preparation; it is not evidence of source publication time.

An experiment root must be new/empty, or carry the matching experiment ownership marker and snapshot identity. Outputs cannot overlap the active or default host state directories, the active `YOUTUBE_WORKBENCH_WORKSPACE`, or the legacy default workspace, including ancestor paths. Source snapshots inside those production directories are rejected. Symlinks in an output path or tree and hardlinked output files are rejected before writes. Each root is private (`0700`), each run has a unique directory, and its raw source copy is read-only. These checks prevent accidental path reuse; run the experiment in a directory controlled by the current user, without concurrent external modification. This is not a sandbox for arbitrary injected Python callbacks.

One root represents one sample. Each explicit invocation starts a new baseline generation attempt, retaining successful historical versions. It does not automatically retry failed jobs or resume an earlier run. Existing request-level bounded retries still apply; restart/resume/publication consistency is the separate follow-up ticket. No production application imports the experiment runner.

## Private record protocol, version 1

`experiment.json` binds ownership and immutable source identity. `workspace/` is the real isolated library. `runs/<run_id>/` contains independent raw captions, complete translation, reading manuscript and a `run.json` record. Successful runs also copy the baseline pipeline provenance. Rejected responses and request metadata remain in private `request-NNNN.json` files. A failed attempt has no successful output links; a previously successful workspace version remains available.

The record contains:

- Schema/run/variant identity (`A-current-baseline`), repository base and running commit, upstream version, model/proxy identity and source SHA-256/metadata.
- Effective validated settings and all prompt templates, request attempt count (including failed requests), elapsed time, observed preparation state and safe failure reason.
- Separate provider-reported fresh input/output/cache read/cache write token totals, the number of requests with and without reported usage, and unknown cost (`null`). Absent or all-zero usage is unknown, not measured free usage. A provider's unreported failed request may still be billed. Simulated reported usage remains simulated.
- Relative artifact links based at the run directory, plus the retained workspace manuscript version. `baseline-provenance.json` contains the pipeline's normalized source hash and historical `budget_tokens`; that counter mixes estimated reservations and available actual usage and must not be labeled measured provider consumption.

The request files retain rendered messages/options, finish reason, response, available usage and elapsed time; error messages and raw provider payloads are not copied. Keep the entire experiment root private: it contains full source content and model responses. Only aggregate conclusions belong in public reports. Records are measurements of an isolated attempt, not a claim of semantic fidelity.

## Known limits of this baseline

Main experimental execution now disables local cumulative call/token budgets, independently of legacy settings and restored checkpoint counters. The optional `historical_diagnostic=True` API argument or `--historical-diagnostic` CLI flag retains those historical budgets for a separately labeled diagnostic. Records disclose `consumption_policy` and still set `fair_long_comparison: false` until matching source, provider, candidate and long-sample evidence exists. See [the provider experiment contract](experiment-provider.md). Production settings and default production budget behavior remain unchanged.

The synthetic smoke and deterministic tests demonstrate storage, isolation and artifact flow. They do not establish translation quality, a real proxy contract, two-/four-hour completion, candidate superiority or a production replacement recommendation. Those require the remaining source-handling, candidate, recovery and evaluation tickets. No live model calls, production rollout or historical production reprocessing follow from this entry.
