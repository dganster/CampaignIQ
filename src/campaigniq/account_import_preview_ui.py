"""Development-only UI for account-scoped saved monthly input validation."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from campaigniq.admin_preview import CONFIG_ENV, create_preview, load_config, preview_store, require_local_mode
from campaigniq.account_import_preview import BoundAccountImportPreviewService, UPLOAD_ROLES
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import LocalAccountDataGateway
from campaigniq.ui.account_administration_view import validate_demo_directory


def render(st, store, config):
    st.title('Account monthly input preview')
    st.warning('LOCAL DEVELOPMENT • Demo accounts only. No publication is available.')
    st.caption('Input readiness does not establish closing inventory reconciliation or authorize publication. '
               'Use synthetic test documents with full supported Schwab account headers.')
    directory = store._snapshot()
    validate_demo_directory(directory, config['registry_id'])
    users = {u.id: u for u in directory.users}
    actor_id = st.sidebar.selectbox('Demo user', list(users), format_func=lambda key: users[key].name)
    actor = users[actor_id]
    if not actor.enabled:
        st.error('This user is disabled.')
        return
    accounts = {}
    for account in directory.accounts:
        try:
            store.authorize(actor.identity, account.id)
        except PermissionError:
            continue
        accounts[account.id] = account
    if not accounts:
        st.info('No accounts are available to this user.')
        return
    account_id = st.selectbox('Brokerage account', list(accounts),
        format_func=lambda key: accounts[key].name + ' · ' + key[:8], key='import-account:' + actor.id)
    access = store.authorize(actor.identity, account_id)
    service = BoundAccountImportPreviewService(LocalAccountDataGateway(store))
    # Keys include registry, actor and account; no document/result cache is shared.
    key = ':'.join((config['registry_id'], actor.id, account_id))
    from campaigniq.broker_account_binding import bindings, KINDS
    known=bindings(directory,account_id)
    if known:
        st.success('Broker account bound: ' + '; '.join(kind.capitalize() + ' ending ' + value.suffix for kind,value in sorted(known.items())))
    else:
        st.info('No broker account identifier is bound yet. Expand Bind demo broker account below to add one.')
    if actor.admin and not accounts[account_id].archived:
        with st.expander('Bind demo broker account'):
            st.caption('Schwab only. Securities and Forex may have different identifiers. '
                       'Masked-only crypto evidence is not supported by this identity check yet.')
            with st.form('binding:' + key + ':' + str(directory.revision), clear_on_submit=True):
                kind=st.selectbox('Account identifier type',KINDS)
                number=st.text_input('Full synthetic broker account identifier',type='default',autocomplete='off',
                    help='Demo account number only, such as 12345678. This is not a password. The saved status shows only the final four digits.')
                confirm=st.checkbox('Confirm replacement of the existing identifier')
                if st.form_submit_button('Save broker binding'):
                    from campaigniq.persistence.postgres_broker_binding import LocalBrokerBindingStore
                    try:
                        LocalBrokerBindingStore(store).bind(actor.identity,account_id,kind,number,
                            expected_revision=directory.revision,confirm_replace=confirm)
                    except (ValueError,PermissionError) as error:
                        st.error(str(error))
                    else:
                        st.rerun()
    if access.writable:
        with st.form('new:' + key):
            year = int(st.number_input('Year', min_value=1900, max_value=2200, value=2025))
            month = int(st.number_input('Month', min_value=1, max_value=12, value=1))
            if st.form_submit_button('Create or resume draft'):
                service.create(actor.identity, account_id, year, month)
                st.rerun()
    else:
        st.info('View access: saved drafts may be previewed; uploads are disabled.')
    drafts = {str(d.id): d for d in service.drafts(actor.identity, account_id)}
    if not drafts:
        st.info('No saved drafts for this account.')
        return
    selected = st.selectbox('Saved month', list(drafts), key='draft:' + key,
        format_func=lambda value: f'{drafts[value].year:04d}-{drafts[value].month:02d}')
    draft = drafts[selected]
    key += ':' + selected + ':' + str(draft.revision)
    st.caption('Saved document revision ' + str(draft.revision))
    if access.writable:
        with st.form('upload:' + key, clear_on_submit=True):
            role = st.selectbox('Document role', UPLOAD_ROLES)
            upload = st.file_uploader('Synthetic test document', type=['pdf', 'txt', 'csv'])
            replacement = st.checkbox('Confirm replacement of this role; preserve the previous original')
            if st.form_submit_button('Save document'):
                if upload is None:
                    st.error('Choose a document first.')
                else:
                    service.save(actor.identity, account_id, draft.id, draft.revision, role,
                        RestoredUpload(upload.name, upload.type, upload.getvalue()), replace=replacement)
                    st.rerun()
    forex = st.checkbox('Forex applicable', value=True, key='forex:' + key)
    crypto = st.checkbox('Crypto applicable', value=True, key='crypto:' + key)
    if st.button('Preview saved inputs', key='preview:' + key):
        result = service.preview(actor.identity, account_id, draft.id, draft.revision,
                                 forex_applicable=forex, crypto_applicable=crypto)
        st.dataframe([dict(Input=v.role, Valid=v.valid, Detail=v.message) for v in result.validations], hide_index=True)
        if result.inputs_ready:
            st.success('Required input checks passed. Reconciliation and publication have not run.')
        else:
            st.info('Complete the missing or invalid inputs before reconciliation.')


def main():
    import streamlit as st
    try:
        require_local_mode(True)
        if (st.get_option('server.address') != '127.0.0.1'
                or st.get_option('server.enableCORS') is not True
                or st.get_option('server.enableXsrfProtection') is not True):
            raise ValueError('Protected loopback server required')
        config = load_config(Path(os.environ[CONFIG_ENV]))
        render(st, preview_store(config), config)
    except (ValueError, PermissionError) as error:
        st.error(str(error))
    except Exception as error:
        # Streamlit rerun/stop exceptions derive from BaseException, not Exception.
        st.error('Preview stopped (' + type(error).__name__ + '). Reload and verify your local account access and documents.')


def launch():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', required=True, action='store_true')
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args()
    require_local_mode(args.development_only)
    path = args.resume.expanduser().resolve() if args.resume else create_preview()
    config = load_config(path)
    store = preview_store(config)
    LocalAccountDataGateway(store).initialize()
    command = [sys.executable, '-m', 'streamlit', 'run',
        str(Path(__file__).parent / 'ui' / 'account_import_preview_app.py'),
        '--server.address=127.0.0.1', '--server.port=8503', '--server.headless=true',
        '--server.fileWatcherType=none', '--server.enableCORS=true',
        '--server.enableXsrfProtection=true', '--browser.gatherUsageStats=false']
    env = dict(os.environ, **{CONFIG_ENV: str(path)})
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get('PYTHONPATH', '')
    print('Local account input preview: http://127.0.0.1:8503', flush=True)
    print('Resume configuration:', path, flush=True)
    try:
        raise SystemExit(subprocess.run(command, env=env).returncode)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    launch()
