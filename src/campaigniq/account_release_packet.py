"""Prepare a source/recovery review packet offline. Never connects or activates."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4
from campaigniq.account_release_config import source_inventory
from campaigniq.account_schema_migrations import digest

ACTIVATION='''# Account release activation review

This packet is an offline source inventory and procedure, not publication or deployment authorization. Production has not been contacted. Do not activate from a synthetic rehearsal registry or substitute an old local restore for current production data.

Before activation, obtain a fresh read-only production capture and independently verified backup/recovery receipt. Confirm all current legacy owners, original document versions, financial artifacts, published months and preferences against that capture. Use the actual trusted server issuer/subject mapping, not display names, email guesses, test subjects or development registry IDs. Review legacy preference carry-forward separately; existing original preference bytes must be retained.

Prepare a private release JSON with `campaigniq.account_release_config.parse_release` validation: a new reviewed deployment registry; complete immutable legacy owner mapping; exact target host/port/database; three distinct restricted role names; matching sign-in client, metadata and HTTPS callback; and this source inventory. It contains no database or OAuth passwords. Record its canonical SHA256 independently for approval. Keep publication_enabled false for the first account-read verification.

Prepare and review the existing deterministic schema/bootstrap/grant proposals against the fresh capture. Review exact role OIDs and catalog/source fingerprints. Schema versions 1–4, owner mapping/bootstrap, and grants must be applied as separately authorized administrator transactions before runtime startup. Startup never migrates or repairs. Runtime roles must not own storage, inherit elevated membership, create objects, rewrite originals/legacy artifacts/audits, or modify migration records. Do not change PUBLIC or default privileges implicitly.

After explicit activation approval, the server-only configuration names are:

- CAMPAIGNIQ_ACCOUNT_RELEASE_FILE: private absolute release JSON path owned by the service user, mode 0600.
- CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256: exact approved canonical configuration SHA256.
- CAMPAIGNIQ_ACCOUNTS_INPUT_DSN, CAMPAIGNIQ_ACCOUNTS_PUBLICATION_DSN, CAMPAIGNIQ_ACCOUNTS_PREFERENCES_DSN: separate matching-role secrets with verify-full TLS. Never put them in this packet, browser forms, screenshots or reports.
- Streamlit [auth]: exact reviewed redirect_uri, client_id, server_metadata_url plus private client_secret and cookie_secret. The server metadata URL must match the trusted issuer. Do not copy local sign-in credentials into production or change the callback without provider review.

The separately approved start command is `streamlit run src/campaigniq/ui/account_deployment_entrypoint.py` using the existing service port/address and CORS/XSRF protections. No Render setting is changed by this packet. The old entry point remains available for recovery. Account startup verifies the complete running Python source inventory, reviewed config checksum, auth settings, exact role targets/profiles, versioned schema, audit integrity and complete legacy mapping. It opens only read-only preflight transactions; failure shows no credentials or partial account UI, and has no filesystem/database fallback.

Verify real owner sign-in, own legacy reports/originals/preferences, another owner's isolation, view grants/revocation, archived history and admin activity before authorizing new-account publication. Compare every retained report and monetary/evidence coverage with the fresh reviewed capture. Enabling publication requires a newly reviewed release config/checksum plus explicit account/month confirmation in the UI. Never use a production test publication in an existing account.
'''
RECOVERY='''# Account release recovery

Preserve the fresh pre-activation source/configuration backup, original start command, environment variable names/values in private storage, exact migration/grant/bootstrap plans, and independent prewrite recovery receipt before any approved change. Existing historical publication recovery tooling must remain byte-for-byte available; this packet does not replace it.

On failed startup or failed owner/report verification, stop account writes and retain the failing reports and logs privately. Restore the exact previous application source/start command/configuration from the reviewed capture. Do not reset main, overwrite unknown files or change identities to bypass an integrity check. Do not restore an older whole database over later user uploads, preferences, directory actions or publications.

Code/configuration rollback preserves the account schema, role identities, original documents, audit trail and any new user records. After any post-activation activity, the old UI may not expose new-account data; preserve it for recovery rather than dropping tables or pretending it is absent. Re-enabling the previous writer must be separately assessed against the current database. Do not run DROP SCHEMA, CASCADE, DROP OWNED, FORCE, destructive role revocations or broad database restore as a routine code rollback.

Any data reversal needs a new exact reviewed delta, durable recovery receipt, affected-row/source fingerprints, current-state checks, transactional rehearsal and explicit authorization. A failed source, role, audit or original-document checksum is a stop condition, not permission for repair.
'''


def git(repo,*args):
    result=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True)
    if result.returncode:raise ValueError('Reviewed Git checkout required')
    return result.stdout.strip()


def save(path,value):
    data=value if isinstance(value,str) else json.dumps(value,sort_keys=True,indent=2)+'\n'
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def prepare_packet(repo,output,*,development_only):
    if development_only is not True or os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        raise ValueError('Offline development preparation only')
    repo=Path(repo).expanduser().resolve(strict=True)
    if Path(git(repo,'rev-parse','--show-toplevel'))!=repo or git(repo,'branch','--show-current')!='feature/historical-backfill-2025':
        raise ValueError('Select the reviewed development checkout')
    if git(repo,'status','--porcelain','--untracked-files=all'):raise ValueError('Commit the reviewed increment before preparing a release packet')
    files=source_inventory(repo)
    tracked=set(git(repo,'ls-files','src/campaigniq').splitlines())
    if set(files)!={name for name in tracked if name.endswith('.py')}:raise ValueError('Release source contains untracked or missing files')
    output=Path(output).expanduser()
    if not output.is_absolute() or output.exists() or output.parent.resolve()!=output.parent:raise ValueError('New absolute packet directory required')
    output.mkdir(mode=0o700)
    manifest=dict(format='campaigniq.account_release_source',version=1,commit=git(repo,'rev-parse','HEAD'),source_files=files)
    save(output/'release-source-manifest.json',manifest)
    save(output/'activation-review.md',ACTIVATION);save(output/'recovery-review.md',RECOVERY)
    report=dict(format='campaigniq.account_release_packet',version=1,source_commit=manifest['commit'],source_manifest_sha256=digest(manifest),
        source_files=len(files),offline_packet_complete=True,activation_ready=False,production_connection_made=False,
        missing_reviews=['fresh production capture and backup','trusted ownership mapping and registry','exact role and schema/bootstrap plans',
            'legacy preference carry-forward','private release configuration and approved checksum','real owner sign-in/report parity','explicit production activation approval'])
    save(output/'release-readiness.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--output',type=Path)
    parser.add_argument('--development-only',action='store_true',required=True);args=parser.parse_args()
    output=args.output or Path.home()/'CampaignIQ-Records/Release-Reviews'/('Accounts-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):parser.exit(1,'Offline development preparation only.\n')
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        report=prepare_packet(args.repo,output,development_only=args.development_only)
    except (ValueError,OSError):parser.exit(1,'Release packet preparation stopped; preserve existing files and verify the reviewed checkout.\n')
    print('Offline release review packet:',output)
    print('Source files verified:',report['source_files'])
    print('Activation ready: False. Production was not contacted or changed.')

if __name__=='__main__':main()
