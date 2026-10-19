# Manual video requests

The fourth YouTube navigation destination, Add video, accepts one explicit video URL. It adapts the centered 560px single-decision flow in Vegapunk's [TranslationView.tsx at e999006b2aedf1775cb2a2cc703d93a9b2966217](https://github.com/v6582374-netizen/Vegapunk/blob/e999006b2aedf1775cb2a2cc703d93a9b2966217/desktop/openworker/upstream/surfaces/gui/src/components/TranslationView.tsx). The adaptation keeps the narrow column, one input and one primary action; preparation moves into the existing Progress destination. PDF controls and translation step selection do not apply to a video request.

A request normalizes watch, short-link, live, embed and Shorts URLs to the existing video identity. The official API verifies a previously unknown video before admission. An existing local identity is resolved first, including while Google is unavailable. A saved manuscript opens directly; another paste never regenerates it.

The SQLite transaction commits identity, explicit execution permission and a request receipt together. Discovery and concurrent submissions merge into one preparation record. Manual permission exempts that video from subscription scope, channel exclusion, age admission and Auto Update pause; shared completion, Shorts, caption availability, environment, retry and version guards still apply. Permission persists across restarts. Cancellation ends execution intent; dedicated Restore and Retry actions grant it again explicitly. Pasting a cancelled or failed video preserves its state and budgets.

The browser stores only the pending URL and request identifier in session storage. Transport ambiguity triggers a durable receipt lookup; failed confirmation remains visible across reload, with explicit lookup or idempotent resend. Confirmed validation failures retain the input and create no offline inbox. Receipts survive video deletion so replay cannot resurrect a deleted record.

Manual live or unverified timing uses the existing discovery scheduler, at most ten official checks per five-minute cycle, independently of Auto Update. External checks cannot revive a video cancelled or deleted in flight.

Verification lives in `tests/test_youtube_manual.py` and `surfaces/gui/e2e/youtube-manual.spec.ts`. Browser tests run actual handlers, workers and storage, including eager Web-host restart with the browser closed. Only external YouTube, caption, model and clock boundaries are replaced; synthetic credentials and temporary libraries are isolated per test.
