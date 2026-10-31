# Explicit Render private database transport

Render documents that its internal PostgreSQL endpoints use self-signed TLS
certificates and do not support verify-ca or verify-full. For private traffic,
sslmode=require forces encryption without certificate/hostname authentication.
This is a deliberate reviewed private-network policy, not equivalent to
verify-full and not an automatic fallback after certificate errors.

AccountDatabaseTarget defaults to verified-tls, preserving exact verify-full
validation for existing targets. An explicit render-private-tls policy permits
only an exact short internal Render database hostname (dpg-...-letter) and
sslmode=require. External hostnames, IPs, alternate hosts/users/databases/ports,
routing overrides and plaintext/prefer modes remain blocked. No connection is
opened to infer or downgrade transport. Review must establish the exact Render
service/database workspace and region before selecting this policy.

Release document version 1 remains supported with its original verified-tls
semantics; it cannot accept a transport override. Version 2 explicitly includes
target.transport, binding the choice to the approved canonical checksum. All
three runtime roles must share one endpoint AND transport policy. Source hashes
change, so a new committed source review packet and production configuration
review are required; existing packets are not silently rewritten or accepted.

This development increment changes no runtime configuration, database rows,
roles, grants, secrets, startup command or live service. It does not activate
account mode. The production read-only preflight must next test the explicit
private require mode and confirm pg_stat_ssl.ssl=true before approving runtime
configuration. Publication stays disabled.

References, checked 2026-10-31:
https://render.com/docs/postgresql-creating-connecting#ssl-modes-for-internal-connections
https://render.com/docs/private-network
