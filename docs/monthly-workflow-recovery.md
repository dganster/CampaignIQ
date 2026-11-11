# Guided monthly inputs and draft recovery

Development checkpoint based on 94bc1f5. No production deployment is included.

The signed-in Data page first asks whether the account traded. No-trading months show the Brokerage Statement and a single statement review; normal months offer market selection and retain the existing market-specific controls. Saved source filenames are visible independently of input validation. Confirmation status survives Streamlit reruns. Publish remains separate, requires explicit account/month authorization, and is disabled in the no-trading UI until its owner confirmation exists.

Saved documents and confirmations can be removed from the active draft after a consequence review and explicit confirmation. Removing any selected item also supersedes the known source-choice confirmations. Start this month over supersedes all active sources and confirmations. Original bytes, upload history and previous financial records remain unchanged. Each correction appends an audit receipt and advances the draft revision. Recovery requires current edit permission, a current revision and exact review hash; source integrity is checked before mutation. Any existing monthly financial artifact blocks both operations, including incomplete generations.

A supporting Thinkorswim CSV is accepted in the no-trading path only when its explicit execution/order sections are empty, its broker identifier and month match, every cash row is a balance or supported statement-matched income, the running cash reconciles, and any Forex cash section is zero. Unknown layouts, executions, transfers and fees remain blocked. Removing an unsupported optional export remains available; file size alone never establishes activity or inactivity.

Owner confirmations and immutable publication controls remain unchanged. Recovery never publishes. The UI does not infer campaign dividend entitlement or treat missing market evidence as verified zero.

Validation includes source-selection and hostile export cases, statement review through publication with a nonempty cash-only CSV, Streamlit interactions for explicit recovery/cancellation/history and guided no-trading visibility. The existing disposable PostgreSQL rehearsal additionally exercises foreign/stale/changed requests, rollback after recovery plus audit, concurrent removal with one winner, reset and upload again, and published-month protection using actual restricted roles. That rehearsal must pass on the user's local development checkout before release review.
