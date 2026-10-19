# EPUB Reader investigation

2026-10-13. Product boundaries and the investigation protocol were confirmed by the user. Client investigation and the controlled EPUB check are complete for this scope. Reader implementation has not begun.

The resulting [research report](../research/readwise-reader/README.md), [runtime verification](../research/readwise-reader/runtime-verification.md), [source/evidence index](../research/readwise-reader/evidence-index.json) and [phased implementation proposal](../research/readwise-reader/implementation-plan.md) are the current handoff. Server-side parsing and synchronization internals remain unobserved; these are explicit limits of the result.

## Confirmed scope

Build toward an independent EPUB reading product inside Edison. The navigation entry opens a full reading view in the main area. Source files and durable reading records belong to the owner's Edison server; first-release acceptance covers the current computer.

The first-release loop is import → Inbox/Later/Archive → read and resume → highlight and annotate → search and export. Reflow must not invalidate the reading position or the passage associated with a highlight. Only deliberately imported EPUBs enter this release. PDF, prior Readwise-data migration, YouTube/classroom integration, offline operation and cross-device acceptance are deferred.

The current delivery is research evidence, the domain model and a phased implementation plan. It does not include implementing or deploying the Reader module. Reading behavior and key interactions are studied faithfully; visual design follows Edison.

## Accepted investigation protocol

1. Verify the published REA version and supported local runtime. Use its official JavaScript/native/browser analysis surfaces as appropriate; do not infer package contents from a guessed framework.
2. Obtain Reader's official desktop package and record release identity and artifact hash. Identify its packaging, usable front-end assets and source maps before selecting the next analysis surface.
3. Use the existing Zen session for interaction reference. REA's browser capture requires a supported Chrome-family/CDP context; the existing Firefox-based session is not presumed directly attachable.
4. Follow the EPUB feature boundaries: import and identity; spine/TOC/body/images/footnotes; reflow and appearance; reading-position restoration; passage selection and annotation anchors; notebook/navigation; organization; search and export.
5. Compare static client findings with controlled behavior checks using identifiable test EPUBs. Distinguish observed product behavior, code-supported mechanisms, hypotheses and mechanisms that remain unknown. Do not claim to recover unobserved server implementation.
6. Deliver a feature/evidence map, the domain model, unresolved risks, a minimal architecture proposal and phased tasks with observable acceptance scenarios. Public APIs can inform data semantics without becoming a required dependency of the independent product.

## Proposed acceptance probes for the later implementation

- Import an EPUB containing Chinese/English text, multiple chapters, a TOC, images and footnotes; verify content order and navigation.
- Resume the same logical reading location after a reload, browser restart, font-size change and width change.
- Create highlights and notes across representative passages; after reflow, verify their original text association and notebook-to-text navigation.
- Move a book between Inbox, Later and Archive without losing its reading records.
- Search books and reading records, and export them with sufficient source identity to remain useful outside the interface. Exact search and export contracts will be proposed in the research results.
- Preserve the original EPUB and durable reading records after browser-cache removal; the server is the source of durable data.

## Decision record and vocabulary

See [ADR 0016](../adr/0016-independent-epub-reader.md) and the Reader section of [CONTEXT.md](../../CONTEXT.md). Existing YouTube reading-state terms must not silently become Reader's classification or reading-position model.

## Official references

- [REA Chinese README](https://github.com/morluto/rea/blob/main/README_zh.md)
- [REA application workflow](https://github.com/morluto/rea/blob/main/docs/javascript-application-workflows.md)
- [REA browser observation](https://github.com/morluto/rea/blob/main/docs/browser-observation.md)
- [REA CLI](https://github.com/morluto/rea/blob/main/docs/cli.md)
- [Readwise desktop releases](https://github.com/readwiseio/reader-desktop-releases)
- [Reader API](https://readwise.io/reader_api)

The public desktop repository distributes releases. Inspection of the official 0.1.3293 package confirmed native Tauri markers and a localhost asset service containing JavaScript and source maps with sourcesContent. Those mappings support the client findings; they are not a complete original repository or server source release. Release identity, hashes and structural coverage limits are recorded in the research report.
