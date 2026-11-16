# Consolidated Data page display

Continued deployment accounts now read the selected account's input details,
earlier retained published-month list and selected saved draft display in one
fresh guarded read-only repeatable snapshot. The gateway still uses its separate
guard connection and financial read connection. The account selector remains
independently authorized, and no connection is held during UI interaction.

The earlier-month list uses only the continuation session's verified retained
artifact rows, preserving its existing scope. Selecting a month still checks
originals, receipts and canonical published results. Results buttons and every
save, recovery, review, confirmation and publication action retain independent
authorization and revision checks. Invalid saved selections require explicit
reload. Local and older service interfaces retain their existing read paths.

The expected Data setup count falls from five to two; this does not include the
separately timed account selector or startup role checks. Actual latency must be
measured after release. The data_page operation is recorded by the existing
bounded sanitized performance monitor.
