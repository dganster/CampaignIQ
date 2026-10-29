# Main dashboard integration: startup and report boundaries

Stage 1 foundation, based on development checkpoint 8f00d4d.

The main dashboard now has explicit initialize_dashboard and render_dashboard
functions. Ordinary imports, including imports from another Streamlit page,
do not authenticate, build a runtime, initialize financial storage or record a
visit. The original streamlit run file entry still authenticates first, builds
the same workspace runtime and access-history storage, then renders the existing
pages. Deployment commands, OIDC configuration and publication rules are unchanged.

Published report-source loading is extracted into dashboard_sources.py. Callers
supply storage and own authorization and snapshot lifetime. The loader opens no
database connection and creates no runtime, directory, schema or preferences.
Its readers receive a read-only storage wrapper. Existing pre-cutover visibility,
finalized-marker filtering, canonical loaders and analytics remain unchanged.
The dashboard's load_summaries wrapper delegates to this shared loader.

Two position-history helpers now receive their reporting history explicitly;
they no longer depend on variables created by the rendering block at module
scope. This preserves their behavior after making rendering callable.

This is the foundation for the integrated account controller. It does NOT yet
add account selection to the main dashboard or replace the direct writable
legacy Data path. The existing signed-in local preview remains available. The
next implementation must route page operations through account-scoped services
and isolate preferences/widget state. Never switch accounts by mutating a
shared imported module's global storage; that is not an authorized context.

## Verification

New checks cover cold imports under OIDC/PostgreSQL settings, authentication
ordering, denial before runtime creation, shared source loading with exact
monetary facts, marker visibility, missing/duplicate periods, read-only mutation
rejection, default Streamlit file startup, and actual Overview/Positions/Data
rendering using temporary synthetic data. Existing source and UI contracts are
also run. No new PostgreSQL schema or data operation is introduced by this step.

Run the full suite after installation. There is no additional database rehearsal
or new browser feature to verify for this internal refactor. Restart the existing
signed-in preview with this kit's resume_preview.py if desired; its account data
and behavior are preserved.

## Existing publication recovery tooling

This is the first admin increment to modify a source file checked by the old
historical-publication recovery tools. If the previously retained recovery
packet exists, the installer reads its required runtime hashes, verifies each
source file BEFORE modifying the dashboard, and retains an exact source copy
under its installation receipt's recovery-source directory. It saves a hash
manifest and an updated read-only status command using that source copy.
No old backups, recovery packets or prior scripts are deleted or rewritten.
The read-only status command is prepared but never executed by installation.

The previous dashboard file is also backed up byte-for-byte before replacement.
No code is pushed or deployed, and no financial data is changed. Source retention
preserves the old tool's strict hash checks; it does not bypass them or grant
permission for any production action.
