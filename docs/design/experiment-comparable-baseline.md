# Comparable three-stage baseline for isolated experiments

The [isolated comparison](https://github.com/v6582374-netizen/Edison/issues/76) selects `variant="A-comparable-three-stage"` through `run_baseline`. It uses the existing `PreparationService`, `HostManuscripts`, caption normalization and versioned persistence. No production pipeline construction or settings change.

```python
record = run_baseline(
    private_root, snapshot, model, complete,
    variant="A-comparable-three-stage",
    capacity=verified_capacity,
    segmentation=common_segmentation,
    attempt_id="fixed-attempt", mode="new",
)
# After an ordinary interruption, use identical inputs/options:
record = run_baseline(
    private_root, snapshot, model, complete,
    variant="A-comparable-three-stage",
    capacity=verified_capacity,
    segmentation=common_segmentation,
    attempt_id="fixed-attempt", mode="resume",
)
```

This variant requires conservative source processing and the same verified model/proxy capacity contract as B/C. The offline fixture CLI also accepts its name. Main comparison execution keeps `historical_diagnostic=False`, disabling cumulative local token/call limits while retaining accounting and bounded retries.

## Shared chapter boundaries, retained upstream stages

A-comparable supplies the same `output_chapters` plan as B/C to the retained `translation_agent.utils.multichunk_translation`. That upstream function owns initial translation, reflection and revision, in that order for all chapters. The existing scoped runtime binding and host prompt/request/checkpoint adapter remain in use. Every stage sees the full ordered source while targeting one chapter. Canonical cue newlines survive upstream's separator-free concatenation.

A-comparable composes each revised chapter exactly once using the existing composition templates and neighboring translated snippets. It does not re-split the translation, mutate upstream globals, call a second arbitrary splitter, or change the retained upstream algorithms. A whole-source plan still uses the explicit multichunk stages. Chinese and mixed sources also take those stages, with no A-only translation bypass.

Rendered input capacity is checked for every request, including review/revision prompts enlarged by previous model output and composition prompts. Oversized requests stop before model I/O; they are not truncated. Selected output tokens are forwarded as a requested allocation, with no claim that a proxy enforces that allocation. Missing finish reasons and incomplete completions cannot publish a manuscript.

The revised complete translation and composed reading manuscript remain separate artifacts. Baseline completeness guards remain unchanged, including the composition guard's literal number check. Numeric wording vetoes are an observable baseline limitation, not evidence that B/C improved fidelity.

## Identity, timing and coverage limits

New A-comparable/B/C records expose `effective_chapter_plan` with ordered chapter IDs/source IDs, plus `effective_chapter_plan_sha256`. This identifies actual source ranges in addition to the requested segmentation settings. Provenance retains the same effective plan. `summarize_runs` includes the effective plan hash and normalized source identity in its common comparison identity, alongside model/proxy identity, source, shared settings and segmentation. Only matching identities support paired mechanism comparisons.

`A-current-baseline` remains selectable with `upstream-current` segmentation and no effective common plan. Existing historical records are not rewritten. A comparison that changes mechanism and segmentation together must remain diagnostic. Segmentation experiments keep mechanism fixed and use their own frozen plan.

A-comparable retains per-chapter output text and host-assigned source ranges. Its model returns plain text; it does **not** echo verified per-cue IDs. Provenance explicitly records `output_mapping="host-assigned-chapter-range"`, `model_returned_source_ids_verified=False`, and `semantic_fidelity_verified=False`. Scheduled range coverage cannot prove that the model translated every fact. B/C's returned ID coverage likewise cannot certify semantics.

Source context is comparable as ordered cue text. Wire representations differ: A uses XML and repeats the selected chapter; B/C use JSON context and output IDs. Prompts and editorial work also differ as part of the defined mechanism. Report their actual request/output usage and these representation limits rather than attributing every token difference solely to removed review/revision.

`stage_elapsed_seconds` records initial, review, revision, total translation and composition wall times for the invocation that ran the pipeline. Translation total contains its three stage durations; do not sum it with them. Resumed invocation times include checkpoint access and omit already spent wall time; completed publication replay can have no pipeline timing. Request records and attempt recovery counters distinguish newly performed work from reused responses. No timing is reconstructed as historical model latency.

Ordinary interruption and publication recovery use the existing experiment attempt API. Successful responses are checkpointed under stable upstream stage keys. Exact attempt identity rejects changed source, prompts, model/proxy, capacity or segmentation; duplicate completed resumes publish no new version. The comparable baseline adds no parallel recovery mechanism.

Deterministic tests drive the actual preparation boundary and persistence with fake external model replies. They establish orchestration, shared source/ranges, independent artifact retention, capacity enforcement and recovery. They do not establish real translation quality or validate provider capacity. Actual finite campaign results and recommendation are recorded separately after execution.
