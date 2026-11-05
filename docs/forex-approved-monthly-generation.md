# Approved daily Forex monthly generation

This checkpoint connects the saved owner monetary approval to actual account monthly publication. It supports the exact October 2026 daily-report policy already reviewed. An October Forex monthly statement is not required.

The existing publication capability and separate month confirmation remain required. The source review, daily originals, owner monetary approval and preceding published lot state are rebuilt inside the serialized publication transaction. An approved Forex month cannot be published with Forex deselected. There is no configuration change, role elevation, deployment or production publication in this development installer.

## Financial and inventory behavior

Only the Forex ledger section of a temporary history projection is replaced with the exact approved cash execution rows. Futures use their existing separate projection. The complete original history, original transaction CSV, daily PDFs, source receipts and approval remain unchanged. Both projections are bound by hashes in the saved generation.

The Forex replay consumes the canonical preceding lots and carries their campaign and lot identity. The ordinary securities path is unchanged. Each approved close must match an exact execution reference, time, pair, quantity, rate and FIFO allocation; the supported policy requires the generated ending Forex book to be flat. Missing original settlement dates remain absent. The daily reporting-session monetary result is retained separately from the original CSV control discrepancies.

The publication saves the normal complete generation plus `YYYY-MM-forex-approved-generation.json`. The publication receipt binds that record to the original owner approval and canonical financial, lot and journal files. The record contains approved opening cash, trading result, financing, ending cash and net cash change. Existing trade-only totals keep their definition. Published results and dashboard summaries expose approved financing and a separately labelled result including financing. Crypto remains separate from brokerage totals.

## Atomic publication and independent reads

Forex-only publication uses the same buffered generation approach as futures and crypto. The complete result is checked before any generation insert, flushed with the final visibility marker last, and checked again before transaction completion. Futures, Forex and approved crypto can commit together in one generation.

An authorized independent reader rebuilds the expected Forex attribution, complete lot state, position journal and source-resolution audit from the saved original documents and approval in one read-only transaction. It compares those outputs with the committed generation. A coherent rewrite of journal contents, hashes and receipt is still withheld rather than repaired. Ordinary reports without this policy retain their existing behavior.

## Verification and release boundary

Unit coverage includes successful ordinary and combined publication, capability/scope/revision/confirmation checks, failure after each component, corrupt and missing descriptors, coherent monetary and journal tampering, dashboard financing and a following month preserving the October generation.

The existing `forex_cash_sources` administrator rehearsal now also exercises both actual publication routes on new synthetic accounts in its disposable PostgreSQL clone: restricted writer rollback, simultaneous duplicate publication, repeatable reader isolation, independent source rebuilding, read-only SQL, revoked viewing access, coherent journal tampering and following-month publication. Its synthetic predecessor fixtures are inserted only in the disposable clone. The existing retained-restore and original financial-data comparisons still run afterward.

Run the installer from clean development commit `82c34c2`. It runs the full local suite and then the extended disposable PostgreSQL rehearsal. A local PASS is required before release review; installing this package does not release the code or publish the real October account.
