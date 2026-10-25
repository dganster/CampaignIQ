"""Local-preview administration forms; all writes pass through the directory service."""
from __future__ import annotations

from uuid import uuid4

from campaigniq.account_administration import Identity, Permission


DEMO_ISSUER = 'https://development-preview.invalid'


def demo_subject_prefix(registry_id):
    return 'synthetic-preview-' + registry_id + '-'


def validate_demo_directory(directory, registry_id):
    prefix = demo_subject_prefix(registry_id)
    if not directory.users or any(u.identity.issuer != DEMO_ISSUER or not u.identity.subject.startswith(prefix)
                                  for u in directory.users):
        raise ValueError('Only this preview registry\'s synthetic users may be displayed')


def _form_revision(st, key, directory):
    name = key + ':displayed_revision'
    if name not in st.session_state:
        st.session_state[name] = directory.revision
    return st.session_state[name]


def _refresh(st, message=None):
    st.session_state['admin_preview_epoch'] = st.session_state.get('admin_preview_epoch', 0) + 1
    if message:
        st.session_state['admin_preview_flash'] = message
    st.rerun()


def _apply(st, store, actor_identity, expected, command, **arguments):
    try:
        store.execute(actor_identity, expected, command, **arguments)
    except PermissionError:
        st.error('Your current permissions do not allow this change. Reload the directory.')
    except ValueError as error:
        # Policy errors contain no connection settings or financial records.
        st.error(str(error))
    except Exception:
        st.error('The local directory action failed. Reload and check the saved state before retrying.')
    else:
        _refresh(st, 'Change saved to the local preview directory.')


def render_preview(st, store, registry_id):
    """Developer-only identity switch; the entry point must enforce loopback mode."""
    st.title('CampaignIQ administration preview')
    st.warning('LOCAL DEVELOPMENT • Synthetic users and accounts only. Production is unchanged.')
    st.caption('The demo user switch below is for testing permissions. It is not production sign-in.')
    try:
        directory = store._snapshot()
        validate_demo_directory(directory, registry_id)
    except Exception:
        st.error('The synthetic directory could not be verified. No administration forms are available.')
        return
    epoch = st.session_state.get('admin_preview_epoch', 0)
    key = 'admin_preview:' + str(epoch) + ':'
    labels = {u.id: u.name + (' [disabled]' if not u.enabled else '') + ' · ' + u.id[:8] for u in directory.users}
    actor_id = st.sidebar.selectbox('Demo user', list(labels), format_func=labels.get, key='preview_actor')
    actor = next(u for u in directory.users if u.id == actor_id)
    if st.button('Reload directory', key='preview_reload'):
        _refresh(st)
    message = st.session_state.pop('admin_preview_flash', None)
    if message:
        st.success(message)
    if not actor.enabled:
        st.error('This user is disabled. Account access and administration are blocked.')
        return
    # Use service authorization even for the preview's account selector.
    accessible = []
    for account in directory.accounts:
        try:
            store.authorize(actor.identity, account.id)
        except PermissionError:
            continue
        except Exception:
            st.error('Account access could not be verified. Reload the directory.')
            return
        accessible.append(account)
    account_labels = {a.id: a.name + ' · ' + a.broker + ' · ' + a.id[:8]
                      + (' [archived]' if a.archived else '') for a in accessible}
    selected = st.sidebar.multiselect('Accounts available to this user', list(account_labels),
                                     format_func=account_labels.get, key=key + actor.id + ':accounts')
    if selected:
        try:
            scopes = store.select(actor.identity, tuple(selected))
        except Exception:
            st.error('Account access changed. Reload the directory.')
            return
        st.info(str(len(scopes)) + ' account(s) selected. Financial reporting is not connected in this preview.')
    if not actor.admin:
        st.info('Administration requires an enabled administrator. This user can access only permitted accounts.')
        return
    st.caption('Directory revision ' + str(directory.revision))
    users_tab, accounts_tab, access_tab, activity_tab = st.tabs(['Users', 'Brokerage accounts', 'Account access', 'Activity'])
    with users_tab:
        st.subheader('Users')
        st.dataframe([dict(Name=u.name, Role='Administrator' if u.admin else 'Member',
                           Status='Enabled' if u.enabled else 'Disabled') for u in directory.users], hide_index=True)
        form_key = key + 'add_user'
        revision = _form_revision(st, form_key, directory)
        with st.form(form_key):
            name = st.text_input('New user name', key=form_key + ':name')
            if st.form_submit_button('Add demo user'):
                identity = Identity(DEMO_ISSUER, demo_subject_prefix(registry_id) + uuid4().hex)
                _apply(st, store, actor.identity, revision, 'add_user', identity=identity, name=name)
        target_id = st.selectbox('User to manage', list(labels), format_func=labels.get, key=key + 'user_target')
        user = next(u for u in directory.users if u.id == target_id)
        form_key = key + 'edit_user:' + user.id
        revision = _form_revision(st, form_key, directory)
        with st.form(form_key):
            name = st.text_input('Display name', value=user.name, key=form_key + ':name')
            enabled = st.checkbox('User enabled', value=user.enabled, key=form_key + ':enabled')
            admin = st.checkbox('Administrator', value=user.admin, key=form_key + ':admin')
            st.caption('Disabling preserves account history and grants. Re-enabling restores existing grants.')
            if st.form_submit_button('Save user'):
                _apply(st, store, actor.identity, revision, 'configure_user', user_id=user.id,
                       name=name, enabled=enabled, admin=admin)
    with accounts_tab:
        st.subheader('Brokerage accounts')
        owner_labels = {u.id: labels[u.id] for u in directory.users if u.enabled}
        st.dataframe([dict(Account=a.name, Broker=a.broker, Owner=labels[a.owner_id],
                           Status='Archived' if a.archived else 'Active', Account_ID=a.id[:8])
                      for a in directory.accounts], hide_index=True)
        form_key = key + 'add_account'
        revision = _form_revision(st, form_key, directory)
        with st.form(form_key):
            owner = st.selectbox('Account owner', list(owner_labels), format_func=owner_labels.get, key=form_key + ':owner')
            name = st.text_input('New account name', key=form_key + ':name')
            broker = st.text_input('Broker', key=form_key + ':broker')
            if st.form_submit_button('Add account'):
                _apply(st, store, actor.identity, revision, 'add_account', owner_id=owner, name=name, broker=broker)
        if directory.accounts:
            account_labels = {a.id: a.name + ' · ' + labels[a.owner_id] + ' · ' + a.id[:8] for a in directory.accounts}
            account_id = st.selectbox('Account to manage', list(account_labels), format_func=account_labels.get, key=key + 'account_target')
            account = next(a for a in directory.accounts if a.id == account_id)
            form_key = key + 'edit_account:' + account.id
            revision = _form_revision(st, form_key, directory)
            with st.form(form_key):
                name = st.text_input('Account name', value=account.name, key=form_key + ':name')
                archived = st.checkbox('Account archived', value=account.archived, key=form_key + ':archived')
                st.caption('Archived accounts retain their history and allow reading, but block new writes.')
                if st.form_submit_button('Save account'):
                    _apply(st, store, actor.identity, revision, 'configure_account', account_id=account.id,
                           name=name, archived=archived)
    with access_tab:
        st.subheader('Account access')
        st.caption('Owners have edit access. Administrator status alone does not provide financial-record access.')
        if directory.accounts and len(directory.users) > 1:
            account_labels = {a.id: a.name + ' · ' + labels[a.owner_id] + ' · ' + a.id[:8] for a in directory.accounts}
            account_id = st.selectbox('Account for sharing', list(account_labels), format_func=account_labels.get, key=key + 'grant_account')
            account = next(a for a in directory.accounts if a.id == account_id)
            others = {u.id: labels[u.id] for u in directory.users if u.id != account.owner_id}
            user_id = st.selectbox('User receiving access', list(others), format_func=others.get, key=key + 'grant_user:' + account_id)
            existing = next((g.permission for g in directory.grants if g.user_id == user_id and g.account_id == account_id), None)
            choices = {'No shared access': None, 'View': Permission.VIEW, 'Edit': Permission.EDIT}
            form_key = key + 'grant:' + account_id + ':' + user_id
            revision = _form_revision(st, form_key, directory)
            with st.form(form_key):
                choice = st.selectbox('Permission', list(choices), index=list(choices.values()).index(existing), key=form_key + ':permission')
                if st.form_submit_button('Save access'):
                    _apply(st, store, actor.identity, revision, 'set_grant', user_id=user_id,
                           account_id=account_id, permission=choices[choice])
    with activity_tab:
        st.subheader('Administration activity')
        try:
            events = store.activity(actor.identity)
        except Exception:
            st.error('Activity access changed. Reload the directory.')
            return
        if not events:
            st.info('No administration changes yet.')
        else:
            st.dataframe([dict(Sequence=e.sequence, Actor=labels.get(e.actor_id, e.actor_id[:8]),
                               Action=e.action.replace('_', ' '), Target=e.target_id[:8],
                               Details='; '.join(k + ': ' + v for k, v in e.details)) for e in events], hide_index=True)
