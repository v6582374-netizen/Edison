# Experimental consumption and proxy evidence

[取消实验中的本地消费上限，验证真实代理行为](https://github.com/v6582374-netizen/Edison/issues/71) changes the isolated experiment only. Main A execution supplies `enforce_local_budgets=False` to the existing `HostManuscripts`/`TranslationPipeline` adapter. Future B/C implementations must share this execution policy. Cumulative counters are still recorded, but cannot terminate or discard normal work, including when an old checkpoint contains low budget settings. `historical_diagnostic=True` explicitly selects the separately labeled historical behavior. Production callers retain their default policy, single-request output limits and bounded request retries.

## Observable accounting

Private request records retain actual model attempts, observed preparation stage, output allocation, response/finish reason and provider-reported fresh/cache/output token fields. Unknown usage and cost remain unknown. `estimated_input_token_bound` is UTF-8 bytes plus 32 per request, a conservative size bound rather than token consumption. `stage_request_seconds` sums observed model round trips per stage; it excludes local work and must not be labeled total stage latency. The legacy `budget_tokens` remains a mixed historical counter, not a measured usage total.

## Exact quota contracts

No default proxy quota contract is assumed. A `QuotaContract` must bind the current credential-free configuration SHA-256, exact HTTP status, exact structured `body.error.code` (or top-level `body.code`) and an evidence reference, and be explicitly marked verified. A mismatched configuration fails before the experiment writes. Error-message substring matches and HTTP status alone never confirm exhaustion.

Only a verified exact contract match discards the unfinished generation checkpoint and its cached intermediate outputs. Source captions and all successful historical translations/manuscripts remain retained; private request evidence remains available for diagnosis. Generic HTTP 402 stops with a truthful unconfirmed-exhaustion reason and preserves progress. Rate limits/authentication and transient failures retain the existing account waiting/retry representation, preserving progress. Requests still have at most three attempts for transient failure. The runner starts no automatic retry loop.

Synthetic tests verify these classification/discard rules, not a real service contract. A configured proxy's quota signal remains unverified until supported by a documented or redacted observed contract. Never provoke exhaustion to obtain evidence; never apply another vendor's `insufficient_quota` semantics to an arbitrary compatible proxy without verification.

## Explicit bounded proxy probe

`probe_provider(root, ModelIdentity(...), complete, ...)` accepts the existing host completion signature and uses only synthetic text. It makes three explicit requests: a marker after a configurable input, a deliberately small output allocation (8 tokens), and a larger allocation (default 256). `wire_requests` can be populated by a real transport hook with model/output-limit/timeout metadata only. The probe records only observed sizes and never infers maximum model capacity.

The CLI uses an explicit OpenAI-compatible endpoint and key from `EDISON_EXPERIMENT_PROXY_URL` and `EDISON_EXPERIMENT_API_KEY`. It does not search or migrate production credentials. Keep existing credentials in their current environment; do not paste them into commands or records. Supply a credential-free config identity, model and proxy build identity:

```sh
PYTHONPATH=src:. python -m youtube_strataread.workbench.experiment_provider \
  --root .local/translation-provider-probes/current \
  --model configured-model --proxy-version configured-proxy-build \
  --configuration-sha256 REDACTED_CONFIGURATION_SHA256 --execute
```

Without `--execute`, a callback, or the required environment credentials, it records an exact blocker and returns `unverified` (CLI exit 2), making no model requests. Output roots use the experiment's production-path/link protection and must be new or probe-owned. The CLI records a strict allowlist of outbound body fields through HTTP transport instrumentation; endpoints, headers, keys and raw provider errors are excluded.

Capabilities remain separate: parameter forwarding requires transport evidence; effective output allocation additionally requires short-output length termination and a larger response at the higher allocation; finish reason and usage require actual reported fields. Timeout semantics remain unverified unless a timeout is observed. Quota semantics are never induced by this probe. Simulated callbacks always remain unverified. Even a real `state=observed` result can have individual unverified capabilities; inspect those fields before selecting output limits or claiming an effective provider contract. Successful small probes do not establish long-input/output capacity.
