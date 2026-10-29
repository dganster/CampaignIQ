"""Compose the existing main reports with freshly authorized LOCAL accounts."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.access import AccessContext
from campaigniq.ui.account_dashboard_state import ScopedUI
from campaigniq.ui.preferences import render_preferences


def report_module():
    # A private module instance per render. Never replace globals in the shared
    # imported dashboard module: other Streamlit sessions may run concurrently.
    path = Path(__file__).with_name('dashboard.py')
    spec = importlib.util.spec_from_file_location('campaigniq_private_account_dashboard', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_reports(ui, snapshot, view, *, identity):
    module = report_module()
    module.st = ui
    module.ACCESS = None  # Directory administration is composed separately.
    module.ARTIFACT_STORAGE = snapshot
    with TemporaryDirectory(prefix='campaigniq-dashboard-') as directory:
        # Never discover another account's CSVs or the developer test fixtures.
        # Published artifacts supply the reports; supplementary CSV evidence
        # has not been attached to this account-aware report reader yet.
        module.HISTORICAL_SOURCE_ROOT = Path(directory) / 'history'
        module.AUTHORITATIVE_STATE_DIR = Path(directory) / 'state'
        module.render_dashboard(selected_view=view, preferences_renderer=lambda st:
            render_preferences(st, snapshot, AccessContext(identity, snapshot.namespace), persist=False))


def render(st, service, *, administration):
    if st.user.to_dict().get('is_logged_in') is not True:
        st.title('CampaignIQ')
        if st.button('Sign in'): st.login()
        return
    try:
        overview = service.choices(st.user)
        root = 'account-dashboard:' + service.store.registry_id + ':' + overview.user_id + ':'
        st.sidebar.caption('LOCAL DEVELOPMENT • Existing data is read-only')
        if st.sidebar.button('Sign out', key=root+'logout'):
            # Discard cached account screen state at logout. Financial bytes
            # are not stored in session state or a process-global report cache.
            for key in list(st.session_state):
                if key.startswith(root): del st.session_state[key]
            st.logout()
            return
        choices = {a.id:a for a in overview.accounts}
        if not choices:
            st.info('No brokerage accounts are available to this sign-in.')
            if overview.administrator:
                from campaigniq.ui.account_signedin_administration_view import render as admin
                admin(st, administration, st.user)
            return
        selector = root+'account'
        if st.session_state.get(selector) not in choices: st.session_state[selector] = next(iter(choices))
        selected = st.sidebar.selectbox('Brokerage account', list(choices),
            format_func=lambda key: choices[key].name + (' [archived]' if choices[key].archived else '')+' · '+key[:8], key=selector)
        account = choices[selected]
        ui = ScopedUI(st, st.session_state, root+selected+':')
        views = ('Overview','Campaigns','Performance','Positions','Data','Crypto','Compare accounts')
        if overview.administrator: views += ('Administration',)
        pending = ui.session_state.get('campaigniq_primary_view')
        if pending in views and pending != ui.session_state.get('_last-report-view'):
            ui.session_state['main-view'] = pending
        if ui.session_state.get('main-view') not in views: ui.session_state['main-view'] = 'Overview'
        view = ui.sidebar.radio('Navigate', views, key='main-view')
        ui.session_state['campaigniq_primary_view'] = view
        ui.session_state['_last-report-view'] = view
        if view == 'Administration':
            from campaigniq.ui.account_signedin_administration_view import render as admin
            admin(ui, administration, st.user)
            return
        if view == 'Compare accounts':
            from campaigniq.ui.account_signedin_comparison_view import render as compare
            compare(ui, service.inputs, st.user)
            return
        if view == 'Data':
            st.title('Data · '+account.name)
            if account.legacy:
                st.info('Existing saved account data is read-only in this local development view.')
                if ui.button('Verify existing saved data'):
                    result = service.legacy.inventory(st.user, selected)
                    ui.success('Original documents and storage adapters match.')
                    ui.dataframe([{'Artifacts':result.artifact_count,'Active drafts':result.active_drafts,
                                   'Original versions':result.document_versions}], hide_index=True)
                from campaigniq.ui.account_legacy_results_view import render as existing
                existing(ui, service.legacy, st.user, selected)
            else:
                from campaigniq.ui.account_signedin_inputs_view import render as inputs
                inputs(ui, service.inputs, st.user, selected_account_id=selected)
            return
        snapshot = service.snapshot(st.user, selected)
        if not snapshot.list_keys(suffix='-realized-attributions.json'):
            st.title('CampaignIQ · '+account.name)
            st.info('No published reports for this account yet. Open Data to prepare its first month.')
            return
        st.caption(account.name+' • Published account records. Supplementary historical CSV evidence is not loaded here.')
        render_reports(ui, snapshot, view, identity=st.user.to_dict()['sub'])
    except (ValueError, PermissionError, KeyError):
        st.error('Account access or saved report sources could not be verified. Reload after checking the account directory.')
