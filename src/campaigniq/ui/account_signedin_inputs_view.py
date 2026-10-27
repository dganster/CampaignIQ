"""Saved monthly inputs for new real-sign-in LOCAL accounts; no publication."""
from campaigniq.account_import_preview import UPLOAD_ROLES
from campaigniq.broker_account_binding import KINDS
from campaigniq.import_draft_bridge import RestoredUpload


def render(st, service, server_user):
    try:
        overview = service.gateway.overview(server_user)
        with st.expander('Monthly inputs for new brokerage accounts'):
            st.caption('Local development only. Existing workspace records are untouched. '
                       'Saving documents and checking inputs does not reconcile or publish a month.')
            if not overview.accounts:
                st.info('Add a new brokerage account in Local account administration first.')
                return
            choices = {a.id:a for a in overview.accounts}
            account_id = st.selectbox('New brokerage account', list(choices),
                format_func=lambda key: choices[key].name + (' [archived]' if choices[key].archived else '') + ' · ' + key[:8],
                key='new-account-selection:' + overview.user_id + ':' + service.gateway._directory.registry_id)
            account = choices[account_id]
            key = 'signedin-input:' + service.gateway._directory.registry_id + ':' + overview.user_id + ':' + account_id
            if not service.gateway.storage_ready(server_user):
                if overview.administrator:
                    st.info('Initialize the separate local input tables before saving drafts.')
                    if st.button('Enable local monthly input storage', key=key + ':initialize'):
                        service.gateway.initialize(server_user)
                        st.rerun()
                else:
                    st.info('An administrator must enable local monthly input storage.')
                return
            if account.suffixes:
                st.success('Broker account bound: ' + '; '.join(kind.capitalize() + ' ending ' + suffix
                           for kind, suffix in account.suffixes))
            else:
                st.info('Bind the full broker account identifier before saving documents.')
            if overview.administrator and not account.archived:
                form_key = key + ':binding:' + str(st.session_state.get(key + ':binding-epoch', 0))
                revision_key = form_key + ':revision'
                if revision_key not in st.session_state:
                    st.session_state[revision_key] = overview.directory_revision
                with st.form(form_key, clear_on_submit=True):
                    kind = st.selectbox('Account identifier type', KINDS, key=form_key + ':kind')
                    number = st.text_input('Full broker account identifier', autocomplete='off', key=form_key + ':number')
                    confirm = st.checkbox('Confirm replacement of the existing identifier', key=form_key + ':confirm')
                    st.caption('Schwab securities and Forex identifiers are separate. Only the final four digits are displayed. '
                               'Masked-only crypto evidence is not supported yet.')
                    if st.form_submit_button('Save broker binding'):
                        service.gateway.bind(server_user, account_id, kind, number,
                            expected_revision=st.session_state[revision_key], confirm_replace=confirm)
                        st.session_state[key + ':binding-epoch'] = st.session_state.get(key + ':binding-epoch', 0) + 1
                        st.rerun()
                if st.button('Reload broker binding', key=key + ':reload-binding'):
                    st.session_state[key + ':binding-epoch'] = st.session_state.get(key + ':binding-epoch', 0) + 1
                    st.rerun()
            if account.writable:
                with st.form(key + ':create'):
                    year = int(st.number_input('Year', min_value=1900, max_value=2200, value=2025, key=key + ':year'))
                    month = int(st.number_input('Month', min_value=1, max_value=12, value=1, key=key + ':month'))
                    if st.form_submit_button('Create or resume draft'):
                        service.create(server_user, account_id, year, month)
                        st.rerun()
            else:
                st.info('View access only. Saved documents remain readable; uploads are disabled.')
            drafts = {str(d.id):d for d in service.drafts(server_user, account_id)}
            if not drafts:
                st.info('No saved drafts for this new account.')
                return
            selected = st.selectbox('Saved month', list(drafts),
                format_func=lambda value: f'{drafts[value].year:04d}-{drafts[value].month:02d}', key=key + ':draft')
            draft = drafts[selected]
            key += ':' + selected + ':' + str(draft.revision)
            st.caption('Saved document revision ' + str(draft.revision))
            if account.writable:
                with st.form(key + ':upload', clear_on_submit=True):
                    role = st.selectbox('Document role', UPLOAD_ROLES, key=key + ':role')
                    upload = st.file_uploader('Broker document', type=['pdf', 'txt', 'csv'], key=key + ':file')
                    replace = st.checkbox('Confirm replacement of this role; preserve the previous original', key=key + ':replace')
                    if st.form_submit_button('Save document'):
                        if upload is None:
                            st.error('Choose a document first.')
                        else:
                            service.save(server_user, account_id, draft.id, draft.revision, role,
                                RestoredUpload(upload.name, upload.type, upload.getvalue()), replace=replace)
                            st.rerun()
            forex = st.checkbox('Forex applicable', value=True, key=key + ':forex')
            crypto = st.checkbox('Crypto applicable', value=True, key=key + ':crypto')
            if st.button('Preview saved inputs', key=key + ':preview'):
                result = service.preview(server_user, account_id, draft.id, draft.revision,
                    forex_applicable=forex, crypto_applicable=crypto)
                st.dataframe([dict(Input=v.role, Valid=v.valid, Detail=v.message) for v in result.validations], hide_index=True)
                if result.inputs_ready:
                    st.success('Required input checks passed. Nothing was reconciled or published.')
                else:
                    st.info('Complete the missing or invalid inputs. No data was published.')
    except PermissionError:
        st.error('Current sign-in no longer has permission for this account. Reload the preview.')
    except ValueError as error:
        st.error(str(error))
    except Exception:
        st.error('Local inputs could not be verified. Reload before retrying.')
