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


def render_reports(ui, snapshot, view, *, identity, preferences_renderer=None):
    from campaigniq.navigation_timing import stage
    with stage('dashboard_module_import'):
        module = report_module()
    from campaigniq.navigation_timing import timed_ui
    module.st = timed_ui(ui)
    module.ACCESS = None  # Directory administration is composed separately.
    module.ARTIFACT_STORAGE = snapshot
    with TemporaryDirectory(prefix='campaigniq-dashboard-') as directory:
        # The root contains only this authorized snapshot's retained originals.
        # Uploaded filenames and shared developer folders are never consulted.
        from campaigniq.account_history_evidence import HistoryEvidence,materialize_history
        history=snapshot.history or HistoryEvidence()
        with stage('history_materialization'):
            module.HISTORICAL_SOURCE_ROOT = materialize_history(Path(directory)/'history',history)
        module.AUTHORITATIVE_STATE_DIR = Path(directory) / 'state'
        with stage('report_rendering'):
            module.render_dashboard(selected_view=view, preferences_renderer=preferences_renderer or (lambda st:
                render_preferences(st, snapshot, AccessContext(identity, snapshot.namespace), persist=False)))


def render(st, service, *, administration, deployment=False):
    if type(deployment) is not bool:raise ValueError("Explicit application view mode required")
    from campaigniq.navigation_timing import stage,authorize_panel,select_view
    mode={"deployment":True} if deployment else {}
    if st.user.to_dict().get('is_logged_in') is not True:
        st.title('CampaignIQ')
        if st.button('Sign in'): st.login()
        return
    try:
        try:
            with stage('account_choices_and_authorization'):
                overview = service.choices(st.user)
        except PermissionError:
            st.info('This sign-in does not have enabled CampaignIQ access.')
            if deployment:registration_screen(st,administration)
            return
        if deployment:authorize_panel(st,administrator=overview.administrator)
        root = 'account-dashboard:' + service.store.registry_id + ':' + overview.user_id + ':'
        st.sidebar.caption(('Account workspace' if getattr(service,'continue_existing',False) else 'Account workspace • Existing accounts are read-only') if deployment else 'LOCAL DEVELOPMENT • Existing data is read-only')
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
                admin(st, administration, st.user, **mode)
            return
        selector = root+'account'
        if st.session_state.get(selector) not in choices: st.session_state[selector] = next(iter(choices))
        labels=account_labels(choices.values())
        selected = st.sidebar.selectbox('Brokerage account', list(choices),
            format_func=labels.__getitem__, key=selector)
        account = choices[selected]
        ui = ScopedUI(st, st.session_state, root+selected+':')
        views = ('Overview','Campaigns','Performance','Positions','Data','Crypto','Compare accounts')
        if overview.administrator: views += ('Administration',)
        pending = ui.session_state.get('campaigniq_primary_view')
        if pending in views and pending != ui.session_state.get('_last-report-view'):
            ui.session_state['main-view'] = pending
        if ui.session_state.get('main-view') not in views: ui.session_state['main-view'] = 'Overview'
        view = ui.sidebar.radio('Navigate', views, key='main-view')
        select_view(view)
        ui.session_state['campaigniq_primary_view'] = view
        ui.session_state['_last-report-view'] = view
        if view == 'Administration':
            from campaigniq.ui.account_signedin_administration_view import render as admin
            admin(ui, administration, st.user, **mode)
            return
        if view == 'Compare accounts':
            from campaigniq.ui.account_signedin_comparison_view import render as compare
            compare(ui, service.inputs, st.user, **mode)
            return
        if view == 'Data':
            st.title('Data · '+account.name)
            page_data=None
            if deployment and getattr(service,'continue_existing',False) and callable(getattr(service.inputs,'data_page',None)):
                from campaigniq.performance_monitor import TimedService
                from campaigniq.account_deployment_application import DraftSelectionUnavailable
                draft_key='signedin-input:'+service.store.registry_id+':'+overview.user_id+':'+selected+':draft'
                try:
                    page_data=TimedService(service.inputs).data_page(st.user,selected,preferred_draft_id=ui.session_state.get(draft_key))
                except DraftSelectionUnavailable as error:
                    ui.error(str(error))
                    if ui.button('Reload saved months',key=draft_key+':reload'):
                        ui.session_state.pop(draft_key,None)
                        st.rerun()
                    return
            if account.legacy:
                st.info(('Published history is preserved. Continue with a new reporting month below.' if getattr(service,'continue_existing',False) else 'Existing saved account data is read-only.') if deployment else 'Existing saved account data is read-only in this local development view.')
                if ui.button('Verify existing saved data'):
                    result = service.legacy.inventory(st.user, selected)
                    ui.success('Original documents and storage adapters match.')
                    ui.dataframe([{'Artifacts':result.artifact_count,'Active drafts':result.active_drafts,
                                   'Original versions':result.document_versions}], hide_index=True)
                from campaigniq.ui.account_legacy_results_view import render as existing
                existing(ui, service.legacy, st.user, selected, **mode,**({'months':page_data['retained_months']} if page_data is not None else {}))
                if deployment and getattr(service,"continue_existing",False):
                    from campaigniq.ui.account_signedin_inputs_view import render as inputs
                    inputs(ui,service.inputs,st.user,selected_account_id=selected,**mode,**({'page_data':page_data} if page_data is not None else {}))
            else:
                from campaigniq.ui.account_signedin_inputs_view import render as inputs
                inputs(ui, service.inputs, st.user, selected_account_id=selected, **mode,**({'page_data':page_data} if page_data is not None else {}))
            return
        with stage('authorized_account_snapshot'):
            snapshot = service.snapshot(st.user, selected, include_history=True)
        if not snapshot.list_keys(suffix='-realized-attributions.json'):
            st.title('CampaignIQ · '+account.name)
            st.info('No published reports for this account yet. Open Data to prepare its first month.')
            return
        history=snapshot.history
        with ui.sidebar.expander('Account source details'):
            ui.caption(account.name+' • Published account records.')
            if history is not None:
                ui.caption(f'Trade-history originals verified for {len(history.originals)} of {len(history.published_months)} published months.')
                ui.caption('This count covers trade-history originals only, not every source document.')
        if history is not None and history.gaps:
            ui.warning('Some supplementary trade-history source details are unavailable. Published journals and financial records remain available.')
            with ui.expander('Trade-history evidence coverage'):
                ui.caption('Supplementary source details require their matching retained originals.')
                for gap in history.gaps:ui.caption(gap)
        preferences_renderer = None
        if hasattr(service,'preferences'):
            from campaigniq.ui.account_preferences_view import render as personal_preferences
            preferences_renderer = lambda scoped_ui: personal_preferences(scoped_ui,service.preferences,st.user,
                selected,st.user.to_dict()['sub'],snapshot,administrator=overview.administrator,**mode)
        render_reports(ui, snapshot, view, identity=st.user.to_dict()['sub'],preferences_renderer=preferences_renderer)
    except (ValueError, PermissionError, KeyError):
        st.error('Account access or saved report sources could not be verified. Reload after checking the account directory.')



def account_labels(accounts):
    """Readable labels only; selections continue to use authorized account IDs."""
    accounts=tuple(accounts)
    names={a.id:a.name+(' [archived]' if a.archived else '') for a in accounts}
    counts={}
    for name in names.values():counts[name.casefold()]=counts.get(name.casefold(),0)+1
    used={name.casefold() for name in names.values()}
    labels={}
    for identifier,name in sorted(names.items()):
        if counts[name.casefold()]==1:
            labels[identifier]=name
            continue
        number=1
        while (name+' · Account '+str(number)).casefold() in used:number+=1
        labels[identifier]=name+' · Account '+str(number)
        used.add(labels[identifier].casefold())
    return labels


def registration_screen(st,administration):
    if hasattr(administration,'request_registration'):
        st.caption('If an administrator invited your Gmail address, request registration after signing in with that Google account. Approval is required before access is enabled.')
        if st.button('Request registration'):
            try:
                message=administration.request_registration(st.user)
            except (PermissionError,ValueError):
                st.error('Registration requires an invitation for your verified Gmail address. Contact your administrator.')
            except Exception:
                st.error('Registration could not be verified. Reload before retrying.')
            else:
                st.info(message)
        if st.button('Sign out',key='registration-logout'):st.logout()
