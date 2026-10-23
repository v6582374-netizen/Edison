# Gate code rollback on a declared library data contract

**Status: accepted (2026-10-22); exercised in the isolated drill only, not on production.**

ADR 0018 lets code roll back only to a program verified compatible with the latest library. Each program now declares one integer, `YOUTUBE_DATA_CONTRACT`, and the library records the highest contract that has opened it without ever lowering it. A rollback target is admitted only when its declared contract covers the library's; a program that declares none — every release before this decision — is refused rather than guessed. If an older program is started on a newer library anyway, it holds itself read-only: reading continues, acquisition and connection changes stay stopped, and it never migrates or backs up that library.

We chose a declared contract over inferring compatibility from columns or test-running old releases on a copy. Additive columns are ignored safely by older code, but meaning can change without any schema change (the cutover hold and disconnection tombstone are plain metadata), so only the author of a change can say an earlier program would act against newer intent. The cost is discipline: a change that an earlier program could misread must bump the contract. Static Web asset promotion remains a separate act and never counts as backend or state rollback.

The consistent backup is disaster recovery, not rollback: it contains the database and knowledge files only, is taken inside the cutover hold after writers are proven stopped, and restores only into an empty directory with acquisition held. See [YouTube backup and rollback](../architecture/youtube-backup-and-rollback.md).
