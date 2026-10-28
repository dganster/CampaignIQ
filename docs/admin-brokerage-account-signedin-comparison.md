# Read-only comparison of published local accounts

The signed-in preview now has a separate `Compare published accounts` section.
Select one or more accessible NEW brokerage accounts, then a month published
by every selected account, and `View account comparison`. Local input test can
be used by itself without creating or uploading another account. Nothing is
selected automatically. Existing legacy workspace accounts remain separate.

Account positions and broker realized subtotals stay separate. An explicit
checkbox optionally combines identical published lot-state instrument quantities.
It does not combine monetary amounts or crypto quantities, compute market
values, infer unknown basis, or reclassify provisional crypto evidence. Forex
settlement subtotals exclude financing and other cash activity. Published crypto
warnings and status remain visible for each applicable account.

The immutable request contains canonical account IDs, scoped draft UUIDs and
displayed source revisions. Duplicate accounts, incorrect types and unsupported
months are rejected before reading. The UI pins those revisions until Reload
comparison sources is clicked. A cached screen is not authorization.

The server checks the current audited directory, server OIDC session, complete
legacy authorization mapping, and permission for the ENTIRE selection. A
directory SHARE lock prevents permission and binding changes during the read.
Every selected draft and financial artifact is read in ONE separate PostgreSQL
REPEATABLE READ READ ONLY snapshot, with scoped expired-on-return sessions and
read-only Python wrappers. Every source revision is checked before loading any
results. Existing published-result verification checks markers, account/registry
receipts, original document hashes, periods and market choices. A failure in any
selected account returns no report or combined totals; no account is omitted.

View grants and archived accounts may read history. Disabled identities,
revocation, foreign drafts and legacy account routing are rejected. No import,
publication, migration, schema change or production connection is enabled by
this comparison.

## Verification

Run the full suite, then:

```
PYTHONPATH=src python -m campaigniq.account_signedin_comparison_rehearsal --development-only
```

The rehearsal uses two NEW synthetic local accounts (110 and 220 shares) and
checks separation, optional 330 total, exact unchanged-row snapshots, access,
stale/foreign selections, blocked Python/raw SQL writes, expired handles, one
concurrent artifact commit without mixed generations, and a conflicting source
receipt withholding the entire report. The synthetic artifact used for injected
changes is restored; existing user data outside that registry must not change.
No production connection is made. Reports are retained under CampaignIQ-Records.

Restart with this kit's resume_preview.py. Expand Compare published accounts,
select Local input test, choose 2025-01, and View account comparison. Expect
110 SPY shares and zero broker realized gain/loss. Combined quantities are off
by default; enabling them with this single account still shows 110 SPY. Your
previous saved local publication is reused; no upload or republishing is needed.
