# Spec 78 real-provider acceptance

Date: 2026-10-28 (Asia/Shanghai)

This acceptance used the isolated integration checkout `codex/78-upstream-manuscript` at `d21d309`, the pinned upstream translation-agent `e0fc605acbb5d78cb7a58a98bc8bd8f0056df49c`, and the credential-preserving SSH completion boundary. Credentials stayed on the remote host. The production workspace was not opened or mutated.

The retained caption sources were verified against the fixed manifest before running:

| Sample | YouTube id | Duration | Source SHA-256 | Input size observed | Initial translation |
| --- | --- | ---: | --- | ---: | --- |
| short-1 | `RLYq760Jyuo` | 68 s | `b644bfedf760d0caf194cd7efe03eedee4ce4a32a84bf64be252009154780444` | 163 tokens | Full path succeeded |
| long-2 | `nk15CT41MFc` | 7,339 s | `15795e22dbcde5b9b70d9a030513dd5ad1c53da002f573ef37f976d0eda1208d` | 27,247 tokens | 29/29 chunks |
| four-hour-public | `bqeuFiAUU4o` | 15,334 s | `a1de9a9ba3f824fa24d2dc7e510648e874a2a117111116bb4b8eaa6e48cb5bf8` | 62,311 tokens | 65/65 chunks |

## Test protocol

For the two long samples, the run completed the native upstream initial-translation stage only. Reflection and revision were deliberately skipped. The saved initial chunks were concatenated once and sent as the complete context to the `whole-markdown-v1` composition prompt. No split composition, summary generation, alternate model, semantic comparison, or numeric/length gate was used.

The direct composition results were:

| Sample | Initial chunks / chars | Composition request count | Finish | Elapsed | Output chars | Provider usage (input / output) | Saved artifact |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |
| long-2 | 29 / 41,582 | 1 | `stop` | 184.54 s | 43,166 | 26,874 / 27,926 | [composition-long-2.md](/tmp/edison-spec78-real-long.x8IxLM/composition-long-2.md) |
| four-hour-public | 65 / 106,040 | 1 | `stop` | 135.65 s | 25,988 | 69,793 / 16,778 | [composition-four-hour-public.md](/tmp/edison-spec78-real-four-hour.m1ba2L/composition-four-hour-public.md) |

The long-sample inputs and outputs were also recorded with SHA-256 metadata:

- `long-2` input `f129a07da04854768d83b6958ef5a9341caad7115a05c3e3fb5e6e196ea92897`; output `ecf069afbc510032f0c7731067937c290b259fbc43abdaba5ed064bbf12775b3`.
- `four-hour-public` input `ed7df0d1cc3c8920e4a9a372e0d004b7be9755f38669eeb15dbf6134e94cd4e9`; output `0bba7c9676c0a02d86f39656c7cd7e2d807404643c9e7a596b820a97eea7923d`.

## Short sample

The complete product path finished in approximately 86.5 seconds. Four remote wire requests were observed: three upstream translation requests and one whole-manuscript request. Every response ended with `stop`. Reported usage totalled 3,047 input tokens and 1,218 output tokens. Translation v1 (326 characters) and Markdown manuscript v1 (404 characters) were persisted in the isolated workspace; the workspace ended in `ready`.

This sample also exercised the product's saved-result path. The long-sample capacity probe intentionally wrote the direct composition artifacts and metadata to the isolated `/tmp` workspaces; it did not claim a UI copy/download event because the question under test was whether the complete initial translation could be accepted as one model context.

## Recovery observation

The approximately four-hour initial-stage run encountered one temporary provider-unavailable response after 23 initial checkpoints. Edison retained the checkpoint as `waiting_retry`; a resumed isolated run continued and completed all 65 initial chunks. The outage was not converted into a success signal. This demonstrates checkpoint recovery independently of the direct composition result.

## Independent production switch evaluation

| Gate | Result | Evidence and remaining boundary |
| --- | --- | --- |
| One-request capacity for approximately two-hour and four-hour initial translations | PASS | Both direct composition requests returned `stop` and produced saved artifacts with provider usage. |
| Initial-stage checkpoint recovery | PASS | The four-hour run recovered from one temporary provider-unavailable response and completed all 65 initial chunks. |
| Product persistence and version binding for the long samples | PARTIAL | The short complete path persisted translation and manuscript versions. The long capacity probe saved files and metadata in isolated workspaces, but did not rerun the product Web save/copy/download controls. |
| Semantic/editorial completeness | NOT ASSESSED | No source-coverage, factual, numeric, length, or human editorial review was performed. |

The capacity gate is therefore satisfied, but this evidence does not authorize an automatic production cutover. A reviewer should inspect the two saved long artifacts and perform one product-level staging run covering version binding plus copy/download before switching the production path. Any provider failure in that run must remain a failed manuscript with the initial translation retained.

## Acceptance conclusion

The real provider accepted the complete concatenated initial translations for both the approximately two-hour and approximately four-hour samples as one `whole-markdown-v1` context. Both requests returned `finish_reason=stop`, produced saved Markdown artifacts, and reported provider usage. This answers the capacity question posed by this test: the initial translations at these observed sizes can be passed directly to the composition model in one request.

This is a capacity and delivery observation, not a semantic quality certification. No source-coverage, completeness, length, numeric, or factual comparison was performed, and the returned Markdown was not declared editorially complete solely because the provider returned `stop`. Production cutover still requires an independent review of the generated artifacts and the product's save/copy/download behavior under the intended deployment environment.
