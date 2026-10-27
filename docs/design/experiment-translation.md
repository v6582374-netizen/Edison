# Direct faithful translation in the isolated comparison

[方案 B](https://github.com/v6582374-netizen/Edison/issues/72) is selected through the same `run_baseline` preparation entry as the existing A mechanism. It uses the same `PreparationService`, `HostManuscripts`, conservative normalized source and real versioned workspace. Production construction and default settings remain unchanged.

## Execution contract

```python
capacity = CompletionCapacity(
    input_characters=observed_safe_input_size,
    output_tokens=requested_allocation,
    verified=True,
    evidence_reference=private_probe_record,
    observed_output_tokens=observed_output_lower_bound,
    output_budget_effective=False,  # when the proxy demonstrably ignores the request
    model_id=model.id,
    configuration_sha256=model.configuration_sha256,
)
run_baseline(
    private_root, snapshot, model, complete,
    variant="B-direct", capacity=capacity,
    segmentation=OutputSegmentation("whole"),
)
```

`input_characters` bounds the rendered request including full source context, source IDs and instructions. It must come from actual configured-proxy evidence for real execution. `output_tokens` is a **requested allocation**, not a proven effective limit. Observed output tokens are a lower bound; the contract does not claim a maximum. Real execution requires verified evidence matching the exact model and configuration identity. Simulated contracts are allowed with simulated models and prove only orchestration. Unknown capacity is not replaced with an assumed million-token allowance.

A and B forward the same selected output allocation when supplied. `comparison_settings` records shared controls (source policy, temperature, country, requested output tokens and cumulative-budget policy), separately from variant prompt settings and the segmentation factor. `A-current-baseline` retains and records `upstream-current` segmentation; comparisons that also change segmentation must report that factor rather than attribute the whole difference to direct translation. The explicit [comparable three-stage baseline](./experiment-comparable-baseline.md) shares B/C output chapters for mechanism comparisons. The source and full model/proxy identity remain in the common run record.

## Input context, output units and checkpoints

Every direct request receives the complete normalized source as context, separately from its selected output source IDs. A response is JSON:

```json
{"units": [{"source_ids": ["cue-000001", "cue-000002"], "text": "完整中文译文。"}]}
```

One output unit may cover many small subtitle cues, avoiding a forced model response row per two-word cue. Flattened source IDs must exactly match the requested chapter in source order, once each. Missing, duplicated, reordered, unknown or empty units are rejected. The retained candidate provenance maps outputs back to stable timed source units. Exact structural coverage proves processing coverage, not that every meaning was translated correctly. Quality evaluation remains a separate source-aligned activity.

`OutputSegmentation("whole")` selects one output chapter. `OutputSegmentation("semantic-chapters", chapter_end_ids=(...))` selects explicit source-unit endpoints through the final source unit. Intermediate boundaries must be complete sentence ends. For unpunctuated subtitles, an independently evidenced source chapter endpoint can be supplied in `boundary_evidence=((cue_id, reference), ...)`, for example a publisher's chapter timestamp mapped to the preceding cue. Numeric continuation fragments are rejected even with such evidence. Evidence is retained; the runner does not invent chapter semantics from arbitrary offsets.

Without explicit endpoints, `semantic-chapters` accumulates toward `target_characters` (default 12,000) and chooses only complete sentence ends. This target selects among intact boundaries; it never slices a cue, sentence or number. With no safe sentence boundary it keeps the text together, which may expose an output-capacity limitation rather than manufacture a boundary. Input capacity, output chapter size and recovery checkpoints are separate concepts.

The request adapter retains bounded failure retries and per-chapter checkpoints. Candidate checkpoint identity covers source units, model, prompts/settings, capacity and segmentation. Incompatible outputs cannot silently be reused. Ordinary restart/publication behavior across A/B/C is validated in the separate recovery ticket.

## Independent complete translation and reading manuscript

The model performs direct faithful translation once per output chapter. Complete translated text is retained as `translation.md`. Reading manuscript composition adds generic numbered headings and paragraph separation deterministically, preserving all translated text and its order; it makes no second free rewrite. This deliberately narrow formatting neither summarizes nor invents topic labels. The two artifacts and version history remain separate under ADR 0005.

Private candidate provenance contains rendered mechanism prompt, source coverage and output units, capacity and segmentation, request count, identity and translation-stage elapsed time. Request records retain actual usage separately from conservative estimates. Missing finish reasons, explicit truncation and invalid structural coverage cannot publish candidate manuscripts. No structure-only check is described as semantic fidelity certification.

## Offline CLI

The existing mock CLI accepts `--variant B-direct --capacity-input-characters SIZE --output-tokens TOKENS`, plus `--output-strategy whole|semantic-chapters`, repeatable `--chapter-end-id` and `--chapter-target-characters`. The mock response fixture text must contain the JSON response shape above. Real callbacks use the Python API with matching verified capability evidence; the CLI remains offline and cannot look up credentials or start production scheduling.
