import copy
import json
from types import SimpleNamespace
import pytest

from tests.test_historical_publication_rehearsal import complete_generation
from campaigniq.historical_backfill_bundle import encode
from campaigniq.historical_backfill_publication_package import build_package, save_package, digest


@pytest.fixture
def inputs(complete_generation):
    bundle = dict(format='campaigniq.backfill_workspace_capture', version=1,
        workspace='workspace-dennis', database='campaigniq_production_db',
        captured_at='2026-10-24T15:05:29+00:00', drafts=[], documents=[],
        artifacts={'2026-01-retained.json': '{"unchanged":true}'})
    protected = {k: digest(v.encode()) for k,v in bundle['artifacts'].items()}
    capture = digest(encode(bundle))
    report = dict(capture_sha256=capture, all_2025_drafts_passed=True,
        identity_preserving_chain_verified=True,
        boundary_plan=dict(status='prepared_for_review', capture_sha256=capture,
            protected_artifact_sha256=protected))
    collector = SimpleNamespace(artifacts=complete_generation, contract_mapping_candidates={
        '2025-02': [dict(source='SPXW exact contract', target='SPX exact contract', approved=False)]})
    rehearsal = dict(capture_sha256=capture, local_rehearsal_passed=True,
        local_workspace_restore_verified=True, transaction_rollback_verified=True,
        committed_stage_verified=True, protected_record_rejection_verified=True,
        backup_restore_rollback_verified=True, production_connection_made=False,
        publication_authorized=False, scope='isolated_local_workspace_copy',
        development_commit='a'*40, backup_sha256='b'*64,
        candidate_artifact_sha256={k:digest(v.encode()) for k,v in complete_generation.items()},
        candidate_artifact_count=len(complete_generation), protected_2026_artifact_count=1)
    return bundle, report, collector, rehearsal


def test_export_is_exact_exclusive_and_still_unauthorized(inputs, tmp_path):
    before = copy.deepcopy(inputs)
    package = build_package(*inputs)
    output = tmp_path/'package'
    save_package(output, package, inputs[2].artifacts)
    for key,text in inputs[2].artifacts.items():
        assert (output/'candidates'/key).read_bytes() == text.encode()
    marker = json.loads((output/'package-complete.json').read_text())
    assert marker['package_sha256'] == digest((output/'publication-package.json').read_bytes())
    assert package['publication_authorized'] is False and package['production_ready'] is False
    assert package['full_portfolio_continuity_verified'] is False
    assert len(package['unresolved_requirements']) == 4
    assert inputs == before
    with pytest.raises(FileExistsError): save_package(output, package, inputs[2].artifacts)


@pytest.mark.parametrize('field', ['local_rehearsal_passed','local_workspace_restore_verified',
    'transaction_rollback_verified','committed_stage_verified','protected_record_rejection_verified',
    'backup_restore_rollback_verified'])
def test_failed_or_incomplete_rehearsal_cannot_be_packaged(inputs, field):
    inputs[3][field] = False
    with pytest.raises(ValueError): build_package(*inputs)


@pytest.mark.parametrize('change', ['capture','candidate','count','protected','draft_checks','boundary',
    'approved_mapping','production','source','backup'])
def test_stale_tampered_or_approved_inputs_are_rejected(inputs, change):
    bundle,report,collector,rehearsal = inputs
    if change=='capture': bundle['artifacts']['2026-01-retained.json']='changed'
    if change=='candidate': rehearsal['candidate_artifact_sha256']['2025-01-finalized.json']='f'*64
    if change=='count': rehearsal['candidate_artifact_count']=0
    if change=='protected': rehearsal['protected_2026_artifact_count']=0
    if change=='draft_checks': report['all_2025_drafts_passed']=False
    if change=='boundary': report['boundary_plan']['protected_artifact_sha256']={}
    if change=='approved_mapping': collector.contract_mapping_candidates['2025-02'][0]['approved']=True
    if change=='production': rehearsal['production_connection_made']=True
    if change=='source': rehearsal['development_commit']='unknown'
    if change=='backup': rehearsal['backup_sha256']='unknown'
    with pytest.raises(ValueError): build_package(*inputs)


def test_payload_tampering_after_validation_has_no_output(inputs, tmp_path):
    package=build_package(*inputs)
    inputs[2].artifacts['2025-01-finalized.json']='changed'
    with pytest.raises(ValueError): save_package(tmp_path/'blocked',package,inputs[2].artifacts)
    assert not (tmp_path/'blocked').exists()
