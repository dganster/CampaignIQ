# One no-trading review and owner confirmation

Development checkpoint based on live 4af49c5b231b9dc54b0e0f1f25f3d048423f8d89. This changes presentation only; backend source checks, owner confirmation and publication transactions are unchanged.

The guided no-trading path now has one Review month button. It displays the statement cash summary and unchanged-position reconciliation together with the existing two owner choices. Confirm reviewed month saves the same source-bound owner receipt through the existing service. After the revision advances, the UI rebuilds the saved owner confirmation and statement review using current access and sources, displays the confirmed review, and enables the separate account/month publication form. No second review click is required.

Unconfirmed cached reviews are scoped to account, draft, source revision and directory revision. Saved confirmations are revalidated rather than trusting browser readiness. Missing statements and failed or stale reviews block publication. After a failed publication attempt, another explicit review is required, without requiring a duplicate owner confirmation when the original receipt remains valid. The final publication authorization is still a separate unchecked choice; this UI never publishes during review or confirmation.

The nonguided compatibility view is preserved. Published guided months still use the read-only results presentation. No migration, financial row change, source replacement, access grant, runtime installation or production operation is included in this development installer.

Tests exercise missing-source feedback and a single review button; the complete review/owner-confirm/publication sequence; invalidation after source or permission-context changes; revalidation of saved owner receipts; and failed-publication retry. Existing no-trading financial and owner-only tests remain unchanged.
