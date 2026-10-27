# Signed-in existing-account access (local development)

The separate signed-in preview now calls the account identity bridge using
Streamlit's server-owned `st.user`. It loads the audited directory again for
each account selection or inventory verification. There is no user impersonation
selector. An administrator receives financial access only through ownership or
an explicit grant. Disabled users are blocked; disabling another owner does not
erase that owner's mapping or block unrelated owners. Archived history remains
readable to authorized users.

## Existing data

The reader uses the original `public.artifact_storage_dev`, `public.import_drafts`
and `public.import_draft_documents` tables and unchanged workspace namespaces.
It does not route legacy accounts into the synthetic preview partitions. It
creates no financial tables, copies no data and supplies no write/publication
API. It compares the existing artifact/draft/document adapters with direct
source rows, validates every original document's SHA256, and fingerprints
abandoned drafts and superseded versions as well as active records.

Each operation uses one guarded local PostgreSQL REPEATABLE READ READ ONLY
transaction containing both directory/audit validation and financial reads.
Python handles reject mutations and expire at transaction exit; PostgreSQL also
rejects SQL writes. Revocations apply on the next transaction. A transaction
already running retains its consistent snapshot; this is not instantaneous
revocation of an in-flight read. No results or identities are cached in the UI.

## Local preparation and activation

Installation does not prepare a directory or start a server. The opt-in rehearsal
adds only new synthetic directory entries and unique local workspace rows; it
checks all prior user-table rows remain unchanged. Its fake server-session
objects are test-only. It does not test an external OIDC provider callback.

For a real local sign-in test, first review the existing trusted server
`CAMPAIGNIQ_WORKSPACE_AUTHORIZATION` mapping and the exact issuer and administrator
subject. Do not paste these or OIDC client secrets into chat. The explicit
`account_signedin_preview --development-only prepare --issuer ...
--administrator-subject ...` command creates an isolated, audited local directory
from this mapping and saves a private configuration under CampaignIQ-Records.
It does not change the legacy dashboard or production authorization. Owner and
account labels initially use workspace identifiers and a generic brokerage name.

Launch with `account_signedin_preview --development-only launch --resume PATH`.
It binds only 127.0.0.1:8504 with CORS/XSRF enabled. Streamlit OIDC secrets must
be configured locally with a provider-authorized callback for this preview.
The launcher neither copies production secrets nor changes provider settings.
Authentication is still performed by Streamlit/OIDC; this code does not verify
tokens itself or store passwords. A session missing exact issuer/subject claims
is denied, including previously authenticated sessions for another issuer.

Only campaigniq_dev, current OS user and the standard local Unix socket are
accepted. Production/Render and remote database routes remain blocked. Existing
data in another database must not be pointed at this reader to bypass the guard.
An existing approved local restore/copy can be reviewed separately; this tool
does not import one automatically. A passing synthetic rehearsal proves reader
behavior, not that the local database contains current production records.

## Limits

This is an operational read-only development application, not activation of
the main dashboard, account writes, new-account ingestion or a production
migration. Inventory verification proves routing/adapter/byte parity, not report
parity, financial reconciliation, campaign correctness or publication approval.
Actual local OIDC sign-in and actual retained data still need user verification.
All original production runtime files remain unchanged, preserving recovery
status tooling. No production deployment or change is authorized by this work.
