# Account monthly input preview (local development)

This increment connects the synthetic account selector to saved monthly drafts
and the existing monthly input validator. It does not activate multi-account
production storage, execute imports, reconcile closing inventory, or publish.

Run the full suite and the explicit account_import_preview_rehearsal runner.
Then launch with:

    PYTHONPATH=src python -m campaigniq.account_import_preview_ui --development-only

The launcher uses only local campaigniq_dev and loopback port 8503. It creates a
new synthetic registry, or accepts --resume with the private preview-config.json
path printed by this launcher or the previous administration preview launcher.
The administration preview on port 8502 can use the same --resume file for
sharing, archive and revocation tests. No real sign-in is bypassed; this is an
explicitly synthetic demo-user switch.

Choose a demo user, authorized account, and month. Create or resume a draft,
choose a document role, save a synthetic document, and preview saved inputs.
Missing/invalid evidence should appear as validation findings. These demo
accounts have no migrated preceding authoritative history, so an opening-state
finding is normal. Use only synthetic fixtures for this prototype. Broker
account-number binding to the new account registry has not yet been implemented;
input readiness must not be treated as a successful account identity check.
The existing supported parsers are Schwab/thinkorswim; a free-text broker label
does not establish support for a different broker's statements.

Permissions are rechecked by the guarded account gateway on every operation.
A directory share lock serializes administrative revocation against active work.
Draft row locks serialize uploads against a preview and check the displayed
revision before editing. View grants and archived accounts can preview; they
cannot create drafts or upload. Disabled users cannot access accounts.
Foreign draft identifiers cannot retrieve another account's evidence. UI widget
keys include registry, user, account, draft and revision; previews are not cached
or persisted in browser session state across account/user changes.

The service reuses restore_uploads and prepare_monthly_import. A pure staging
module mirrors the existing dashboard extraction logic without importing the
dashboard. A structural regression test checks that extraction logic remains
equivalent. It supplies only the selected account's
artifact adapter and keeps extracted files in a private temporary directory,
removed on success or error. Only immutable validation summaries escape the
transaction. Original documents remain in the local scoped draft repository;
confirmed replacements retain superseded original bytes. The execution engine,
live runtime builder and publication entry points are never called. Failed SQL
continues to force the gateway's outer transaction to roll back.

The preview-staging fix backs up the previous development files before updating
them; the original dashboard is unchanged. Original production runtime
hashes, source fixtures, financial records and historical recovery materials are
preserved. The local SQL runner retains synthetic rows and a report under
CampaignIQ-Records/Development-Rehearsals. No data is reset or deleted.
