"""Saved inputs, reconciliation and explicit LOCAL new-account publication."""
from campaigniq.account_import_preview import UPLOAD_ROLES
from campaigniq.broker_account_binding import KINDS
from campaigniq.import_draft_bridge import RestoredUpload


def render(st, service, server_user, *, selected_account_id=None, deployment=False):
    if type(deployment) is not bool:raise ValueError("Explicit application view mode required")
    try:
        overview = service.gateway.overview(server_user)
        with st.expander('Monthly inputs for new brokerage accounts'):
            st.caption('Save source documents, check closing inventory, and publish a reconciled month for the selected account.' if deployment else
                'Local development only. Existing workspace records are untouched. Publish locally stores a reconciled month only in the local development database.')
            if not overview.accounts:
                st.info('Add a new brokerage account in Account administration first.' if deployment else 'Add a new brokerage account in Local account administration first.')
                return
            choices = {a.id:a for a in overview.accounts}
            if selected_account_id is not None and selected_account_id not in choices:
                raise PermissionError('Selected account is unavailable')
            account_id = selected_account_id if selected_account_id is not None else st.selectbox('New brokerage account', list(choices),
                format_func=lambda key: choices[key].name + (' [archived]' if choices[key].archived else '') + ' · ' + key[:8],
                key='new-account-selection:' + overview.user_id + ':' + service.gateway._directory.registry_id)
            account = choices[account_id]
            key = 'signedin-input:' + service.gateway._directory.registry_id + ':' + overview.user_id + ':' + account_id
            if not service.gateway.storage_ready(server_user):
                if deployment:
                    st.error('Account storage could not be verified. Contact an administrator.')
                elif overview.administrator:
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
            published = service.published(server_user, account_id, draft.id, draft.revision)
            if published:
                st.success('This month is published. Saved originals are preserved.' if deployment else 'This month is published locally. Saved originals are preserved.')
            if account.writable and not published:
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
            if st.button('Preview closing inventory', key=key + ':reconcile'):
                result = service.reconcile(server_user, account_id, draft.id, draft.revision,
                    forex_applicable=forex, crypto_applicable=crypto)
                st.caption('Read-only quantity reconciliation. No financial approval or publication has been performed.')
                if result.inventory_reconciled:
                    st.success('Closing inventory reconciles with the supplied statement.')
                else:
                    st.warning('Closing inventory or source controls do not reconcile. Review the differences.')
                if result.closing_quantities:
                    st.dataframe([dict(Instrument=name, Quantity=quantity) for name, quantity in result.closing_quantities], hide_index=True)
                if result.mismatches:
                    st.dataframe([dict(Instrument=row.instrument, Computed=row.computed, Statement=row.statement,
                        Difference=row.difference) for row in result.mismatches], hide_index=True)
                for finding in result.source_findings:
                    st.warning(finding)
            if published and st.button('View published results', key=key + ':results'):
                result = service.results(server_user, account_id, draft.id, draft.revision)
                st.subheader('Published results · ' + result.month)
                st.caption(('Saved generation • Source revision ' if deployment else 'Saved local generation • Source revision ') + str(result.revision) +
                    ' • ' + str(result.original_documents) + ' original documents verified. Read-only view.')
                st.write('Closing positions')
                if result.positions:
                    st.dataframe([dict(Instrument=name, Quantity=quantity) for name, quantity in result.positions], hide_index=True)
                else:
                    st.info('No open positions in the published lot state.')
                with st.expander('Closing lots and source basis'):
                    st.dataframe([dict(Instrument=row[0], Quantity=row[1], Lot=row[2], Basis=row[3],
                        Source=row[4], Campaign=row[5]) for row in result.lots], hide_index=True)
                st.write('Broker realized gain/loss, USD: ' + result.brokerage_realized_usd)
                if result.realized:
                    st.dataframe([dict(Date=row[0], Instrument=row[1], Quantity=row[2], Proceeds=row[3],
                        Basis=row[4], Gain_loss=row[5]) for row in result.realized], hide_index=True)
                else:
                    st.caption('No broker realized records for this month.')
                if result.forex_applicable:
                    st.write('Forex settled P/L, USD: ' + result.forex_settled_usd)
                    st.caption('Settlement P/L only; financing and other cash activity are not included in this subtotal.')
                    st.dataframe([dict(Reference=row[0], Instrument=row[1], Settled_PL_USD=row[2], Campaign=row[3])
                        for row in result.forex_settlements], hide_index=True)
                if result.crypto_applicable:
                    st.write('Crypto evidence status: ' + result.crypto_status)
                    st.dataframe([dict(Instrument=name, Quantity=quantity) for name, quantity in result.crypto_positions], hide_index=True)
                    for warning in result.crypto_warnings:
                        st.warning(warning)
            publication_enabled=getattr(service,'publication_enabled',True)
            if account.writable and not published and not publication_enabled:
                st.caption('Publication is not enabled for this application.')
            if account.writable and not published and publication_enabled:
                with st.form(key + ':publication:' + str(forex) + ':' + str(crypto)):
                    confirm = st.checkbox((f'I authorize publication for {account.name} · {draft.year:04d}-{draft.month:02d}' if deployment else 'I authorize publication to the local development database'),
                        key=key + ':authorize:' + str(forex) + ':' + str(crypto))
                    st.caption('Rechecks sign-in, account, documents and closing inventory before committing. '
                               'Existing published months cannot be replaced.')
                    if st.form_submit_button('Publish month' if deployment else 'Publish locally'):
                        service.publish(server_user, account_id, draft.id, draft.revision,
                            **({"confirm_publication":confirm} if deployment else {"authorize_local_publication":confirm}),
                            forex_applicable=forex, crypto_applicable=crypto)
                        st.rerun()
    except PermissionError:
        st.error('Current sign-in no longer has permission for this account. Reload the account list.' if deployment else 'Current sign-in no longer has permission for this account. Reload the preview.')
    except ValueError as error:
        st.error(str(error))
    except Exception:
        st.error('Account inputs could not be verified. Reload before retrying.' if deployment else 'Local inputs could not be verified. Reload before retrying.')
