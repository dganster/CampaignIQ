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
    return result
