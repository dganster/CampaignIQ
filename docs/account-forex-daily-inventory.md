# October daily Forex reports as independent inventory evidence

The Data review now accepts a ZIP of original daily Forex PDFs. October does not require a
monthly Forex statement. The prior optional monthly-reader implementation is retained for
compatibility, but the normal inventory UI uses the daily archive.

The read-only service rechecks the account, draft revision, saved cash-source owner receipt,
original fingerprints and separate Forex binding. Archive paths are labels only: PDFs are
read into generated temporary filenames. Unsafe paths, symlinks, encryption, duplicate entries,
non-PDF files, excessive sizes/counts and invalid signatures are rejected before SQL access.
PDFs are not saved, replaced or adopted by this checkpoint.

Each original report must contain one daily header and an explicit Forex account/currency
panel. Daily layouts can place the cash table to the left of that panel; the identifier is
read only below its labelled column. The service validates cash and account-equity equations,
financing details against each daily total, and exact successive cash/equity boundaries.
The coverage must be a continuous chain of single business-day reports (weekends can bridge
Friday to Monday). The first report must independently corroborate the September closing
position details before any October execution.

Daily execution references cannot duplicate, disappear or change pair, direction, quantity or
rate. Explicit OPEN/CLOSE effects and every daily closing position table must agree with the
position replay. The complete saved October cash execution set must match the daily set.
Daily/export execution clock observations are retained; no timezone conversion is inferred.
The exact existing published predecessor is checked without seeding or changing any opening
lots, basis or campaign identity. FIFO allocations use its existing lot/campaign IDs in memory.

Coverage is explicit, not guessed. A final report before the last business day is the last
independent daily confirmation. Later unchanged cash-export rows are labelled separately.
An archive ending early can reconcile the combined inventory evidence when its complete
execution set matches and its closing cash agrees with the unchanged export tail, while
`full_independent_daily_coverage` and `independent_month_end_positions_verified` remain false.
The review neither invents later reports nor assumes that the broker issued them. Missing
predecessor state remains incomplete; no authoritative lots are created from inferred closes.

Dollar totals from daily session periods and the export's calendar period can differ. The
result retains original CSV findings and explicit settled P/L, financing and opening-cash
comparison deltas. These are diagnostic comparisons, not activated financial source choices.
No monetary exception, financial writer or publication guard is changed.

The UI performs a fresh review and retains no approval/cache. The existing fixed disposable
PostgreSQL rehearsal now also tests synthetic partial/full daily chains through the restricted
input/read-only role, canonical FIFO identity, original/source preservation, stale/foreign
requests, SQL write rejection and conflicting predecessor rejection without repair. Synthetic
originals are generated in the code bundle; no user daily PDFs are included in the installer.

Next work: durable append-only daily originals with owner-attributed receipts, approved period
and monetary source scope, then financial candidate/writer integration. Production source
review and deployment remain separate. Neither a read-only review nor a passing synthetic
rehearsal publishes or financially approves October.
