"""Prepare source-bound publication inputs offline. No database writer or authorization."""
import argparse
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from campaigniq.historical_backfill_bundle import decode, encode, validate, LIMIT
from campaigniq.historical_backfill_candidate_payloads import CandidateCollector, validate_generation
from campaigniq.historical_backfill_preflight import run_preflight


def digest(data):
    return sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def build_package(bundle, report, collector, rehearsal):
    """Bind regenerated payloads to a completed rehearsal; preserve unresolved gates."""
    validate(bundle)
    validate_generation(collector.artifacts)
    capture_hash = digest(encode(bundle))
    if report.get('capture_sha256') != capture_hash or rehearsal.get('capture_sha256') != capture_hash:
        raise ValueError('Capture differs from the preflight or successful rehearsal.')
    for flag in ('local_rehearsal_passed', 'local_workspace_restore_verified',
                 'transaction_rollback_verified', 'committed_stage_verified',
                 'protected_record_rejection_verified', 'backup_restore_rollback_verified'):
        if rehearsal.get(flag) is not True:
            raise ValueError('Successful rehearsal is required: ' + flag)
    if rehearsal.get('production_connection_made') is not False or rehearsal.get('publication_authorized') is not False:
        raise ValueError('Expected an isolated, unauthorized rehearsal.')
    if rehearsal.get('scope') != 'isolated_local_workspace_copy':
        raise ValueError('Unexpected rehearsal scope.')
    if not re.fullmatch('[0-9a-f]{40}', rehearsal.get('development_commit', '')):
        raise ValueError('Rehearsal source commit is missing.')
    if not re.fullmatch('[0-9a-f]{64}', rehearsal.get('backup_sha256', '')):
        raise ValueError('Rehearsal backup checksum is missing.')
    hashes = {key: digest(text.encode()) for key, text in collector.artifacts.items()}
    if hashes != rehearsal.get('candidate_artifact_sha256') or len(hashes) != rehearsal.get('candidate_artifact_count'):
        raise ValueError('Regenerated candidates differ from the successful rehearsal.')
    if set(hashes) & set(bundle['artifacts']):
        raise ValueError('Publication candidates would replace existing artifacts.')
    protected = {key: digest(text.encode()) for key, text in bundle['artifacts'].items() if key.startswith('2026-')}
    if not protected or len(protected) != rehearsal.get('protected_2026_artifact_count'):
        raise ValueError('Protected artifact count differs from the rehearsal.')
    if report.get('all_2025_drafts_passed') is not True or report.get('identity_preserving_chain_verified') is not True:
        raise ValueError('Draft or campaign continuity checks failed.')
    boundary = report.get('boundary_plan', {})
    if boundary.get('status') != 'prepared_for_review' or boundary.get('capture_sha256') != capture_hash:
        raise ValueError('A source-bound boundary review plan is required.')
    if boundary.get('protected_artifact_sha256') != protected:
        raise ValueError('Boundary plan protected records differ.')
    mappings = collector.contract_mapping_candidates
    if any(item.get('approved') is not False for rows in mappings.values() for item in rows):
        raise ValueError('Preparation cannot approve contract mappings.')
    return dict(format='campaigniq.historical_publication_package', version=1,
        workspace=bundle['workspace'], capture_sha256=capture_hash,
        rehearsal_report_sha256=digest(canonical(rehearsal)),
        rehearsed_source_commit=rehearsal['development_commit'],
        candidate_artifact_sha256=hashes, candidate_artifact_count=len(hashes),
        existing_artifact_sha256={key: digest(value.encode()) for key, value in bundle['artifacts'].items()},
        protected_2026_artifact_sha256=protected,
        retained_documents_sha256=digest(canonical(bundle['documents'])),
        retained_drafts_sha256=digest(canonical(bundle['drafts'])),
        contract_mapping_candidates=mappings, boundary_plan=boundary,
        operations=[dict(action='insert_if_absent', key=key, sha256=hashes[key]) for key in sorted(hashes)],
        unresolved_requirements=[
            'Review exact SPXW/SPX contract mapping candidates.',
            'Resolve boundary relationship and basis handling without changing published 2026 records.',
            'Verify a full production backup by restoring it in isolation.',
            'Implement and test the production transaction, stale-input checks, publication protocol change and rollback.',
        ],
        production_ready=False, full_portfolio_continuity_verified=False,
        full_production_database_restore_verified=False, publication_authorized=False)


def save_package(output, package, artifacts):
    """Exclusive private output directory; partial output never gets a completion marker."""
    if package['candidate_artifact_sha256'] != {k: digest(v.encode()) for k, v in artifacts.items()}:
        raise ValueError('Payload changed after package verification.')
    output.mkdir(mode=0o700, exist_ok=False)
    payload = output / 'candidates'
    payload.mkdir(mode=0o700)
    for key, text in artifacts.items():
        if not re.fullmatch(r'2025-(0[1-9]|1[0-2])-[a-z-]+\.json', key):
            raise ValueError('Invalid candidate filename.')
        with (payload / key).open('x', encoding='utf-8') as stream:
            os.chmod(stream.name, 0o600)
            stream.write(text)
        if digest((payload / key).read_bytes()) != package['candidate_artifact_sha256'][key]:
            raise ValueError('Exported payload checksum differs.')
    data = canonical(package)
    with (output / 'publication-package.json').open('xb') as stream:
        os.chmod(stream.name, 0o600)
        stream.write(data)
    with (output / 'package-complete.json').open('x') as stream:
        os.chmod(stream.name, 0o600)
        json.dump(dict(package_sha256=digest(data), publication_authorized=False), stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--rehearsal-report', type=Path, required=True)
    parser.add_argument('--development-only', action='store_true', required=True)
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        parser.error('Use the local development checkout.')
    if Path(__file__).resolve().parents[2] != repo:
        parser.error('Installed module and selected repository differ.')
    def git(*parts):
        return subprocess.check_output(['git', '-C', str(repo), *parts], text=True).strip()
    if git('branch', '--show-current') != 'feature/historical-backfill-2025' or git('status', '--porcelain'):
        parser.error('Use the clean, committed development branch.')
    path = args.capture.expanduser().resolve()
    verification = json.loads(path.with_name('capture-verification.json').read_text())
    if digest(path.read_bytes()) != verification['compressed_file_sha256']:
        parser.error('Compressed capture checksum differs.')
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT or digest(raw) != verification['capture_sha256']:
        parser.error('Capture content checksum differs.')
    bundle = decode(raw)
    rehearsal = json.loads(args.rehearsal_report.expanduser().read_text())
    if subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', rehearsal['development_commit'], 'HEAD']).returncode:
        parser.error('Rehearsed commit is not an ancestor of this checkout.')
    collector = CandidateCollector(bundle['artifacts'])
    report = run_preflight(bundle, campaign_continuity=True, candidate_collector=collector,
        progress=lambda row: print(row['month'] + ': ' + str(len(row['blocking_findings'])) + ' candidate/preflight findings', flush=True))
    package = build_package(bundle, report, collector, rehearsal)
    output = Path.home() / 'Downloads' / ('CampaignIQ-publication-package-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    save_package(output, package, collector.artifacts)
    print('Rehearsed candidate checksum match: PASS')
    print('Candidate artifacts:', package['candidate_artifact_count'])
    print('Protected 2026 artifacts:', len(package['protected_2026_artifact_sha256']))
    print('Contract mappings awaiting review:', sum(map(len, collector.contract_mapping_candidates.values())))
    print('Package:', output / 'publication-package.json')
    print('Offline preparation only. Production readiness and publication remain false.')


if __name__ == '__main__':
    main()
