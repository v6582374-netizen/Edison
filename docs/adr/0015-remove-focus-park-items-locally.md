# Remove minimalism and retain items only locally

**Status: accepted (2026-10-13).** Supersedes the shipped object-module scope of ADR 0006 and ADR 0007, and narrows ADR 0013's Web instance scope.

The owner has removed minimalism, the focus-task module, from Edison. Its UI, queue code, styles, translations, tests and dedicated design captures are deleted.

items, the object-memory module, is paused. Its source, backend, tests and supporting resources are retained in the ignored `.local/items/` directory, using their original relative paths. The old `minimalism` names within that archive refer to object memory. The active application has no items navigation, image-generation settings, API helpers or object-library endpoints, and does not initialize the object library.

Only tracked source is released. `.local/` must never be copied into a Web release or added to Git. Existing user object data is retained outside the release; this decision does not authorize deleting it or rewriting Git history. Older commits may still contain the former modules.
