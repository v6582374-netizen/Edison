# Spec 78 finite acceptance record

Date: 2026-10-28 (Asia/Shanghai)  
Integration candidate: `codex/78-upstream-manuscript` at `fc2bcc7`  
Upstream translation-agent: `e0fc605acbb5d78cb7a58a98bc8bd8f0056df49c`  
Composition rules: `whole-markdown-v1`  
Automatic composition allowance: three actual outbound requests, including the first

This record fixes the isolated sample set and the operational evidence to collect. It does not publish source captions, model responses, credentials, or semantic judgements.

| Sample | YouTube id | Duration | Retained source SHA-256 |
| --- | --- | ---: | --- |
| short-1 | `RLYq760Jyuo` | 68 s | `b644bfedf760d0caf194cd7efe03eedee4ce4a32a84bf64be252009154780444` |
| long-2 | `nk15CT41MFc` | 7,339 s | `15795e22dbcde5b9b70d9a030513dd5ad1c53da002f573ef37f976d0eda1208d` |
| four-hour-public | `bqeuFiAUU4o` | 15,334 s | `a1de9a9ba3f824fa24d2dc7e510648e874a2a117111116bb4b8eaa6e48cb5bf8` |

The deterministic contract suite passed before this acceptance record: translation delivery, whole composition, recovery, library and updated upstream workflow tests. It substitutes only captions and model responses. The Web path is exercised through the authenticated capability API and the existing GUI document controls; copy/download assertions use returned bytes and filenames.

The finite real-provider run was **not executed in this implementation turn**. No credentials, network calls or production workspace were used. Therefore whole-output capacity, elapsed time, provider finish metadata and usage remain unknown. The isolated runner must invoke the original upstream public entry, preserve the complete translation before composition, record non-secret source/translation/rule/model identities, request ordinals, finish/error signals, persistence, copy/download outcomes and elapsed/usage metadata. It must report a failed long manuscript as a failed manuscript while retaining the translation. It must not run the historical A/B/C campaign, split or summarize output, or compare translation and manuscript content.

Run the no-call plan with `python scripts/spec78_acceptance.py`; it verifies only the three immutable source hashes and prints the bounded plan. A real run requires an explicit isolated workspace and a separate `EDISON_SPEC78_ALLOW_REAL=1` acknowledgement after review. `--real` by itself remains blocked.

This record is operational evidence and a capacity limitation record, not semantic completeness certification and not production deployment approval.
