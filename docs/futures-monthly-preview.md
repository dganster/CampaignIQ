# Empty-ending confirmation and combined monthly preview

This development checkpoint records an owner's confirmation of no ending futures or
futures-options positions, then checks ordinary securities/Forex inventory alongside
the separate futures calculation. It does not enable futures monthly publication.

## Ending evidence

The owner explicitly confirms the selected account's last calendar date. The server
rechecks current ownership, displayed revision and publication status, and rejects
an assertion if calculated futures or futures-option positions are nonzero. It saves
one metadata document, futures_empty_closing_confirmation_v1, in the existing account
draft transaction. No schema, financial artifact or grant changes are required.

The record identifies the account, draft, month, date, server actor and source revision,
and fingerprints the current calculation plus exact opening confirmation, statement
association, PDF and Trade History. It is user_confirmation with broker_verified=false.
A zero calculated position is not reclassified as an independent broker position list.
Source changes invalidate the old assertion. Explicit reconfirmation retains the prior
record; corruption is blocked rather than repaired. Exact repeats create no revision.
Post-write verification and source re-read occur before the transaction can commit.

## Combined read-only check

The service uses one guarded account snapshot. First it validates the full futures
sources and metadata, then checks account identities against the original uploads.
Only after those checks does it create a temporary derived history for the existing
securities/Forex/crypto pipeline. Every removed row must belong to the already
verified futures calculation for the selected month. A separate lineage object gives
the exact original and derived hashes and every removed original row identifier.
All non-futures rows and other source sections are retained. The original upload is
never rewritten, replaced, archived as a derivative or sent to publication.

This permits the unchanged ordinary inventory engine to recognize the securities
portion while futures quantities, premium cash, exercise, settlement and fees remain
accounted for separately. An optional validation with no events does not become a
new requirement; the existing contract still determines required inputs. The result
reports securities mismatches and futures result independently, not as a new combined
performance total. Source and identity failures cannot be overridden by confirmation.

Crypto controls use the original full export, preserving its original row identities.
Crypto fills, holdings or cash activity require the separate statement and reconciled
fill, running-cash and month-end controls. Unchecking Crypto applicable cannot omit
existing crypto activity. One-cent discrepancies are not rounded away or waived.

all_source_controls_ready describes only the source checks. Every result explicitly
sets publication_authorized=false and publication_integration_ready=false. The UI
also displays the publication blocker, even when source checks pass.

## Publication boundary

The deployment publisher rejects futures months before invoking the generation writer.
The guard detects saved futures metadata, futures execution rows and futures cash-ledger activity, including
the case where execution rows were removed but delivery/settlement evidence remains.
Market applicability flags, owner confirmations and a successful preview cannot bypass
it. Existing non-futures publication behavior is preserved and regression-tested.

Canonical futures generations, financial report integration and a real restricted-role
PostgreSQL rehearsal remain necessary before enabling publication. No production
configuration, deployment or October publication occurs in this checkpoint.

## Tests and limitations

Synthetic tests exercise atomic ending-confirmation saves, idempotence, stale and
foreign requests, corruption and changed-source checks, non-flat rejection, original
preservation and derivative lineage. A combined synthetic SPY/futures month reconciles
through the real ordinary engine without writes. UI tests cover explicit confirmation,
read-only state and crypto blockers. Existing publication tests continue to verify
ordinary generations; a separate test proves futures never reaches the writer.

The actual supplied October export's verified partition also passes the ordinary
Trade History format validator with its original bytes unchanged. That check does not
claim that the actual production month's securities closing inventory has reconciled.
No production database was contacted, and no PostgreSQL rehearsal is claimed here.
