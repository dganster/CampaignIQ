"""Application facade checks on the existing restricted disposable-clone runner."""
from decimal import Decimal
from campaigniq.account_deployment_application import DeploymentApplication
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.account_backend_rehearsal import ServerSession
from campaigniq.account_administration import Permission
from campaigniq.account_signedin_comparison import PublishedSelection
from campaigniq.ui.preferences import DEFAULT_PREFERENCES
from campaigniq.dashboard_sources import load_dashboard_sources


def verify_application(c,passed):
    before=c.snapshot()
    disabled=DeploymentApplication(c.reader,c.writer,c.store)
    app=DeploymentApplication(c.reader,c.writer,c.store,publication_enabled=True)
    assert c.snapshot()==before and disabled.inputs.publication_enabled is False
    assert app.preferences.store is c.store and app.inputs._publisher.writer is c.writer
    assert app.inputs._inputs.gateway._directory is c.reader
    passed('application_composes_three_distinct_restricted_roles_without_connection_side_effects_or_implicit_publication')
    def command(action,**values):return app.administration.execute(c.user,app.administration.snapshot(c.user).revision,action,**values)
    directory=app.administration.snapshot(c.user);owner=directory.user(c.user.identity).id
    try:app.administration.snapshot(c.foreign)
    except PermissionError:pass
    else:raise AssertionError('Member read administration directory')
    command('add_account',owner_id=owner,name='Application lifecycle',broker='schwab')
    account=next(a.id for a in app.administration.snapshot(c.user).accounts if a.name=='Application lifecycle')
    overview=app.inputs.gateway.overview(c.user)
    assert overview.directory_revision==app.administration.snapshot(c.user).revision
    assert account in {a.id for a in overview.accounts}
    assert all(not a.legacy for a in app.choices(c.user).accounts if a.id in {x.id for x in overview.accounts})
    assert app.inputs.gateway.storage_ready(c.user)
    try:app.inputs.gateway.initialize(c.user)
    except PermissionError:pass
    else:raise AssertionError('Application initialized schema')
    passed('current_server_signin_administration_and_new_account_choices_share_the_reviewed_directory_without_schema_initialization')
    revision=overview.directory_revision
    app.inputs.gateway.bind(c.user,account,'securities','12340000',expected_revision=revision)
    draft=app.inputs.create(c.user,account,2025,1);source_revision=0
    bad=next(iter(synthetic_sources('87654321').items()))
    before=c.snapshot()
    try:app.inputs.save(c.user,account,draft.id,0,*bad)
    except ValueError:pass
    else:raise AssertionError('Wrong-account application upload accepted')
    assert c.snapshot()==before
    for role,upload in synthetic_sources('12340000').items():
        source_revision=app.inputs.save(c.user,account,draft.id,source_revision,role,upload)
    assert source_revision==4 and not app.inputs.published(c.user,account,draft.id,source_revision)
    passed('application_binding_and_uploads_use_current_identity_and_preserve_wrong_account_rejection')
    before=c.snapshot()
    preview=app.inputs.preview(c.user,account,draft.id,source_revision,forex_applicable=False,crypto_applicable=False)
    reconciliation=app.inputs.reconcile(c.user,account,draft.id,source_revision,forex_applicable=False,crypto_applicable=False)
    assert preview.inputs_ready and reconciliation.inventory_reconciled and c.snapshot()==before
    passed('application_preview_and_closing_reconciliation_are_readonly_and_reconcile_to_the_supplied_sources')
    for service,confirmed in ((disabled.inputs,True),(app.inputs,False)):
        try:service.publish(c.user,account,draft.id,source_revision,confirm_publication=confirmed,forex_applicable=False,crypto_applicable=False)
        except PermissionError:pass
        else:raise AssertionError('Application publication gate bypassed')
    assert c.snapshot()==before
    passed('server_publication_availability_and_explicit_month_confirmation_are_both_required_without_writes')
    generation=app.inputs.publish(c.user,account,draft.id,source_revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    assert generation.account_id==account
    assert app.inputs.published(c.user,account,draft.id,source_revision)
    result=app.inputs.results(c.user,account,draft.id,source_revision)
    assert Decimal(result.positions[0][1])==110 and result.original_documents==4
    financial=c.snapshot()
    snapshot=app.snapshot(c.user,account,include_history=True)
    assert snapshot.history and len(snapshot.history.originals)==1 and not snapshot.history.gaps
    assert load_dashboard_sources(snapshot) and c.snapshot()==financial
    for service in (disabled.inputs,app.inputs):
        assert service.results(c.user,account,draft.id,source_revision)==result
    passed('explicit_application_publication_commits_once_and_dashboard_and_independent_results_use_the_same_verified_generation')
    selections=(PublishedSelection(c.accounts[0],c.drafts[c.accounts[0]].id,c.revisions[c.accounts[0]]),PublishedSelection(account,draft.id,source_revision))
    separate=app.inputs.compare(c.user,2025,1,selections)
    combined=app.inputs.compare(c.user,2025,1,selections,include_positions=True)
    assert not separate.combined_positions and Decimal(combined.combined_positions[0][1])==220
    assert c.snapshot()==financial
    passed('application_comparison_keeps_account_results_separate_and_combined_quantities_require_opt_in')
    values={**DEFAULT_PREFERENCES,'fit_columns':True,'timezone':'America/Denver'}
    assert app.preferences.load(c.user,account).revision==0
    assert app.preferences.save(c.user,account,values,expected_revision=0)==1
    assert dict(disabled.preferences.load(c.user,account).values)==values
    assert {k:v for k,v in c.snapshot()['rows'].items() if k!='account_preferences'}=={k:v for k,v in financial['rows'].items() if k!='account_preferences'}
    passed('application_personal_preferences_persist_on_their_own_role_without_touching_financial_or_directory_rows')
    foreign_id=app.administration.snapshot(c.user).user(c.foreign.identity).id
    command('set_grant',user_id=foreign_id,account_id=account,permission=Permission.VIEW)
    assert app.inputs.results(c.foreign,account,draft.id,source_revision)==result
    assert app.preferences.load(c.foreign,account).revision==0
    app.preferences.save(c.foreign,account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    for call in (lambda:app.inputs.create(c.foreign,account,2025,2),lambda:app.inputs.publish(c.foreign,account,draft.id,source_revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)):
        try:call()
        except PermissionError:pass
        else:raise AssertionError('View grant wrote application financial records')
    command('configure_account',account_id=account,archived=True)
    assert app.inputs.results(c.user,account,draft.id,source_revision)==result
    app.preferences.save(c.foreign,account,values,expected_revision=1)
    try:app.inputs.create(c.user,account,2025,2)
    except PermissionError:pass
    else:raise AssertionError('Archived application account accepted new month')
    command('configure_account',account_id=account,archived=False)
    command('set_grant',user_id=foreign_id,account_id=account,permission=None)
    for call in (lambda:app.snapshot(c.foreign,account),lambda:app.preferences.load(c.foreign,account),lambda:app.inputs.results(c.foreign,account,draft.id,source_revision)):
        try:call()
        except PermissionError:pass
        else:raise AssertionError('Revoked application access retained')
    command('configure_user',user_id=foreign_id,enabled=False)
    try:app.choices(c.foreign)
    except PermissionError:pass
    else:raise AssertionError('Disabled application identity retained')
    command('configure_user',user_id=foreign_id,enabled=True)
    passed('view_sharing_archived_history_revocation_and_disabled_signin_are_rechecked_across_all_application_services')
    directory=app.administration.snapshot(c.user);count=0
    for identity in c.identities:
        user=ServerSession(identity);owner_id=directory.user(identity).id
        for legacy in (a for a in directory.accounts if a.legacy and a.owner_id==owner_id):
            inventory=app.legacy.inventory(user,legacy.id)
            assert inventory.artifact_count>=1
            for period in app.legacy.published_months(user,legacy.id):
                year,month=map(int,period.split('-'))
                assert app.legacy.published_results(user,legacy.id,year,month).month==period
            assert app.snapshot(user,legacy.id).list_keys();count+=1
    assert count>=2
    passed('every_retained_owner_uses_exact_original_workspace_inventory_published_results_and_dashboard_routes')
    after=c.snapshot();excluded={'account_preferences','directories','audit_events'}
    assert after['catalog_sha256']==financial['catalog_sha256']
    assert {k:v for k,v in after['rows'].items() if k not in excluded}=={k:v for k,v in financial['rows'].items() if k not in excluded}
    passed('application_reads_preferences_and_sharing_preserve_every_committed_financial_artifact_draft_and_original')
