# Signed-in local account publication

This increment enables explicit publication inside the local campaigniq_dev
PostgreSQL database for NEW Schwab accounts in the signed-in preview. It does
not enable production writes, legacy-workspace publication, replacement, or
historical backfill. The existing production runtime is not changed.

The browser presents `Publish locally` and an explicit local authorization
checkbox. The selected source revision and market choices go to the server;
no cached preview authorizes publication. The server reloads the audited
directory, verifies actual OIDC sign-in and legacy bindings, rechecks account
write access, and holds the directory lock through commit. A namespace advisory
lock serializes publishers; the scoped draft row is locked before checking its
revision. Revocation and binding edits wait for an active transaction.

The existing publication engine is shared with the synthetic rehearsal through
an internal session helper. Its synthetic public entry point retains its exact
service-type and explicit synthetic authorization guards. A separate signed-in
publisher creates a short-lived artifact writer on the guarded local connection.
Ordinary signed-in upload and preview handles remain unable to write artifacts.

Inputs are restored from retained original bytes. Broker identity, complete
input preflight, closing inventory, and Forex source controls must pass. No
reconciliation exception or historical source reseeding is supplied. Existing
month artifacts cannot be overwritten, earlier months cannot be backfilled,
and subsequent publication requires the exact preceding published month.
Artifacts, visibility markers, and a receipt identifying actor, source revision,
original hashes, and market choices commit on one SQL connection. An error,
including a caught SQL error, rolls the transaction back.

Publication status requires the new account's complete marker, lot state and
matching source receipt; the legacy reader's implicit pre-cutover visibility is
not treated as proof of publication. Published originals cannot be replaced.
View and archived accounts can read status and preview, but cannot publish.
This UI is a local workflow preview, not a complete production dashboard.

## Verification

Run the full suite and then:

```
PYTHONPATH=src python -m campaigniq.account_signedin_publication_rehearsal --development-only
```

The SQL rehearsal creates a NEW synthetic registry and publishes January and
February only in its dedicated local account partition. It checks current
sign-in, source revision, binding, isolated namespaces, failed reconciliation,
post-marker rollback, caught SQL errors, concurrent duplicates, source
preservation, independent readers, grant/archive guards, and predecessor rules.
All existing user tables outside that registry must remain unchanged. It keeps
its sources and report under CampaignIQ-Records; it does not contact production.

After a successful rehearsal, restart with this kit's resume_preview.py, which
uses the existing private Google configuration and saved signed-in directory.
Select Local input test / 2025-01, disable Forex and crypto for the stock-only
fixture, preview 110 SPY shares, confirm local authorization, and Publish locally.
The month should display its persistent local publication status after reload.
