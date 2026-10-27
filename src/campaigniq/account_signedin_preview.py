"""Launch the LOCAL signed-in, read-only existing-account preview.

The separate prepare command explicitly bootstraps local directory metadata
from trusted server authorization. It never initializes financial tables.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from uuid import UUID, uuid4

from campaigniq.account_administration import Identity, LegacyOwner, propose_legacy_directory
from campaigniq.account_legacy_access import LocalLegacyAccountAccess
from campaigniq.account_signin import verify_legacy_workspace_bindings
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory

CONFIG_ENV = 'CAMPAIGNIQ_SIGNEDIN_PREVIEW_CONFIG'


def local_store(registry_id):
    return LocalPostgresAccountDirectory(
        os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev'),
        registry_id, development_only=True)


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate configuration field')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def prepare(issuer, administrator_subject):
    # The existing environment is a SERVER authorization source, never a web
    # form. Deliberately do not use its older whitespace-normalizing parser.
    mapping = strict_json(os.environ.get('CAMPAIGNIQ_WORKSPACE_AUTHORIZATION', '{}'))
    if type(mapping) is not dict or not mapping:
        raise ValueError('Existing trusted workspace authorization is required')
    admin = Identity(issuer, administrator_subject)
    owners = tuple(LegacyOwner(Identity(issuer, subject), workspace, workspace,
                              'Existing brokerage account', 'schwab')
                   for subject, workspace in mapping.items())
    initial = propose_legacy_directory(owners, admin)
    verify_legacy_workspace_bindings(initial, mapping, trusted_issuer=issuer)
    registry_id = str(uuid4())
    store = local_store(registry_id)  # Reject nonlocal routing before any file.
    root = Path.home() / 'CampaignIQ-Records' / 'Development-Previews'
    folder = root / ('Signed-in-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + registry_id[:8])
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    config = dict(format='campaigniq.signedin_preview', version=1, registry_id=registry_id,
                  issuer=issuer, authorization=mapping, readonly=True)
    path = folder / 'preview-config.json'
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        json.dump(config, f, sort_keys=True, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    # Save receipt before bootstrapping only the separate local admin schema.
    store.initialize()
    store.bootstrap(initial)
    return path


def load_config(path):
    root = Path.home() / 'CampaignIQ-Records' / 'Development-Previews'
    path = Path(path).expanduser()
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('A saved local sign-in configuration is required')
    metadata = path.stat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077):
        raise ValueError('Sign-in configuration must be private and owned by the current user')
    value = strict_json(path.read_text())
    if (type(value) is not dict or set(value) != {'format', 'version', 'registry_id', 'issuer', 'authorization', 'readonly'}
            or value['format'] != 'campaigniq.signedin_preview' or type(value['version']) is not int
            or value['version'] != 1 or value['readonly'] is not True
            or str(UUID(value['registry_id'])) != value['registry_id']
            or type(value['authorization']) is not dict or not value['authorization']):
        raise ValueError('Invalid signed-in preview configuration')
    Identity(value['issuer'], 'configuration-validation')
    return value


def render(st, service, *, administration=None, inputs=None):
    st.title('Brokerage accounts')
    st.info('LOCAL DEVELOPMENT • Existing workspace data is read-only. New-account monthly inputs can be saved locally.')
    claims = st.user.to_dict()
    if claims.get('is_logged_in') is not True:
        st.caption('Use the configured OIDC sign-in. There is no demo-user selector.')
        if st.button('Sign in'):
            st.login()
        return
    if st.button('Sign out'):
        st.logout()
        return
    if administration is not None:
        from campaigniq.ui.account_signedin_administration_view import render as render_administration
        render_administration(st, administration, st.user)
    if inputs is not None:
        from campaigniq.ui.account_signedin_inputs_view import render as render_inputs
        render_inputs(st, inputs, st.user)
    try:
        accounts = service.choices(st.user)
        if not accounts:
            st.info('No existing accounts are available to this sign-in.')
            return
        by_id = {a.id: a for a in accounts}
        selected = st.selectbox('Existing brokerage account', list(by_id),
            format_func=lambda key: by_id[key].name + (' (archived)' if by_id[key].archived else '') + ' · ' + key[:8])
        if st.button('Verify existing saved data'):
            result = service.inventory(st.user, selected)
            st.success('Existing adapters match the retained workspace records. No data was copied or changed.')
            st.dataframe([{'Published/source artifacts': result.artifact_count,
                           'Active drafts': result.active_drafts, 'Abandoned drafts': result.abandoned_drafts,
                           'Original document versions': result.document_versions,
                           'Original document bytes': result.original_bytes}], hide_index=True)
            st.caption('This checks storage access and original bytes; it does not rerun financial reconciliation.')
    except (ValueError, PermissionError):
        st.error('Account access or retained storage could not be verified. Reload after checking local configuration.')


def run_app(st):
    try:
        if (st.get_option('server.address') != '127.0.0.1'
                or st.get_option('server.enableCORS') is not True
                or st.get_option('server.enableXsrfProtection') is not True):
            raise ValueError('Protected loopback server required')
        config = load_config(Path(os.environ[CONFIG_ENV]))
        store = local_store(config['registry_id'])
        service = LocalLegacyAccountAccess(store,
                    trusted_issuer=config['issuer'], authorization=config['authorization'])
        from campaigniq.account_signedin_administration import LocalSignedInAdministration
        administration = LocalSignedInAdministration(store,
                    trusted_issuer=config['issuer'], authorization=config['authorization'])
        from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
        from campaigniq.persistence.signedin_account_data import LocalSignedInInputGateway
        inputs = SignedInMonthlyInputs(LocalSignedInInputGateway(store,
                    trusted_issuer=config['issuer'], authorization=config['authorization']))
        render(st, service, administration=administration, inputs=inputs)
    except Exception:
        st.error('Local sign-in preview stopped. Verify local OIDC configuration and account-directory access.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', required=True, action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('prepare')
    create.add_argument('--issuer', required=True)
    create.add_argument('--administrator-subject', required=True)
    launch = sub.add_parser('launch')
    launch.add_argument('--resume', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            path = prepare(args.issuer, args.administrator_subject)
            print('Local account directory prepared. No financial tables changed.')
            print('Configuration:', path)
            return
        config = load_config(args.resume)
        local_store(config['registry_id'])  # Guard before launching a server.
        env = dict(os.environ, **{CONFIG_ENV: str(args.resume.expanduser().resolve())})
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get('PYTHONPATH', '')
        command = [sys.executable, '-m', 'streamlit', 'run',
            str(Path(__file__).parent / 'ui' / 'account_signedin_app.py'),
            '--server.address=127.0.0.1', '--server.port=8504', '--server.headless=true',
            '--server.fileWatcherType=none', '--server.enableCORS=true',
            '--server.enableXsrfProtection=true', '--browser.gatherUsageStats=false']
        print('Local signed-in preview: http://127.0.0.1:8504', flush=True)
        raise SystemExit(subprocess.run(command, env=env).returncode)
    except KeyboardInterrupt:
        return
    except Exception as error:
        parser.exit(1, 'Local sign-in setup stopped (' + type(error).__name__ + '). No production changes.\n')


if __name__ == '__main__':
    main()
