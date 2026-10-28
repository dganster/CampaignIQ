# Saved results for retained existing accounts

Existing account published results adds a read-only financial view below the
existing brokerage account selector. It uses the retained LOCAL workspace copy,
not a production connection. Production updates made since that copy was taken
may not be present. It neither imports data nor reruns reconciliation.

Only server-authenticated, currently authorized legacy accounts may be read.
The existing LocalLegacyAccountAccess service retains the exact original
workspace namespace and public tables, checks the complete issuer-bound legacy
mapping, and uses its existing REPEATABLE READ READ ONLY transaction. The new
report compares direct source rows with the existing artifact adapter in that
same snapshot. No names or browser workspace values establish data ownership.

The month list requires an actual saved lot-state artifact and preserves the
existing publication protocol, including legitimate pre-cutover visibility.
Protected months without markers are omitted; malformed markers fail closed.
Selected lot-state and realized-record periods must match. No partial report
is returned when a required financial artifact is absent or inconsistent.

The view shows saved closing quantities, lots with known/unknown source basis,
broker realized records and subtotal, Forex settlement subtotal when retained,
and crypto evidence status/warnings. Missing Forex coverage is explicitly
unknown, never a fabricated zero. Forex settlement P/L excludes financing and
other cash activity. No portfolio return, market value, consolidated profit,
new basis allocation or financial approval is inferred.

View grants, disabled users, archived history and revocation retain the existing
account policy. Admin status alone grants no financial access. New account
namespaces are not routed through this reader. Existing dashboards, financial
records, original document versions and all live production settings remain
unchanged.

## Verify

Run the full suite, then:

```
PYTHONPATH=src python -m campaigniq.account_legacy_results_rehearsal --development-only
```

The rehearsal creates NEW synthetic legacy workspaces and canonical saved
results with 110 and 220 shares. It checks exact namespace isolation, source
adapter parity, read-only snapshots, no changes from reads, owner/grant guards,
blocked writes, expired handles, missing Forex coverage and wrong-period source
rejection. Injected changes to those new test workspaces are restored; all
pre-existing rows outside the test registry/workspaces must remain unchanged.
It does not contact production or alter the user's existing workspace copy.

Restart with this kit's resume_preview.py. Under Existing brokerage account,
expand Existing account published results, select an available local month and
click View existing published results. The data source remains the local copy;
there is no need to upload, republish or recreate any existing account data.
