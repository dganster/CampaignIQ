# Futures and crypto application workflow

The existing account application can now route an explicitly confirmed month to
the tested futures, approved-crypto or ordinary monthly writer. Both server
publication availability and the new multi-asset capability must be enabled.
Constructors default the new capability to false. Existing configurations retain
their previous behavior.

Release configuration version 4 explicitly includes `multi_asset_enabled` and
`continue_existing`. Its reviewed checksum binds both flags, the complete source
inventory, trusted owner mapping, sign-in settings and the three existing roles.
Versions 1–3 remain supported and cannot enable the new capability. No installed
configuration, credentials or production data are changed by this checkpoint.

With the reviewed capability enabled, Data exposes Crypto statements and source
review. The owner supplies prior and current PDF statements, reviews exact costs
and original source discrepancies, and explicitly confirms both account
association and source choices. Changing either PDF clears the displayed review.
Changing the saved revision also clears it. The backend independently rebuilds
the preview and checks its fingerprint before saving. Original PDFs and receipts
are committed atomically through the input role, separately from financial data.
The generic uploader omits the crypto statement role in this workflow.

View-only and published accounts can inspect saved crypto reviews but have no
source approval controls. Futures retain explicit opening/ending owner assertions
and statement account association. These are labelled as owner evidence, not
independent broker verification. Monthly source checks do not publish anything.
The separate Publish month form still identifies the account and month and
requires explicit confirmation. Publication always rechecks current identity,
access, source revision and financial controls inside the serialized writer.

The disposable LOCAL crypto rehearsal now exercises the application publication
entry point for crypto-only and combined futures/crypto months, with the same
rollback, concurrency, readonly-reader and corruption tests. Default-disabled
publication is checked before enabling the capability only in the disposable
process. All copied existing records and the retained restore remain verified.

This development installer does not activate the feature in production. Exact
source release review, current production startup verification and deployment
approval remain separate steps. No October month is published by installation.
