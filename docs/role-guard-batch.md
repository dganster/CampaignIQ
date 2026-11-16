# Fresh role guard batching

The input, publication and preference role validators read role flags,
memberships, database creation rights and both schemas' creation, grant-option
and ownership checks in one bounded catalog query instead of nine separate
queries. Table and column privilege checks and schema-USAGE checks remain.

Every invocation executes fresh SQL on its caller's connection. No permissions,
directories, connections or validation results are cached across requests.
Missing, duplicated, reordered, null or non-boolean guard results fail closed.
Existing migration policy hashes and grant-review digest formats are unchanged.

Parity tests retain the scalar reference validator for all three grant policies.
The disposable PostgreSQL rehearsal compares the batch with independent scalar
checks through the real restricted roles and verifies unchanged rows. The release
review must still verify the actual production role connections and source chain.

This reduces query round trips; actual page latency must be measured after release.
