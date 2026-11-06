"""Deployment new-account results, with current identity and one read-only snapshot."""
from pathlib import Path
from tempfile import TemporaryDirectory
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_signedin_results import _results_session
from campaigniq.import_draft_bridge import restore_uploads
from campaigniq.monthly_upload_staging import stage_monthly_uploads


def load_deployment_results(service,user,account_id,draft_id,revision):
    if type(service) is not DeploymentMonthlyInputs or type(revision) is not int:
        raise ValueError('Deployment monthly input service and displayed source revision required')
    with service.gateway.transaction(user,account_id) as session:
        draft=service._lock(session,draft_id)
        if draft.revision!=revision:raise ValueError('Draft changed; reload before viewing results')
        result=_deployment_results_session(service,session,draft,account_id,revision)
        session._check();return result


def _deployment_results_session(service,session,draft,account_id,revision):
    # Caller owns one authorized READ ONLY snapshot for every selected source.
    uploads=restore_uploads(session.drafts,draft.id)
    with TemporaryDirectory(prefix='campaigniq-deployment-results-') as root:
        supplied=stage_monthly_uploads(upload_dir=Path(root),uploads=uploads)
        if any(not item.valid for item in service._identity_validations(session,uploads,supplied,draft=draft)):
            raise ValueError('Current broker identity differs from retained publication sources')
    result=_results_session(service,session,draft,account_id,revision,receipt_format='campaigniq.deployment_account_publication')
    from campaigniq.account_futures_generation import verify_published
    record=verify_published(service,session,draft)
    if record is not None:
        from dataclasses import replace
        result=replace(result,futures_applicable=True,futures_realized_usd=str(record.net_realized_usd),
            futures_evidence_status='Owner-confirmed empty opening/ending positions; statement association owner-confirmed')
    from campaigniq.account_crypto_publication import verify_published as verify_crypto
    verify_crypto(service,session,draft)
    from campaigniq.account_forex_publication import verify_published as verify_forex
    forex=verify_forex(service,session,draft)
    if forex is not None:
        from dataclasses import replace
        totals=forex['financials']
        result=replace(result,forex_policy_approved=True,forex_financing_usd=totals['financing_usd'],
            forex_opening_cash_usd=totals['opening_cash_usd'],forex_ending_cash_usd=totals['ending_cash_usd'],
            forex_net_cash_change_usd=totals['net_cash_change_usd'])
    from campaigniq.account_no_trading_publication import verify as verify_no_trading
    carry=verify_no_trading(service,session,draft)
    if carry is not None:
        from dataclasses import replace
        result=replace(result,no_trading_month=True,cash_income_usd=carry['review']['financials']['income_usd'])
    from campaigniq.account_income_evidence import current_statement
    from dataclasses import replace
    result=replace(result,income_evidence=current_statement(service,session,draft))
    return result
