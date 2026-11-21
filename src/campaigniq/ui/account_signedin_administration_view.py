"""Local real-sign-in administration; no actor selector or financial actions."""
from contextlib import nullcontext
from campaigniq.account_administration import Permission


def _distinct_labels(names, kind):
    """Keep ID-keyed selections distinct without displaying internal codes."""
    counts={}
    for name in names.values():counts[name.casefold()]=counts.get(name.casefold(),0)+1
    used={name.casefold() for name in names.values()}
    labels={}
    for identifier,name in sorted(names.items()):
        if counts[name.casefold()]==1:
            labels[identifier]=name
            continue
        number=1
        while (name+' · '+kind+' '+str(number)).casefold() in used:number+=1
        labels[identifier]=name+' · '+kind+' '+str(number)
        used.add(labels[identifier].casefold())
    return {identifier:labels[identifier] for identifier in names}


def directory_labels(directory):
    users=_distinct_labels({u.id:u.name+(' [disabled]' if not u.enabled else '')
        for u in directory.users},'User')
    accounts=_distinct_labels({a.id:a.name+' · '+users[a.owner_id]+(' [archived]' if a.archived else '')
        for a in directory.accounts},'Account')
    return users,accounts


def activity_rows(directory):
    """Present immutable audit events without exposing technical identifiers."""
    users,accounts=directory_labels(directory)
    actions={'bind_broker_account_v1':'Broker account linked','grant_access':'Access granted',
        'revoke_access':'Access removed','configure_user':'User settings saved',
        'configure_account':'Account settings saved','add_user':'User registered','add_account':'Account added'}
    rows=[]
    for event in directory.events:
        details=dict(event.details);parts=[]
        if event.action=='bind_broker_account_v1':
            parts=[{'schwab':'Schwab'}.get(details.get('broker'),'Broker'),
                {'securities':'Equities and options','forex':'Forex'}.get(details.get('kind'),'Broker account')]
            suffix=details.get('suffix','')
            if len(suffix)==4 and suffix.isascii() and suffix.isdigit():parts.append('ending '+suffix)
        elif event.action in ('grant_access','revoke_access'):
            recipient=users.get(details.get('user_id'),'Unavailable user')
            permission={'view':'View access','edit':'Edit access'}.get(details.get('permission'),'Access permission recorded')
            parts=[('Shared access removed' if event.action=='revoke_access' else permission)+' for '+recipient]
        elif event.action in ('configure_user','add_user'):
            if 'name' in details:parts.append('Display name: '+details['name'])
            if details.get('enabled') in ('True','False'):parts.append('Enabled' if details['enabled']=='True' else 'Disabled')
            if details.get('admin') in ('True','False'):parts.append('Administrator' if details['admin']=='True' else 'Member')
        elif event.action in ('configure_account','add_account'):
            if 'name' in details:parts.append('Name: '+details['name'])
            if 'owner_id' in details:parts.append('Owner: '+users.get(details['owner_id'],'Unavailable user'))
            if 'broker' in details:parts.append('Broker: '+{'schwab':'Schwab'}.get(details['broker'],details['broker']))
            if details.get('archived') in ('True','False'):parts.append('Archived' if details['archived']=='True' else 'Active')
        rows.append({'Event':event.sequence,'Changed by':users.get(event.actor_id,'Unavailable user'),
            'Action':actions.get(event.action,'Other administration activity'),
            'User or account':users.get(event.target_id,accounts.get(event.target_id,'Unavailable user or account')),
            'Summary':'; '.join(parts) if parts else 'See technical audit details for the recorded information.'})
    return rows


def render(st, service, server_user, *, deployment=False):
    if type(deployment) is not bool:raise ValueError("Explicit application view mode required")
    try:
        directory = service.snapshot(server_user)
    except PermissionError:
        return
    except Exception:
        st.error('Administration access could not be verified.' if deployment else 'Local administration access could not be verified.')
        return
    actor = next(u for u in directory.users if u.identity.issuer == service._issuer
                 and u.identity.subject == server_user.to_dict().get('sub'))
    root = 'signedin-admin:' + service.registry_id + ':' + actor.id + ':'
    epoch_key = root + 'epoch'
    epoch = st.session_state.get(epoch_key, 0)
    key = root + str(epoch) + ':'

    def revision(form_key):
        saved = form_key + ':revision'
        if saved not in st.session_state:
            st.session_state[saved] = directory.revision
        return st.session_state[saved]

    def apply(expected, command, **values):
        try:
            if command == 'add_user':
                service.add_user(server_user, expected, **values)
            else:
                service.execute(server_user, expected, command, **values)
        except PermissionError:
            st.error('Current sign-in no longer has administrator permission. Reload the directory.')
        except ValueError as error:
            st.error(str(error))
        except Exception:
            st.error('The action failed. Reload before retrying.' if deployment else 'The local action failed. Reload before retrying.')
        else:
            st.session_state[epoch_key] = epoch + 1
            st.session_state[root + 'flash'] = 'Directory change saved and audited.' if deployment else 'Local directory change saved and audited. Production unchanged.'
            st.rerun()

    def reload_directory():
        if st.button('Reload directory', key=root + 'reload'):
            st.session_state[epoch_key] = epoch + 1
            st.rerun()

    if deployment:
        st.header('Administration')
    with nullcontext() if deployment else st.expander('Local account administration'):
        st.caption(('Manage users, brokerage accounts, and sharing. ' if deployment else 'Changes affect only this local account directory. Financial data remains read-only. ')+
                   'Administrator status alone grants no access to another owner’s financial records.')
        flash = st.session_state.pop(root + 'flash', None)
        if flash:
            st.success(flash)
        if deployment:
            with st.expander('Directory details'):
                st.caption('Directory revision ' + str(directory.revision))
                reload_directory()
        else:
            reload_directory()
            st.caption('Directory revision ' + str(directory.revision))
        labels,account_labels = directory_labels(directory)
        users_tab, accounts_tab, grants_tab, audit_tab = st.tabs(
            ['Users', 'Brokerage accounts', 'Account access', 'Activity'])
        with users_tab:
            st.dataframe([dict(Name=u.name, Role='Administrator' if u.admin else 'Member',
                               Status='Enabled' if u.enabled else 'Disabled') for u in directory.users], hide_index=True)
            target = st.selectbox('User to manage', list(labels), format_func=labels.get, key=key + 'user')
            user = next(u for u in directory.users if u.id == target)
            form_key = key + 'edit-user:' + user.id
            expected = revision(form_key)
            with st.form(form_key):
                name = st.text_input('Display name', value=user.name, key=form_key + ':name')
                enabled = st.checkbox('User enabled', value=user.enabled, key=form_key + ':enabled')
                admin = st.checkbox('Administrator', value=user.admin, key=form_key + ':admin')
                st.caption('Disabling preserves ownership, grants and history. The last enabled administrator is protected.')
                if st.form_submit_button('Save user'):
                    apply(expected, 'configure_user', user_id=user.id, name=name, enabled=enabled, admin=admin)
            with st.expander('Register another verified Google identity'):
                st.caption('Use the exact Google subject verified independently for this person. '
                           'An email address is not the subject. Registration grants no access to existing accounts.')
                form_key = key + 'new-user'
                expected = revision(form_key)
                with st.form(form_key, clear_on_submit=True):
                    name = st.text_input('New user display name', key=form_key + ':name')
                    subject = st.text_input('Verified Google subject', autocomplete='off', key=form_key + ':subject')
                    confirmed = st.checkbox('I verified this person and their exact Google subject', key=form_key + ':confirm')
                    if st.form_submit_button('Register user'):
                        if not confirmed:
                            st.error('Verify the identity before registering it.')
                        else:
                            apply(expected, 'add_user', name=name, subject=subject)
        with accounts_tab:
            st.dataframe([dict(Account=a.name, Owner=labels[a.owner_id], Broker=a.broker,
                Status='Archived' if a.archived else 'Active',
                Storage='Retained existing workspace' if a.legacy else ('New brokerage account' if deployment else 'New account; financial activation pending'))
                for a in directory.accounts], hide_index=True)
            if directory.accounts:
                target = st.selectbox('Account to manage', list(account_labels), format_func=account_labels.get,
                                      key=key + 'account')
                account = next(a for a in directory.accounts if a.id == target)
                form_key = key + 'edit-account:' + account.id
                expected = revision(form_key)
                with st.form(form_key):
                    name = st.text_input('Account name', value=account.name, key=form_key + ':name')
                    archived = st.checkbox('Account archived', value=account.archived, key=form_key + ':archive')
                    st.caption('Renaming or archiving never changes storage ownership or removes history.')
                    if st.form_submit_button('Save account'):
                        apply(expected, 'configure_account', account_id=account.id, name=name, archived=archived)
            form_key = key + 'new-account'
            expected = revision(form_key)
            with st.form(form_key, clear_on_submit=True):
                owners = {u.id: labels[u.id] for u in directory.users if u.enabled}
                owner = st.selectbox('New account owner', list(owners), format_func=owners.get, key=form_key + ':owner')
                name = st.text_input('New account name', key=form_key + ':name')
                broker = st.text_input('Broker', value='schwab', key=form_key + ':broker')
                st.caption('Adds a brokerage account for its selected owner. Save its broker binding before uploading documents.' if deployment else 'Adds directory metadata only. New-account financial storage and imports are not activated here.')
                if st.form_submit_button('Add account'):
                    apply(expected, 'add_account', owner_id=owner, name=name, broker=broker)
        with grants_tab:
            st.caption('View grants read access. Edit also grants permission to prepare unpublished reporting months. Published months remain read-only.' if deployment else
                'Owners have implicit edit permission. This preview still provides only financial reads, even when an Edit grant is recorded.')
            if directory.accounts and len(directory.users) > 1:
                target = st.selectbox('Account for sharing', list(account_labels), format_func=account_labels.get,
                                      key=key + 'grant-account')
                account = next(a for a in directory.accounts if a.id == target)
                st.caption('Account owner: ' + labels[account.owner_id] + '. Owners do not need shared access.')
                if account.archived:
                    st.caption('Archived accounts are read-only.')
                others = {u.id: labels[u.id] for u in directory.users if u.id != account.owner_id}
                recipient = st.selectbox('User receiving access', list(others), format_func=others.get,
                                        key=key + 'grant-user:' + target)
                existing = next((g.permission for g in directory.grants if g.user_id == recipient
                                 and g.account_id == target), None)
                choices = {'No shared access': None, 'View': Permission.VIEW, 'Edit': Permission.EDIT}
                saved_choice = next(label for label, permission in choices.items() if permission == existing)
                st.write('Saved access: ' + saved_choice)
                if not next(u for u in directory.users if u.id == recipient).enabled:
                    st.caption('This user is disabled and cannot access the account while disabled.')
                form_key = key + 'grant:' + target + ':' + recipient
                expected = revision(form_key)
                with st.form(form_key):
                    choice = st.selectbox('Permission', list(choices), index=list(choices.values()).index(existing),
                                          key=form_key + ':permission')
                    st.caption('Changes take effect only after you select Save access.')
                    if st.form_submit_button('Save access'):
                        apply(expected, 'set_grant', user_id=recipient, account_id=target, permission=choices[choice])
            else:
                st.info('Register another verified user before sharing an account.')
        with audit_tab:
            # Read only the already-authorized snapshot; never rewrite events.
            rows=activity_rows(directory)
            if not rows:
                st.info('No administration activity has been recorded.')
            else:
                st.caption('User and account labels reflect the current directory. Summaries describe the settings recorded at the time of each event.')
                st.dataframe(rows,hide_index=True,width='stretch')
                st.subheader('Activity details')
                events={e.sequence:e for e in directory.events}
                summaries={row['Event']:row for row in rows}
                selected=st.selectbox('Activity event',list(events),key=key+'audit-event',
                    format_func=lambda number:'Event '+str(number)+' · '+summaries[number]['Action'])
                row=summaries[selected]
                st.write('Changed by: '+row['Changed by'])
                st.write('User or account: '+row['User or account'])
                st.write('Summary: '+row['Summary'])
                with st.expander('Technical audit details'):
                    event=events[selected]
                    st.json(dict(sequence=event.sequence,actor_id=event.actor_id,action=event.action,
                        target_id=event.target_id,details=dict(event.details)))
