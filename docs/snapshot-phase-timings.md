# Snapshot preparation phase timings

The artifact-batch capture still showed warm authorized_account_snapshot around 2.4–3.25 seconds, with roughly 1–1.5 seconds outside the existing verification subtimers. This checkpoint adds fixed timing boundaries only. It does not change SQL statements, arguments, source verification, financial formulas, access policy, caching limits, or publication behavior.

New stages:

| Stage | Work included |
| --- | --- |
| snapshot_account_selection | Fresh dashboard account choices and selection authorization path |
| account_data_connection_setup | Entering the existing directory connection, existing runtime role/schema verification, and input-role validation; excludes its yielded body |
| account_data_guard_authorization | Existing directory SHARE lock, actor/account authorization and account routing |
| account_data_session_authorization | Actor/account authorization on the financial connection and storage routing |
| account_data_session_schema | Search path and exact table checks, session construction and readonly-repeatable proof |
| snapshot_continuation_setup | Retained source/frontier validation and continuation adapter setup |
| snapshot_draft_listing | Current source-draft listing |
| snapshot_publication_checks | Draft original checksums and published marker/receipt eligibility checks; count may exceed one |
| snapshot_artifact_assembly | Published-key parity and detached artifact-row selection |
| snapshot_artifact_batch_read | First bounded namespace batch read, including inventory SQL; only on cache miss |
| snapshot_history_source_reads | Queries for current and retained original Trade History bytes |

These stages nest inside existing page and snapshot timers. The continuation stage includes retained-result verification, publication eligibility may trigger batch reads, and connection/authorization stages can occur multiple times per page. Never add overlapping parents and children. The same labels can appear on Data/save/review routes that use this gateway. They contain no SQL text, identifiers, filenames, arguments, result values or exception messages.

ExitStack enters the original connection context inside a setup timer and keeps it open across exactly the same yield. It forwards normal or exceptional exit to that original context. Timers add no queries, polling, background work or new persistence. Existing logging and bounded in-app retention remain unchanged. No speed improvement is claimed by this instrumentation.

Validation covers successful connection entry/exit, validation failure, body failure with exact exception identity, first-batch query count, session phase capture and fixed-label redaction. Existing snapshot reuse, document, binding, dashboard, timing, publication and restricted-role checks remain required. The next production timing capture will determine which phase to optimize.
