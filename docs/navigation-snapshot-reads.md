# Navigation reads within one verified snapshot

Baseline: cae41ce. The PDF reuse release reduced measured browser navigation from about 20 seconds to 7–8 seconds. Its warm shared-server candidate profile took 5.8594 seconds. Earlier profiling counted 873 SQL execute calls, including repeated history boundary validation and individual privilege checks.

This checkpoint combines the existing table-permission checks into one ordered query per validator. It still checks every permitted and forbidden privilege and, where previously required, effective table ownership. Missing, extra, malformed or reordered results fail closed. Elevated-role, target, schema integrity, grant-option and column-grant checks in existing validators are unchanged. No migration ledger or policy definition changes.

For deployment sessions only, reuse is enabled after SQL confirms both read-only mode and repeatable-read isolation. The continuation wrapper then reuses its verified key set, boundary marker validation and already-read continuation text only within that session. The broker-binding reader similarly reuses the directory that was fully validated inside that snapshot. Writable and read-committed sessions keep the previous live-read behavior.

Every reuse checks active session and connection state first. Closed, idle or failed SQL transactions cannot return cached content. New navigation creates fresh sessions, checks current authorization and obtains fresh source rows. There is no cross-navigation financial-result or authorization cache. Existing directory guards, revocation waits, receipt reconstruction, financial integrity checks and stale-source controls remain in place. Failures are not cached.

Tests cover SQL-result/permission mismatches; boundary and source changes in fresh sessions; expired, idle and failed handles; non-reuse for writable/read-committed transactions; and directory revalidation in new sessions. The full suite and actual disposable PostgreSQL rehearsal are required before release. Candidate server timing and financial parity will be measured before deployment; no speed claim is made from unit tests.
