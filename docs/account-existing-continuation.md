# Existing account continuation development candidate

Dennis retains January 2025–September 2026 (21 months). Andrew retains January–September 2026 (9 months). Both continue with October 2026 under their existing account IDs and workspace namespaces.

The candidate reads retained public artifacts and appends future generations in the existing account schema. No public DML grants, schema migrations, copied financial history, replacement accounts, credential changes or production settings are introduced. Historical sources and originals remain in place. Explicit `DeploymentApplication(..., continue_existing=True)` composition is required for the candidate; the default remains disabled. The reviewed live launcher and release configuration are unchanged.

Every continuation session freshly authorizes the signed-in actor, validates the exact retained history using canonical readers, and blocks overlapping or historical private artifacts. All retained future drafts must still have revision zero and no original documents. Their rows remain untouched; future working drafts live in the account schema under the same account. If any retained future draft acquires a saved source, the route stops pending a reviewed resumption path.

The input role still cannot publish or mutate artifacts. The distinct publisher still requires current owner/write access, explicit confirmation, current source revision, current broker identity, exact preceding month, complete inputs, and independent closing reconciliation. It inserts only future artifacts and cannot replace historical or existing generation keys. Broker identifiers must be recorded through the audited directory binding controls before uploads; no full identifiers appear in this package.

Dashboard snapshots combine the immutable retained prefix with verified future generations in the same authorized financial snapshot. Historical report routes remain available. Publication availability is unchanged and remains disabled by the live release. This package is development source, not a deployment or activation package.

Validation performed here: 205 focused unit, engine and application tests passed, including synthetic September-to-October continuation from 110 to 120 shares with retained artifacts and source documents unchanged. This uses synthetic documents and in-memory transaction doubles, not a PostgreSQL or production verification. The local full suite and a disposable full-restore restricted-role rehearsal remain required before release review.
