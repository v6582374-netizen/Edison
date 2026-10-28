# Translation-agent in Edison

Upstream: https://github.com/andrewyng/translation-agent

Pinned commit: `e0fc605acbb5d78cb7a58a98bc8bd8f0056df49c`, MIT, Andrew Ng 2024.

## Native ownership

Edison's preparation service calls `translation_agent.translate()` from `src/translation_agent`. The original functions own initial translation, reflection, revision, token counting, chunk sizing, full-source context and concatenation. Edison supplies its existing model router through a context-local completion binding. The desktop does not start an upstream server or create another provider client.

| Upstream | Integrated location / contract |
| --- | --- |
| `src/translation_agent` | Same package under Edison's `src/`; public function names, signatures, defaults and plain-text return contract retained |
| `app/{app,process,patch}.py` | `src/translation_agent/webui/`; original optional UI and document/diff operations retained, only relative imports changed |
| `examples/` | `examples/translation/`; scripts and sample texts unchanged |
| Complete tracked tree, tests, docs and metadata | `source.tar.gz`, verified against every file hash in `UPSTREAM.json` |
| MIT license | `src/translation_agent/LICENSE`, included in the wheel and desktop bundle |

The archive is a provenance baseline, not runtime code or an independent project. No nested Git repository or root clone is required. The host's wheel, dependency lock and desktop packager own the live implementation.

`EDISON.patch` records the core changes: lazy standalone SDK construction and six completion-boundary hooks. The new `runtime.py` restores context on exit and isolates concurrent jobs. `prompts.json` contains the original three system templates and eight task-template variants; the desktop settings add two composition templates. With defaults, already-rendered upstream prompts pass through unchanged. Overrides use the original variables and plain-text contract.

## Compatibility entry points

- Python: `from translation_agent import translate`; original signature and default OpenAI behavior outside Edison remain available.
- Example: `uv run python examples/translation/example_script.py` (requires a usable standalone OpenAI key).
- Original optional UI: `sh scripts/translation-ui.sh`. This launches the relocated module using an isolated dependency environment: upstream Gradio 4.37.2 requires websockets <12, while Edison requires >=13. It is a developer compatibility entry, not a desktop service or separate checkout. Its provider selector, document import/export, comparison and cancellation behavior remain in the original source. It does not replace Edison's model settings.

## Explicit host additions and limits

The owner authorized Chinese conversion/bypass, editable prompts, shared model configuration, durable checkpoints, resource budgets and separate manuscript composition. These are host policies around the retained library, not upstream algorithm changes. Legacy JSON-protocol templates are archived and legacy checkpoints require explicit regeneration. No silent continuation under a different contract occurs.

Tests compare original function bodies (excluding only completion hooks), request sequences, signatures and results for short/long and regional/nonregional flows. They also verify custom-template dispatch, concurrent context isolation, preservation hashes and optional UI source parity. Deterministic fixtures establish integration behavior, not translation quality.

## Edison downstream contract (2026-10)

Edison invokes the pinned upstream public entry with upstream prompts, native segmentation, reflection, revision and concatenation. The host does not bypass Chinese input, replace templates, apply content completeness guards, impose cumulative per-video budgets, or split the returned translation for composition. The exact non-empty translation is retained as its own versioned asset before any downstream manuscript work; the existing historical settings and checkpoints remain archived for compatibility and are not reused by new tasks.
