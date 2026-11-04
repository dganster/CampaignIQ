"""Private, reviewed account release settings. No I/O or environment discovery."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit
from campaigniq.account_backend_config import AccountDirectorySettings,AccountDatabaseTarget
from campaigniq.account_schema_migrations import digest

ROLE_ENV=(('inputs','CAMPAIGNIQ_ACCOUNTS_INPUT_DSN'),('publication','CAMPAIGNIQ_ACCOUNTS_PUBLICATION_DSN'),('preferences','CAMPAIGNIQ_ACCOUNTS_PREFERENCES_DSN'))


def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate release configuration field')
            result[key]=value
        return result
    def reject(value):raise ValueError('Invalid release JSON value')
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=reject)
    except (TypeError,json.JSONDecodeError):raise ValueError('Invalid release configuration') from None


def exact(value,keys):
    if type(value) is not dict or set(value)!=set(keys):raise ValueError('Release configuration fields differ')


def https(value):
    if not isinstance(value,str):raise ValueError('Explicit HTTPS address required')
    try:parsed=urlsplit(value);port=parsed.port
    except ValueError:raise ValueError('Explicit HTTPS address required') from None
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment or port not in (None,443) or value!=value.strip()):
        raise ValueError('Explicit HTTPS address required')
    return parsed


@dataclass(frozen=True,repr=False)
class AccountReleaseConfig:
    settings:AccountDirectorySettings
    host:str
    port:int
    database:str
    roles:tuple[tuple[str,str],...]
    source_files:tuple[tuple[str,str],...]
    redirect_uri:str
    client_id:str
    metadata_url:str
    publication_enabled:bool
    sha256:str
    transport:str = "verified-tls"
    continue_existing:bool = False
    multi_asset_enabled:bool = False
    def __repr__(self):return 'AccountReleaseConfig(<private reviewed settings>)'
    def target(self,role):return AccountDatabaseTarget(self.host,self.database,dict(self.roles)[role],self.port,self.transport)


def parse_release(raw,*,reviewed_sha256):
    value=strict_json(raw)
    keys=('format','version','schema_version','registry_id','trusted_issuer','legacy_authorization','target','roles','source_files','auth','publication_enabled')
    if type(value) is dict and value.get('version') in (3,4):keys+=('continue_existing',)
    if type(value) is dict and value.get('version')==4:keys+=('multi_asset_enabled',)
    exact(value,keys)
    if (value['format']!='campaigniq.account_release' or type(value['version']) is not int or value['version'] not in (1,2,3,4)
        or type(value['schema_version']) is not int or value['schema_version']!=4 or type(value['publication_enabled']) is not bool):
        raise ValueError('Unsupported account release configuration')
    if not isinstance(reviewed_sha256,str) or not re.fullmatch('[0-9a-f]{64}',reviewed_sha256) or digest(value)!=reviewed_sha256:
        raise ValueError('Reviewed release checksum differs')
    continuation=value.get('continue_existing',False)
    if type(continuation) is not bool:raise ValueError('Explicit continuation availability required')
    multi_asset=value.get('multi_asset_enabled',False)
    if type(multi_asset) is not bool:raise ValueError('Explicit multi-asset availability required')
    mapping=value['legacy_authorization']
    if type(mapping) is not dict:raise ValueError('Explicit legacy owner mapping required')
    if continuation and set(mapping.values())!={'workspace-dennis','workspace-andrew'}:
        raise ValueError('Continuation requires the two reviewed retained workspace bindings')
    settings=AccountDirectorySettings(value['registry_id'],value['trusted_issuer'],tuple(sorted(mapping.items())))
    issuer=https(settings.trusted_issuer)
    if issuer.path not in ('','/'):raise ValueError('Account release requires the reviewed root issuer')
    target_keys=('host','port','database','transport') if value['version'] in (2,3,4) else ('host','port','database')
    exact(value['target'],target_keys);exact(value['roles'],('inputs','publication','preferences'))
    transport=value['target'].get('transport','verified-tls')
    if any(type(role) is not str for role in value['roles'].values()) or len(set(value['roles'].values()))!=3:raise ValueError('Three distinct runtime roles required')
    from campaigniq.account_runtime_roles import role_name
    for role in value['roles'].values():
        role_name(role);AccountDatabaseTarget(value['target']['host'],value['target']['database'],role,value['target']['port'],transport)
    files=value['source_files']
    if type(files) is not dict or not files:raise ValueError('Exact release source inventory required')
    for name,checksum in files.items():
        if (not isinstance(name,str) or not name.startswith('src/campaigniq/') or not name.endswith('.py')
            or str(PurePosixPath(name))!=name or '..' in PurePosixPath(name).parts
            or not isinstance(checksum,str) or not re.fullmatch('[0-9a-f]{64}',checksum)):
            raise ValueError('Invalid release source inventory')
    exact(value['auth'],('redirect_uri','client_id','server_metadata_url'))
    auth=value['auth'];redirect=https(auth['redirect_uri']);https(auth['server_metadata_url'])
    if redirect.path!='/oauth2callback':raise ValueError('Reviewed OAuth callback required')
    if auth['server_metadata_url']!=settings.trusted_issuer.rstrip('/')+'/.well-known/openid-configuration':
        raise ValueError('Sign-in metadata issuer differs')
    if not isinstance(auth['client_id'],str) or not auth['client_id'] or auth['client_id']!=auth['client_id'].strip():
        raise ValueError('Explicit reviewed sign-in client required')
    return AccountReleaseConfig(settings,value['target']['host'],value['target']['port'],value['target']['database'],
        tuple(sorted(value['roles'].items())),tuple(sorted(files.items())),auth['redirect_uri'],auth['client_id'],auth['server_metadata_url'],value['publication_enabled'],digest(value),transport,continuation,multi_asset)


def source_inventory(repo):
    root=repo/'src/campaigniq'
    files={}
    if any(path.is_symlink() for path in root.rglob('*')):raise ValueError('Release source symlink prohibited')
    for path in sorted(root.rglob('*.py')):
        if path.is_symlink() or path.resolve()!=path:raise ValueError('Release source symlink prohibited')
        files[path.relative_to(repo).as_posix()]=sha256(path.read_bytes()).hexdigest()
    if not files:raise ValueError('Release source inventory missing')
    return files


def verify_source(config,repo):
    if type(config) is not AccountReleaseConfig:raise ValueError('Reviewed release configuration required')
    if source_inventory(repo)!=dict(config.source_files):raise ValueError('Running code differs from reviewed release')


def validate_auth(config,auth):
    try:
        if set(auth)!={'redirect_uri','client_id','server_metadata_url','client_secret','cookie_secret'}:raise ValueError
        expected={'redirect_uri':config.redirect_uri,'client_id':config.client_id,'server_metadata_url':config.metadata_url}
        if any(auth.get(key)!=value for key,value in expected.items()):raise ValueError
        if (not isinstance(auth.get('client_secret'),str) or not auth['client_secret']
            or not isinstance(auth.get('cookie_secret'),str) or len(auth['cookie_secret'])<32):raise ValueError
        # Named-provider overrides would make st.login() use a different contract.
        if any(isinstance(value,dict) or hasattr(value,'to_dict') for value in auth.values()):raise ValueError
    except Exception:raise ValueError('Server sign-in settings do not match the reviewed release') from None


def release_document(settings,targets,auth,*,repo,publication_enabled=False,continue_existing=False):
    """Build a review proposal from explicit trusted inputs, never a browser form.

    No credentials are accepted here. Caller stores the result privately and
    obtains independent review of its canonical digest before runtime use.
    """
    if type(continue_existing) is not bool:raise ValueError('Explicit continuation availability required')
    if type(settings) is not AccountDirectorySettings or type(targets) is not dict or set(targets)!={'inputs','publication','preferences'}:
        raise ValueError('Trusted account settings and three explicit role targets required')
    values=list(targets.values())
    if any(type(target) is not AccountDatabaseTarget for target in values):raise ValueError('Explicit reviewed role targets required')
    endpoint=lambda target:(target.host,target.port,target.database,target.transport)
    if len({endpoint(target) for target in values})!=1:raise ValueError('Same reviewed database target required')
    target=targets['inputs']
    value=dict(format='campaigniq.account_release',version=1,schema_version=4,registry_id=settings.registry_id,
        trusted_issuer=settings.trusted_issuer,legacy_authorization=dict(settings.authorization),
        target=dict(host=target.host,port=target.port,database=target.database),roles={role:t.user for role,t in targets.items()},
        source_files=source_inventory(repo),auth=dict(auth),publication_enabled=publication_enabled)
    if target.transport!='verified-tls':
        value['version']=2;value['target']['transport']=target.transport
    if continue_existing:
        value['version']=3;value['continue_existing']=True;value['target']['transport']=target.transport
    parse_release(json.dumps(value),reviewed_sha256=digest(value))
    return value
