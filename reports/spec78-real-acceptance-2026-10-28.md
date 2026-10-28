# Spec 78 real-provider acceptance

Date: 2026-10-28 (Asia/Shanghai)

This run used the isolated integration checkout `codex/78-upstream-manuscript` at `d21d309`, the pinned upstream translation-agent `e0fc605acbb5d78cb7a58a98bc8bd8f0056df49c`, and the existing credential-preserving SSH completion boundary. Credentials stayed on the remote host. The production workspace was not opened or mutated.

The retained caption sources were verified against the fixed manifest before running:

| Sample | YouTube id | Duration | Source SHA-256 | Input size observed | Result |
| --- | --- | ---: | --- | ---: | --- |
| short-1 | `RLYq760Jyuo` | 68 s | `b644bfedf760d0caf194cd7efe03eedee4ce4a32a84bf64be252009154780444` | 163 tokens | Full path succeeded |
| long-2 | `nk15CT41MFc` | 7,339 s | `15795e22dbcde5b9b70d9a030513dd5ad1c53da002f573ef37f976d0eda1208d` | 27,247 tokens | All 29 initial chunks completed; later stages stopped |
| four-hour-public | `bqeuFiAUU4o` | 15,334 s | `a1de9a9ba3f824fa24d2dc7e510648e874a2a117111116bb4b8eaa6e48cb5bf8` | 62,311 tokens | 65 initial chunks completed; one temporary provider outage recovered from checkpoint; later stages stopped |

## Short sample

The complete path finished in approximately 86.5 seconds. Four remote wire requests were observed: three upstream translation requests and one whole-manuscript request. Every response ended with `stop`. Reported usage totalled 3,047 input tokens and 1,218 output tokens. The exact translation was persisted as version 1 (326 characters) and the Markdown manuscript as version 1 (404 characters); the isolated workspace ended in `ready`.

This is the only sample in this run for which a complete reading manuscript was delivered. It verifies operational delivery for a short input, not long-output capacity.

## Approximately two-hour sample

The upstream workflow observed 27,247 input tokens and completed all 29 native initial-translation chunks. The durable checkpoint reached 52 Edison completion calls and 1,922,570 accounted tokens, with 20 reflection checkpoints also completed before the run was stopped. No translation or manuscript was published because the run was intentionally stopped before the remaining upstream stages and whole-manuscript composition.

The checkpoint was left in the isolated workspace `/tmp/edison-spec78-real-long.x8IxLM`. Its state is `generating`; no failure was relabelled as success. The persisted counts are Edison request/accounting data. Exact remote wire telemetry for this interrupted process was not retained after the runner was stopped, so provider-level request count and reported usage for this sample remain unknown.

## Approximately four-hour sample

The upstream workflow observed 62,311 input tokens. It completed 65 native initial-translation chunks. The first attempt encountered a temporary provider-unavailable response after 23 initial checkpoints; Edison retained the checkpoint as `waiting_retry` and did not discard the work. A resumed isolated run continued from the checkpoint. At the point the run was stopped, the checkpoint contained 71 Edison completion calls, 5,759,656 accounted tokens, 65 initial checkpoints and two reflection checkpoints. No translation or manuscript was published because later stages and whole-manuscript composition were intentionally stopped.

The checkpoint was left in the isolated workspace `/tmp/edison-spec78-real-four-hour.m1ba2L`. The provider outage and recovery demonstrate durable retry behavior. Exact provider-level wire request count and provider-reported usage for this interrupted process remain unknown because the runner was stopped before its in-memory wire telemetry was emitted.

## Acceptance conclusion

The real provider accepted the short input through the complete upstream path and delivered a saved translation plus a saved whole Markdown manuscript. The two long inputs also accepted substantial native upstream workloads, including all initial chunks for the tested source sizes, but they did not complete the reflection/revision/whole-manuscript path in this run. Therefore:

- short-input operational delivery is demonstrated;
- long-input upstream input handling and checkpoint recovery are demonstrated;
- two-hour and four-hour whole-manuscript delivery remains unknown;
- no semantic, length, numeric or source-coverage comparison was performed;
- no split, summary or alternate-model fallback was used;
- no production cutover is authorized by this evidence.

The next production decision requires a separately authorized run that completes the long-input upstream and whole-manuscript stages, with provider-level request telemetry and usage persisted outside the runner process.
