# Account sign-in bridge (development only)

The additive account_signin adapter consumes the existing server-owned Streamlit
OIDC user object and a freshly loaded account directory. It requires an exact
server-configured HTTPS issuer and subject, an authenticated session, and an
enabled registered user. Email, display name and browser workspace selections
never establish identity. Administrator status alone grants no financial access.
Archived accounts remain readable under existing grants; revocation is checked
again on every call and must still be checked inside every data transaction.

This adapter does not validate JWTs or implement authentication. Streamlit's OIDC
integration remains responsible for signature, issuer, audience and session
validation. Passing arbitrary objects that imitate st.user violates this API's
trust boundary. There is no client-facing claims endpoint or password store.

The legacy verifier compares the complete existing server authorization mapping
against explicit issuer-bound directory owners. Every legacy account must retain
its exact existing workspace namespace. Duplicate owners, changed namespaces,
missing mappings and extra legacy accounts fail closed. Verification neither
copies nor changes artifacts, documents, drafts or database records. It proves
mapping compatibility, not a production storage migration or an audit of actual
financial rows.

Existing dashboard authentication and runtime files remain unchanged. The local
synthetic database guard remains unchanged and does not accept real identities.
This increment is a tested integration boundary, not live sign-in activation.
Next activation work must provide an audited real directory backend and route
account transactions through it, with a read-only comparison of existing data.
No production changes or data re-entry are required by this installer.
