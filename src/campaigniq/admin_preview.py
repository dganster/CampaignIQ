"""Launch an explicitly local, synthetic-only Streamlit administration preview."""
from __future__ import annotations

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
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.ui.account_administration_view import DEMO_ISSUER, demo_subject_prefix, validate_demo_directory


CONFIG_ENV = 'CAMPAIGNIQ_ADMIN_PREVIEW_CONFIG'


def require_local_mode(development_only):
    if development_only is not True or os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        raise ValueError('This synthetic preview is available only in explicit local development mode')


def load_config(path: Path) -> dict:
    root = Path.home() / 'CampaignIQ-Records' / 'Development-Previews'
    path = path.expanduser()
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Select a saved local preview configuration')
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError('Preview configuration must be a private file owned by the current user')
    value = json.loads(path.read_text())
    if (type(value) is not dict or set(value) != {'format', 'version', 'registry_id', 'synthetic_only'}
            or value['format'] != 'campaigniq.admin_preview' or type(value['version']) is not int
            or value['version'] != 1 or value['synthetic_only'] is not True
            or str(UUID(value['registry_id'])) != value['registry_id']):
        raise ValueError('Invalid synthetic preview configuration')
    return value


def preview_store(config):
    require_local_mode(True)
    store = LocalPostgresAccountDirectory(os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev'),
                                         config['registry_id'], development_only=True)
    validate_demo_directory(store._snapshot(), config['registry_id'])
    return store


def create_preview():
    require_local_mode(True)
    registry_id = str(uuid4())
    store = LocalPostgresAccountDirectory(os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev'),
                                         registry_id, development_only=True)
    prefix = demo_subject_prefix(registry_id)
    initial = propose_legacy_directory((
        LegacyOwner(Identity(DEMO_ISSUER, prefix + 'admin'), 'workspace-preview-' + UUID(registry_id).hex + '-admin',
                    'Demo administrator', 'Demo taxable account', 'Demo broker'),
        LegacyOwner(Identity(DEMO_ISSUER, prefix + 'member'), 'workspace-preview-' + UUID(registry_id).hex + '-member',
                    'Demo member', 'Demo retirement account', 'Demo broker'),
    ), Identity(DEMO_ISSUER, prefix + 'admin'))
    validate_demo_directory(initial, registry_id)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + registry_id[:8]
    folder = Path.home() / 'CampaignIQ-Records' / 'Development-Previews' / ('Admin-' + stamp)
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    config = dict(format='campaigniq.admin_preview', version=1, registry_id=registry_id, synthetic_only=True)
    path = folder / 'preview-config.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(config, f, sort_keys=True, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    # Receipt is saved first. Failed initialization leaves a record, not a reset.
    store.initialize()
    store.bootstrap(initial)
    return path


def streamlit_command():
    return [sys.executable, '-m', 'streamlit', 'run',
            str(Path(__file__).parent / 'ui' / 'admin_preview_app.py'),
            '--server.address=127.0.0.1', '--server.port=8502', '--server.headless=true',
            '--server.fileWatcherType=none', '--server.enableCORS=true',
            '--server.enableXsrfProtection=true', '--browser.gatherUsageStats=false']


def run_app(st):
    """Fail closed before opening a database if invoked outside the launcher."""
    try:
        require_local_mode(True)
        if (st.get_option('server.address') != '127.0.0.1'
                or st.get_option('server.enableCORS') is not True
                or st.get_option('server.enableXsrfProtection') is not True):
            raise ValueError('Preview server must use protected loopback settings')
        name = os.environ.get(CONFIG_ENV)
        if not name:
            raise ValueError('Use the explicit local preview launcher')
        config = load_config(Path(name))
        store = preview_store(config)
    except Exception:
        st.error('Local synthetic preview configuration could not be verified. No account data was loaded.')
        return
    from campaigniq.ui.account_administration_view import render_preview
    render_preview(st, store, config['registry_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', required=True, action='store_true')
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args()
    try:
        require_local_mode(args.development_only)
        path = args.resume.expanduser().resolve() if args.resume else create_preview()
        config = load_config(path)
        preview_store(config)
    except Exception as error:
        parser.exit(1, 'Preview stopped (' + type(error).__name__ + '). Existing records and production are unchanged.\n')
    env = dict(os.environ)
    env[CONFIG_ENV] = str(path)
    src = str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH'] = src + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    print('Local administration preview: http://127.0.0.1:8502', flush=True)
    print('Synthetic preview configuration:', path, flush=True)
    print('Stop with Ctrl+C. Resume with --development-only --resume and this configuration path.', flush=True)
    try:
        result = subprocess.run(streamlit_command(), env=env)
    except KeyboardInterrupt:
        return
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
