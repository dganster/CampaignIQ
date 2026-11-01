"""Explicit reviewed entry point support; no migration, bootstrap or repair."""
import os
from pathlib import Path
import stat
from campaigniq.account_release_config import AccountReleaseConfig,parse_release,verify_source,validate_auth,ROLE_ENV
from campaigniq.account_deployment_application import DeploymentApplication,render_application
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction
from campaigniq.account_runtime_roles import _verify_complete
from campaigniq.account_publication_roles import validate_publication_role
from campaigniq.account_preference_roles import validate_preference_role


def read_release(path,*,reviewed_sha256):
    path=Path(path)
    if not path.is_absolute() or path.resolve()!=path:raise ValueError('Private absolute release file required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077 or info.st_size>1048576):
            raise ValueError('Release file must be private, bounded and owned by the service user')
        raw=stream.read(1048577)
    return parse_release(raw,reviewed_sha256=reviewed_sha256)


def compose_release(config,connections,auth,*,repo):
    if type(config) is not AccountReleaseConfig:raise ValueError('Reviewed release configuration required')
    verify_source(config,repo);validate_auth(config,auth)
    stores=[]
    for role,name in ROLE_ENV:
        dsn=connections.get(name)
        if not isinstance(dsn,str) or not dsn:raise ValueError('Three explicit account role connections required')
        try:stores.append(PostgresAccountDirectory(dsn,config.settings,config.target(role)))
        except Exception:raise ValueError('Account role connection does not match the reviewed release') from None
    return DeploymentApplication(*stores,publication_enabled=config.publication_enabled,continue_existing=config.continue_existing)


def verify_startup(application):
    """Read-only preflight across all roles before rendering or accepting commands."""
    if type(application) is not DeploymentApplication:raise ValueError('Explicit deployment application required')
    stores=(application.store,application.inputs._publisher.writer,application.preferences.store)
    validators=(_verify_complete,validate_publication_role,validate_preference_role)
    directories=[]
    for store,validate in zip(stores,validators):
        with store._connection(readonly=True) as conn:
            if validate is _verify_complete:validate(conn,store.target.user)
            else:validate(conn)
            if conn.execute('SELECT version FROM campaigniq_accounts.schema_migrations ORDER BY version').fetchall()!=[(1,),(2,),(3,),(4,)]:
                raise ValueError('Reviewed account migrations are incomplete')
            directories.append(DirectoryTransaction(conn,store.settings)._load())
    if directories[1:]!=directories[:-1]:raise ValueError('Account directory changed during startup; retry')


def run(st,*,repo):
    try:
        path=os.environ.get('CAMPAIGNIQ_ACCOUNT_RELEASE_FILE')
        reviewed=os.environ.get('CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256')
        if not path:raise ValueError('Reviewed release configuration required')
        config=read_release(path,reviewed_sha256=reviewed)
        application=compose_release(config,os.environ,st.secrets['auth'],repo=repo)
        verify_startup(application)
    except Exception:
        # Do not expose identities, credentials, DSNs or database exception text.
        st.error('CampaignIQ account startup checks failed. Contact the administrator.')
        return
    try:render_application(st,application)
    except Exception:
        st.error('CampaignIQ account access could not be verified. Contact the administrator.')
