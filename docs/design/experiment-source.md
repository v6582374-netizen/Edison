# Fixed speech-caption sources for isolated comparisons

This is the source-processing contract for [保证字幕来源完整，无字幕时明确告知](https://github.com/v6582374-netizen/Edison/issues/70), within the [isolated comparison specification](https://github.com/v6582374-netizen/Edison/issues/68). It is an explicitly selected experiment policy. Production caption cleanup and downloader behavior retain their existing defaults.

`run_baseline` uses `source_policy="conservative-v1"` by default. The real `PreparationService` receives an optional `transcript_normalizer` callback, keeping its existing acquisition, status handling, `HostManuscripts` generation and persistence boundary. Omitting this callback preserves production behavior. The isolated experiment supplies a copied, read-only SRT snapshot and never starts discovery, speech recognition or production scheduling.

## Immutable source representation

Every successful normalization saves a private, read-only `normalized-source.json` alongside the byte-identical raw source copy. The run record links it as `artifacts.normalized_source` and records its hash in `source_processing.normalized_sha256`. This artifact is independent of the translation mechanism and can be fixed for A/B/C.

The version-1 artifact includes:

- `policy`, exact `raw_sha256`, normalized `text`, and canonical-record `sha256` (SHA-256 of all other fields encoded as UTF-8 JSON with sorted keys).
- `units` in original cue order, with stable ordinal identifiers such as `cue-000001`, original SRT caption index, `start_ms` and `end_ms`.
- Each unit's `raw_text`, normalized `text`, and half-open `source_char_range` within that original cue content. Whitespace is collapsed for model input; raw text and raw bytes remain available to resolve the range precisely.
- Optional `overlap_of` identifying the preceding cue when a verified rolling overlap was removed. The retained raw text explains the removed prefix. This is source provenance, not a claim of semantic correctness.

Normalization does not delete parentheses, square brackets, speaker prefixes, names, qualifications, intentional repetition or non-speech markers wholesale. It does not invent a speaker identity, merge across speaker turns, reorder cues or globally deduplicate repeated text. Metadata and timing come from the supplied snapshot; caption end time does not establish actual video duration.

## Rolling captions require explicit evidence

By default every repetition is retained, including temporally overlapping cues. For a source whose rolling-caption behavior has been independently verified, `CaptionSnapshot.verified_rolling_pairs=((1, 2), ...)` carries the explicit annotation using 1-based cue ordinals. An annotation is not inferred from matching text alone.

The normalizer additionally checks adjacent cue order, overlapping time intervals, and an exact complete-word suffix/prefix containing at least two words. It removes only that prefix from the later cue and records the remaining source range and earlier cue ID. If these conditions fail, preparation stops before model calls instead of silently deleting uncertain material. Unsupported overlap shapes, including unverified Chinese rolling text without word boundaries, are retained unless a valid supported annotation exists. These conservative checks intentionally favor retaining duplicates over losing speech.

The known-correct annotated fixture in the deterministic suite distinguishes an actual rolling duplicate from later deliberate repetition. This annotation is engineering source evidence; it is not a new owner semantic-adjudication workflow.

## Unavailable speech and the actual acquisition adapter

`CaptionSnapshot.source_type` is `speech`, `live_chat` or `absent`. Non-speech inputs and the `live_chat` language produce the existing `unavailable` preparation state with `暂无可用字幕，未处理`, and zero generation requests. Empty speech snapshots receive the same explanation. Raw input remains retained privately for provenance; no translation or manuscript is fabricated.

For explicit isolated acquisition, `YtDlpCaptions(speech_only=True)` forwards an opt-in policy to the actual yt-dlp downloader. It filters `live_chat` before language selection. If only chat or no captions are advertised, it stops after the metadata probe with the same unavailable reason, without downloading or converting chat. Deterministic preparation-boundary tests replace only the external yt-dlp transport and model; they exercise the real adapter and real workspace. No speech-recognition fallback is introduced. Omitting `speech_only` keeps the existing production/standalone downloader default.

## Separate the source factor from the mechanism

An explicit `source_policy="historical"` (CLI `--source-policy historical`) preserves legacy cleanup as a separate diagnostic. Its run record has no fixed conservative-source artifact and records a null normalized hash. It still excludes chat and absent speech. This diagnostic must not be mixed into a mechanism comparison against conservative A/B/C inputs. CLI `--source-type` records the source classification; untrusted chat must not be mislabeled as speech.

The source hash, normalization policy and annotated rolling pairs must remain fixed across mechanism variants. The experiment root's ownership marker binds the raw snapshot and its source metadata; a corrected snapshot needs a fresh root. These source tests demonstrate deterministic preservation and isolation, not translation fidelity or successful two-/four-hour processing.
