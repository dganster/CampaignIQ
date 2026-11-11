"""Saved inputs, reconciliation and explicit LOCAL new-account publication."""
from hashlib import sha256
from contextlib import nullcontext
from calendar import monthrange
from datetime import date, timedelta

from campaigniq.account_import_preview import UPLOAD_ROLES
from campaigniq.broker_account_binding import KINDS
from campaigniq.import_draft_bridge import RestoredUpload


def render(st, service, server_user, *, selected_account_id=None, deployment=False):
    if type(deployment) is not bool:raise ValueError("Explicit application view mode required")
    try:
        overview = service.gateway.overview(server_user)
        with st.expander('Monthly inputs' if deployment and getattr(getattr(service.gateway,'_backend',None),'_continue_existing',False) else 'Monthly inputs for new brokerage accounts'):
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
            guided=deployment and callable(getattr(service,'saved_documents',None))
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
                with st.expander('Account settings',expanded=not bool(account.suffixes)) if guided else nullcontext():
                    form_key = key + ':binding:' + str(st.session_state.get(key + ':binding-epoch', 0))
                    revision_key = form_key + ':revision'
                    if revision_key not in st.session_state:
                        st.session_state[revision_key] = overview.directory_revision
                    with st.form(form_key, clear_on_submit=True):
                        kind = st.selectbox('Account identifier type', KINDS, key=form_key + ':kind')
                        number = st.text_input('Full broker account identifier', autocomplete='off', key=form_key + ':number')
                        confirm = st.checkbox('Confirm replacement of the existing identifier', key=form_key + ':confirm')
                        st.caption('Schwab securities and Forex identifiers are separate. Only the final four digits are displayed. '
                                   'Crypto statements use a separate owner source review below.')
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
                    year = int(st.number_input('Year', min_value=1900, max_value=2200, value=2026 if deployment and getattr(getattr(service.gateway,'_backend',None),'_continue_existing',False) else 2025, key=key + ':year'))
                    month = int(st.number_input('Month', min_value=1, max_value=12, value=10 if deployment and getattr(getattr(service.gateway,'_backend',None),'_continue_existing',False) else 1, key=key + ':month'))
                    if st.form_submit_button('Create or resume draft'):
                        service.create(server_user, account_id, year, month)
                        st.rerun()
            else:
                st.info('View access only. Saved documents remain readable; uploads are disabled.')
            drafts = {str(d.id):d for d in service.drafts(server_user, account_id)}
            if not drafts:
                st.info('No saved monthly drafts.')
                return
            selected = st.selectbox('Saved month', list(drafts),
                format_func=lambda value: f'{drafts[value].year:04d}-{drafts[value].month:02d}', key=key + ':draft')
            draft = drafts[selected]
            stable_key=key+':'+selected
            key=stable_key+':'+str(draft.revision)
            st.caption('Saved document revision ' + str(draft.revision))
            published = service.published(server_user, account_id, draft.id, draft.revision)
            if published:
                st.success('This month is published. Saved originals are preserved.' if deployment else 'This month is published locally. Saved originals are preserved.')
            guided=deployment and callable(getattr(service,'saved_documents',None))
            no_trading_path=False
            markets=('Equities and options','Forex','Futures','Crypto')
            if guided and published:
                from campaigniq.ui.account_draft_recovery_view import render as render_saved_documents
                render_saved_documents(st,service,server_user,account_id,draft,key=key,writable=False,published=True)
                if st.button('View published results',key=key+':results'):
                    show_published_results(st,service,server_user,account_id,draft,deployment=deployment)
                return
            if guided:
                if st.session_state.get(stable_key+':notice'):st.success(st.session_state[stable_key+':notice'])
                st.subheader('1. Activity this month')
                mode=st.radio('Choose the monthly workflow',('I traded','No trading activity'),
                    index=1 if service.no_trading_status(server_user,account_id,draft.id,draft.revision) else (0 if published else None),
                    key=stable_key+':activity',disabled=published)
                no_trading_path=mode=='No trading activity'
                from campaigniq.ui.account_draft_recovery_view import render as render_saved_documents
                saved_roles=render_saved_documents(st,service,server_user,account_id,draft,key=key,writable=account.writable,published=published)
                if mode is None:
                    st.info('Choose whether you traded this month to see the documents you need. You can correct saved uploads above.')
                    return
                if not no_trading_path:
                    defaults=['Equities and options']
                    for name,token in (('Forex','forex'),('Futures','futures'),('Crypto','crypto')):
                        if any(token in r for r in saved_roles):defaults.append(name)
                    markets=st.multiselect('Markets to include this month',markets,default=defaults,key=stable_key+':markets',disabled=published)
                if not no_trading_path and not markets:
                    st.info('Select at least one market to continue.');return
                st.subheader('2. Documents')
                if no_trading_path:st.caption('Save the monthly Brokerage Statement. Empty trading reports are not required. Remove mistaken uploads using the saved-document controls above.')
            upload_locked=guided and 'no_trading_owner_confirmation_v1' in saved_roles
            if upload_locked:st.info('A no-trading confirmation is saved. To change the inputs, remove that confirmation using Correct this draft above; then save the updated documents and review again.')
            if account.writable and not published and not upload_locked:
                roles=tuple(r for r in UPLOAD_ROLES if r!='schwab_crypto_statement') if deployment and getattr(service,'multi_asset_enabled',False) else UPLOAD_ROLES
                if guided:roles=tuple(r for r in roles if ('forex' not in r or 'Forex' in markets) and ('crypto' not in r or 'Crypto' in markets))
                if no_trading_path:roles=('schwab_brokerage_statement',)
                from campaigniq.account_draft_recovery import label
                if guided and len(roles)==1:
                    role=roles[0];st.caption('Document: '+label(role))
                else:
                    role = st.selectbox('Document role', roles, index=None, format_func=label,
                        placeholder='Select a document type…', key=key + ':role')
                if role == 'schwab_forex_transaction_report':
                    start = date(draft.year, draft.month, 1) - timedelta(days=1)
                    end = date(draft.year, draft.month, monthrange(draft.year, draft.month)[1])
                    st.info(f'Forex Transaction Report: export {start:%B %d, %Y} through {end:%B %d, %Y}.')
                with st.form(key + ':upload', clear_on_submit=False):
                    upload = st.file_uploader('Broker document', type=['pdf', 'txt', 'csv'], key=key + ':file')
                    replace = st.checkbox('Replace the saved document; preserve its original in history', key=key+':replace:'+str(role)) if guided and role in saved_roles else (False if guided else st.checkbox('Confirm replacement of this role; preserve the previous original',key=key+':replace'))
                    associated = st.checkbox('I confirm this Gain/Loss report belongs to the selected account and month', key=key + ':associate') if deployment and role == 'schwab_realized_gain_loss' else False
                    if st.form_submit_button('Save document', disabled=role is None):
                        if role is None:
                            st.error('Select a document type first.')
                        elif upload is None:
                            st.error('Choose a document first.')
                        else:
                            restored=RestoredUpload(upload.name, upload.type, upload.getvalue())
                            if associated:
                                if role!='schwab_realized_gain_loss':raise ValueError('Same-account confirmation applies only to the Gain/Loss report')
                                service.save_associated_gain_loss(server_user,account_id,draft.id,draft.revision,restored,
                                    confirm_same_account=True,replace=replace)
                            else:
                                service.save(server_user, account_id, draft.id, draft.revision, role,restored,replace=replace)
                            if guided:st.session_state[stable_key+':notice']='Document saved. It appears in Saved documents above.'
                            st.rerun()
            if deployment and not no_trading_path and 'Futures' in markets and hasattr(service, 'confirm_empty_futures_opening'):
                with st.expander('Futures opening positions'):
                    as_of = date(draft.year,draft.month,1) - timedelta(days=1)
                    st.caption('For accounts resuming futures trading with no carried positions. '
                        'This records your confirmation, not independent broker verification, and does not publish a month.')
                    if st.button('Check saved futures opening confirmation', key=key + ':futures-opening-status'):
                        status = service.futures_opening_status(server_user,account_id,draft.id,draft.revision)
                        if status['confirmed']:
                            st.success('No futures or futures-options positions as of ' + status['opening_as_of'] +
                                ' — confirmed by account owner; not independently broker-verified.')
                        else:
                            st.info('No saved futures opening confirmation for this month.')
                    if account.writable and not published:
                        with st.form(key + ':futures-opening'):
                            empty = st.checkbox(f'I confirm this account had no futures or futures-options positions open on {as_of:%B %d, %Y}',
                                key=key + ':empty-futures')
                            if st.form_submit_button('Save empty futures opening confirmation'):
                                service.confirm_empty_futures_opening(server_user,account_id,draft.id,draft.revision,
                                    confirm_empty_opening=empty)
                                st.rerun()
            no_trading=False
            if guided:st.subheader('3. Review and publish')
            if deployment and (not guided or no_trading_path) and callable(getattr(service,'no_trading_preview',None)):
                from campaigniq.ui.account_no_trading_view import render as render_no_trading
                no_trading=render_no_trading(st,service,server_user,account_id,draft,key=key,writable=account.writable,published=published,guided=guided)
            if deployment and not no_trading_path and 'Futures' in markets and hasattr(service, 'futures_preview'):
                with st.expander('Futures statement and accounting preview'):
                    st.caption('Save the empty opening confirmation first. This separate preview does not publish a month.')
                    if account.writable and not published:
                        with st.form(key + ':futures-statement', clear_on_submit=False):
                            futures_pdf = st.file_uploader('Futures monthly statement PDF', type=['pdf'], key=key + ':futures-pdf')
                            same = st.checkbox('I confirm this futures statement belongs to the selected account and month', key=key + ':futures-association')
                            change = st.checkbox('Replace saved futures statement; preserve prior original', key=key + ':futures-replace')
                            st.caption('This records your account association. The futures GMI number is separate from securities and Forex identifiers.')
                            if st.form_submit_button('Save futures statement'):
                                if futures_pdf is None:
                                    st.error('Choose the futures statement PDF first.')
                                else:
                                    service.save_futures_statement(server_user,account_id,draft.id,draft.revision,
                                        RestoredUpload(futures_pdf.name,futures_pdf.type,futures_pdf.getvalue()),
                                        confirm_same_account=same,replace=change)
                                    st.rerun()
                    if hasattr(service, 'confirm_empty_futures_closing') and account.writable and not published:
                        end_of_month = date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
                        with st.form(key + ':futures-closing'):
                            empty_end = st.checkbox(f'I confirm this account had no futures or futures-options positions open on {end_of_month:%B %d, %Y}',
                                key=key + ':empty-ending-futures')
                            if st.form_submit_button('Save empty futures ending confirmation'):
                                service.confirm_empty_futures_closing(server_user,account_id,draft.id,draft.revision,
                                    confirm_empty_closing=empty_end)
                                st.rerun()
                    if st.button('Preview saved futures accounting', key=key + ':futures-preview'):
                        futures = service.futures_preview(server_user,account_id,draft.id,draft.revision)
                        st.write('Futures/options realized result after fees, USD: ' + futures['realized_net_usd'])
                        st.dataframe([dict(Component='Option result before fees', USD=futures['option_realized_usd']),
                            dict(Component='Underlying futures result before fees', USD=futures['futures_realized_usd']),
                            dict(Component='Fees', USD=futures['fees_cash_usd'])], hide_index=True)
                        st.caption('Opening positions and statement account association: confirmed by account owner, not independently broker-verified. '
                            'Cash sweeps are transfers and are excluded from the trading result.')
                        if futures.get('closing_positions_confirmed_by_owner'):
                            st.caption('Ending positions also confirmed empty by account owner; not independently broker-verified.')
                        else:
                            st.info('Ending positions are calculated only. Save the owner ending-position confirmation before the monthly check.')
                        st.write('Calculated ending futures positions', futures['calculated_ending_futures'])
                        st.write('Calculated ending futures-option positions', futures['calculated_ending_options'])
                        for finding in futures['blockers']:
                            st.warning(finding)
                        st.info('Read-only futures preview. No month was published.')
            if deployment and not no_trading_path and 'Crypto' in markets and getattr(service,'multi_asset_enabled',False):
                from campaigniq.ui.account_crypto_sources_view import render as render_crypto_sources
                render_crypto_sources(st,service,server_user,account_id,draft,key=key,writable=account.writable,published=published)
            if deployment and not no_trading_path and 'Forex' in markets and hasattr(service,'review_forex_cash_sources'):
                from campaigniq.ui.account_forex_cash_review_view import render as render_forex_cash
                render_forex_cash(st,service,server_user,account_id,draft,key=key,writable=account.writable,published=published)
            forex = ('Forex' in markets and not no_trading_path) if guided else st.checkbox('Forex applicable', value=not no_trading, key=key + (':forex:no-trading' if no_trading else ':forex'))
            crypto = ('Crypto' in markets and not no_trading_path) if guided else st.checkbox('Crypto applicable', value=not no_trading, key=key + (':crypto:no-trading' if no_trading else ':crypto'))
            if deployment and not guided and not no_trading_path and not no_trading and hasattr(service, 'futures_monthly_preview'):
                with st.expander('Monthly preview including futures'):
                    st.caption('Check securities/Forex inventory, futures accounting and approved crypto sources together. No month is published by this check.' if getattr(service,'multi_asset_enabled',False) else 'Read-only securities/Forex inventory, separate futures accounting, and crypto source controls. Publication remains disabled for futures months.')
                    if st.button('Check monthly sources including futures', key=key + ':monthly-futures'):
                        monthly = service.futures_monthly_preview(server_user,account_id,draft.id,draft.revision,
                            forex_applicable=forex,crypto_applicable=crypto)
                        st.dataframe(monthly['validations'],hide_index=True)
                        st.write('Securities/Forex closing inventory reconciled:',monthly['securities_inventory_reconciled'])
                        st.write('Futures/options result after fees, USD:',monthly['futures']['realized_net_usd'])
                        if monthly.get('approved_forex_policy'):
                            st.caption('Forex uses the verified owner-approved daily-session basis. Original CSV differences and daily coverage limits remain retained.')
                        st.caption('Full original Trade History preserved. A labelled temporary projection separates already-verified futures rows for the securities checks.')
                        for finding in monthly['source_findings']:
                            st.warning(finding)
                        if monthly['inventory_mismatches']:
                            st.dataframe(monthly['inventory_mismatches'],hide_index=True)
                        if getattr(service,'multi_asset_enabled',False) and monthly['all_source_controls_ready']:
                            st.success('Monthly source and inventory checks passed. Use the separate month confirmation to publish.')
                        else:st.info(monthly['publication_blocker'])
            if not guided and not no_trading_path and st.button('Preview saved inputs', key=key + ':preview'):
                result = service.preview(server_user, account_id, draft.id, draft.revision,
                    forex_applicable=forex, crypto_applicable=crypto)
                st.dataframe([dict(Input=v.role, Valid=v.valid, Detail=v.message) for v in result.validations], hide_index=True)
                if result.inputs_ready:
                    st.success('Required input checks passed. Nothing was reconciled or published.')
                else:
                    st.info('Complete the missing or invalid inputs. No data was published.')
            if not guided and not no_trading_path and st.button('Preview closing inventory', key=key + ':reconcile'):
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
            if published and st.button('View published results',key=key+':results'):
                show_published_results(st,service,server_user,account_id,draft,deployment=deployment)
            review_ready=False
            if guided:
                from campaigniq.ui.monthly_review import render as render_review
                signature=(account_id,str(draft.id),draft.revision,overview.directory_revision,mode,tuple(sorted(markets)) if not no_trading_path else ())
                review_ready=render_review(st,service,server_user,account_id,draft,key=key,signature=signature,forex=forex,crypto=crypto,futures='Futures' in markets and not no_trading_path,no_trading=no_trading_path)
            authorization_context=sha256(repr(signature).encode()).hexdigest() if guided else str(forex)+':'+str(crypto)
            publication_enabled=getattr(service,'publication_enabled',True)
            if account.writable and not published and not publication_enabled:
                st.caption('Publication is not enabled for this application.')
            if account.writable and not published and publication_enabled:
                with st.form(key+':publication:'+authorization_context):
                    confirm = st.checkbox((f'I authorize publication for {account.name} · {draft.year:04d}-{draft.month:02d}' if deployment else 'I authorize publication to the local development database'),
                        key=key+':authorize:'+authorization_context)
                    st.caption('Rechecks sign-in, account, documents and closing inventory before committing. '
                               'Existing published months cannot be replaced.')
                    if guided and no_trading_path and not no_trading:st.caption('Review the statement and save the no-trading confirmation above before publishing.')
                    if st.form_submit_button('Publish month' if deployment else 'Publish locally',disabled=guided and not review_ready):
                        if guided and not review_ready:raise ValueError('Review the current month before publishing')
                        if guided:st.session_state.pop(key+':monthly-review',None)
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


def show_published_results(st,service,server_user,account_id,draft,*,deployment):
    from decimal import Decimal
    from campaigniq.ui.income_performance import money
    result = service.results(server_user, account_id, draft.id, draft.revision)
    st.subheader('Published results · ' + result.month)
    st.caption(('Saved generation • Source revision ' if deployment else 'Saved local generation • Source revision ') + str(result.revision) +
        ' • ' + str(result.original_documents) + ' original documents verified. Read-only view.')
    if getattr(result,'no_trading_month',False):
        from decimal import Decimal
        st.write('No trading activity. Published lots and campaign identities carried forward unchanged.')
        st.write('Statement cash dividends / interest: '+f'${Decimal(result.cash_income_usd):,.2f}'+' (separate from realized trading P/L)')
    st.write('Closing positions')
    if result.positions:
        st.dataframe([dict(Instrument=name, Quantity=quantity) for name, quantity in result.positions], hide_index=True)
    else:
        st.info('No open positions in the published lot state.')
    with st.expander('Closing lots and source basis'):
        st.dataframe([dict(Instrument=row[0], Quantity=row[1], Lot=row[2], Basis=money(Decimal(row[3])) if row[3]!='Unknown' else row[3],
            Source=row[4], Campaign=row[5]) for row in result.lots], hide_index=True)
    from campaigniq.ui.income_performance import money
    from decimal import Decimal
    st.write('Broker realized gain/loss: ' + money(Decimal(result.brokerage_realized_usd)))
    if result.realized:
        st.dataframe([dict(Date=row[0], Instrument=row[1], Quantity=row[2], Proceeds=money(Decimal(row[3])),
            Basis=money(Decimal(row[4])), Gain_loss=money(Decimal(row[5]))) for row in result.realized], hide_index=True)
    else:
        st.caption('No broker realized records for this month.')
    from campaigniq.ui.income_performance import show_statement
    from decimal import Decimal
    trading=Decimal(result.brokerage_realized_usd)+Decimal(result.forex_settled_usd)+Decimal(result.futures_realized_usd)
    with st.expander('Dividends, interest and performance',expanded=True):
        show_statement(st,getattr(result,'income_evidence',None),realized=trading,
            known_forex_financing=result.forex_financing_usd if result.forex_policy_approved else None)
    if result.forex_applicable:
        st.write('Forex settled P/L: ' + money(Decimal(result.forex_settled_usd)))
        st.caption('Settlement P/L only; financing and other cash activity are not included in this subtotal.')
        if getattr(result,'forex_policy_approved',False):
            st.write('Approved daily-session Forex financing: '+money(Decimal(result.forex_financing_usd)))
            st.write('Forex cash: '+money(Decimal(result.forex_opening_cash_usd))+' → '+money(Decimal(result.forex_ending_cash_usd))+
                '; net change '+money(Decimal(result.forex_net_cash_change_usd)))
            st.caption('Owner-approved daily-session basis. Original CSV differences and daily coverage limits remain retained.')
        st.dataframe([dict(Reference=row[0], Instrument=row[1], Settled_PL_USD=money(Decimal(row[2])), Campaign=row[3])
            for row in result.forex_settlements], hide_index=True)
    if getattr(result,'futures_applicable',False):
        st.write('Futures/options net realized P/L: ' + money(Decimal(result.futures_realized_usd)))
        st.caption(result.futures_evidence_status)
    if result.crypto_applicable:
        st.write('Crypto evidence status: ' + result.crypto_status)
        st.dataframe([dict(Instrument=name, Quantity=quantity) for name, quantity in result.crypto_positions], hide_index=True)
        for warning in result.crypto_warnings:
            st.warning(warning)
