# Local administration preview

This increment provides a working local Streamlit interface backed by the
previously rehearsed local PostgreSQL directory. Production sign-in, financial
records and the deployed dashboard remain untouched.

## Running

From the development checkout run:

```
PYTHONPATH=src python -m campaigniq.admin_preview --development-only
```

Open `http://127.0.0.1:8502` on the same computer. Stop with Ctrl+C. The launcher
prints a private configuration file under CampaignIQ-Records/Development-Previews.
Pass that file with `--resume` to reopen the same preview without resetting it.
Starting without `--resume` deliberately creates a separate synthetic registry.

The preview uses the existing local campaigniq_dev database and its dedicated
campaigniq_admin_dev schema. Every registry identity must use a development-only
issuer and a subject prefix tied to the generated registry UUID. An ordinary
Google identity directory or older rehearsal registry is refused before it is
displayed. There is no import of Dennis/Andrew's real identities or finances.

## Available workflows

- Add synthetic demo users; change display name, enabled status and admin role.
- Add brokerage accounts for enabled owners; rename, archive and restore them.
- Grant view/edit access or revoke shared access. Owners retain implicit access.
- Read administrative activity with actor, action, target and change details.
- Switch synthetic demo users to inspect member/disabled-user behavior.
- Select only accounts the current demo user can access. Financial reporting
  and consolidation calculations are deliberately not connected.

The demo user switch is NOT authentication or a production impersonation feature.
The launcher and app refuse Render and non-loopback server settings. The app
requires a private user-owned configuration inside the preview records folder;
CORS and XSRF protections must remain enabled. No passwords or sign-in recovery
credentials are collected. Real-user invitation/sign-in recovery is still to be
integrated with the existing external identity provider.

## Update and concurrency behavior

Forms retain the revision displayed when they were opened. A stale submission
is rejected instead of silently being retried using the latest revision. Reload
refreshes forms. Successful saves reset form state. The service locks and checks
the current persisted actor on each write, so stale UI privilege flags cannot
override disabled accounts, revoked roles, or the last-admin safeguard.

This increment fixes a Python argument-name collision in the existing directory
command dispatcher: the acting identity is now a positional-only actor argument,
distinct from the new user's `identity` field. Existing positional callers are
compatible. The installer backs up the exact previous dispatcher file before
replacing it; the remaining files are new. No database schema change is needed.

Streamlit AppTest exercises actual form submissions using the real immutable
policy and an in-memory service adapter. Local PostgreSQL transaction behavior
was checked in the preceding 14-control rehearsal; this preview still needs a
human browser check on Jumbo before production integration is considered.
