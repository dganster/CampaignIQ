# Published local account results

A new `View published results` button appears for a published saved month in
Monthly inputs for new brokerage accounts. The existing account and saved-month
selectors scope the report; no additional account navigation is required.

The service checks current server sign-in, audited directory and storage scope,
holds directory and source draft locks, and reads the generation using a
separate PostgreSQL REPEATABLE READ READ ONLY transaction. The same publication
status verifier is shared with the publisher's status reader. Source revision,
marker, lot-state period, account/registry receipt, and retained original hashes
must match. Monetary artifacts and market choices must match the selected month.
It returns only frozen summaries; database handles expire before return. A stale,
foreign, incomplete or conflicting report returns no partial result.

The report shows closing quantities, individual lots with known/unknown source
basis and campaign references, and broker realized rows and subtotal. Forex
settlement P/L has its own subtotal and explicitly excludes financing and other
cash activity. Crypto quantities retain the published evidence status and
warnings. It does not combine these into a portfolio profit or market valuation,
allocate unknown basis, rerun the import, or change a financial record.

View grants and archived accounts may read history. Disabled users, revoked
access, wrong issuers, foreign drafts, and stale revisions remain blocked.
Ordinary upload/preview transactions still cannot write artifacts. The live
runtime, legacy workspaces, production database, and existing saved local
publication are unchanged by installation.

## Verify

Run the full suite, then:

```
PYTHONPATH=src python -m campaigniq.account_signedin_results_rehearsal --development-only
```

This rehearsal creates two NEW synthetic local accounts and generations (110
and 220 shares). It checks independent readers, account isolation, immutable
read-only snapshots, unchanged rows, access controls, blocked Python/raw-SQL
writes, expired handles, source fingerprint conflicts and wrong-period monetary
artifacts. Existing user tables outside that registry must remain unchanged.
Reports are retained under CampaignIQ-Records/Development-Rehearsals.

Restart using this kit's resume_preview.py. In the existing saved signed-in
preview select Local input test, saved month 2025-01, and View published results.
Expect 110 SPY shares, four verified original documents, and no broker realized
records. No new upload, reconciliation, or publication is needed.
