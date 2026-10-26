# Account monthly input preview (local development)

The interactive synthetic preview now uses BoundAccountImportPreviewService.
It saves account-specific drafts and runs the existing financial input validator,
with an additional broker identity check. There is no execution or publication
endpoint, production storage switch, or legacy-data migration in this increment.

Run the full test suite and broker_binding_rehearsal --development-only. The
browser launcher is still:

    PYTHONPATH=src python -m campaigniq.account_import_preview_ui --development-only

It accepts --resume with a previously printed private preview-config.json path.
The existing local socket/database, Render rejection, protected loopback, synthetic
registry and enabled-user guards remain unchanged. No new database table or
schema is needed for broker metadata.

On an authorized demo account, an administrator can expand Bind demo broker
account and enter a full synthetic Schwab identifier. Securities and Forex are
separate bindings: eight securities digits (optional 4-4 hyphen) and six to twelve
Forex digits. Leading zeros are retained. Replacement needs an explicit checkbox
and current directory revision. Binding an identifier to another account in the
same registry is blocked, including when its existing account is archived.
Binding metadata does not change an account's owner, grants or storage scope.

Each change appends a versioned event to the existing verified directory audit.
The digest, kind, broker and final four digits are retained; full identifiers are
not retained in directory/audit records. Digests are comparison metadata, not
encryption. Old bindings remain in the audit when replaced. Metadata and audit
commit or roll back together. Admin permission is checked inside the directory
row lock. The account gateway's existing share lock prevents a binding change
from committing during a document save or preview.

The UI shows only suffixes. Document uploads are staged privately and checked
before a draft document write. A wrong account, missing binding, unknown format,
masked identifier or conflicting headers rejects the upload; replacement
confirmation cannot override an identity mismatch. Saved older documents are
checked again during each preview, so changing the binding invalidates a prior
identity match without deleting the document. Identity failures are displayed
alongside input validation findings and prevent input readiness. A match does
not waive financial validation, reconciliation or publication safeguards.

Recognized header families are Schwab Brokerage Statement Account Number,
thinkorswim Account Statement for <full number> since/from, Forex Transaction
Report Account (USD), and Forex monthly Account Account Currency. Opening,
next-month assignment and realized documents must have a supported full Schwab
account header. This is deliberately format-limited: some real or masked exports
will remain unverified. The crypto statement reader currently recognizes only
masked suffixes; the new identity gate therefore rejects crypto uploads rather
than claiming a full identity match. Other broker labels do not imply supported
parsers. Use synthetic fixtures only until real document coverage is reviewed.

The earlier AccountImportPreviewService is retained as the explicitly unbound
compatibility harness for previous local storage rehearsals; it is not the
interactive entry point. No class in this prototype is a production API. Trusted
server Python must not expose raw storage adapters to a browser.

UI keys include registry, actor, account, draft and revision, and validation
results are not cached across selections. View grants and archived accounts can
read/preview; they cannot upload or create drafts. Disabled users are denied.
Original documents, confirmed replacement versions, existing financial data,
original dashboard runtime sources and historical recovery files are preserved.

The installer backs up updated development files before replacement. The SQL
rehearsal retains synthetic rows and its report under CampaignIQ-Records. Nothing
is reset, deleted, imported or published to production.
