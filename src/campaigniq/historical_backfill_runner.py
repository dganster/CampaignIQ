#!/usr/bin/env python3
"""Local runner: only the explicitly selected capture uses read-only remote SQL."""
import argparse
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def save_capture(bundle, output):
    from campaigniq.historical_backfill_bundle import encode, validate, decode
    raw = encode(validate(bundle))
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    capture = output / 'workspace-capture.json.gz'
    with capture.open('xb') as stream:
        os.chmod(capture, 0o600)
        stream.write(gzip.compress(raw, mtime=0))
    restored = validate(decode(gzip.decompress(capture.read_bytes())))
    if encode(restored) != raw:
        raise ValueError('Saved capture round-trip verification failed.')
    manifest = dict(capture_sha256=sha256(raw).hexdigest(),
                    compressed_file_sha256=sha256(capture.read_bytes()).hexdigest(),
                    workspace_snapshot_roundtrip_verified=True, physical_database_restore_verified=False)
    _write_json(output / 'capture-verification.json', manifest)
    return restored, manifest


def _write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True, default=str)
        stream.write('\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--development-only', required=True, action='store_true')
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--render-read-only', action='store_true')
    source.add_argument('--capture', type=Path, help='Previously verified workspace-capture.json.gz; requires its adjacent capture-verification.json')
    p.add_argument('--output', type=Path)
    p.add_argument('--campaign-continuity', action='store_true',
                   help='Attempt identity-preserving local replay; retain strict blockers and label any quantity-only fallback.')
    p.add_argument('--forex-monthly', action='append', default=[], metavar='YYYY-MM=PATH',
                   help='Read an additional monthly PDF/text statement without changing the saved draft; repeat for another month.')
    months = [f'2025-{m:02}' for m in range(1, 13)]
    p.add_argument('--forex-not-applicable', nargs='*', choices=months, default=[])
    p.add_argument('--crypto-not-applicable', nargs='*', choices=months, default=[])
    args = p.parse_args()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        p.error('Run locally in the development checkout, never on Render.')
    repo = args.repo.expanduser().resolve()
    branch = subprocess.check_output(['git', '-C', str(repo), 'branch', '--show-current'], text=True).strip()
    if branch != 'feature/historical-backfill-2025':
        p.error('Use the feature/historical-backfill-2025 development checkout.')
    sys.path.insert(0, str(repo / 'src'))
    from campaigniq.historical_backfill_bundle import remote_script, parse_remote_output, SSH_TARGET, validate, encode, decode, LIMIT
    from campaigniq.historical_backfill_preflight import run_preflight
    try:
        if args.render_read_only:
            print('Capturing workspace drafts and artifacts through a read-only database transaction...', flush=True)
            nonce = uuid4().hex
            response = subprocess.run(['ssh', '-T', '-oBatchMode=yes', '-oStrictHostKeyChecking=yes', '-oConnectTimeout=15',
                                       SSH_TARGET, 'python -'], input=remote_script(nonce), text=True, capture_output=True, timeout=180)
            if response.returncode:
                raise ValueError('Read-only SSH capture failed. Existing SSH authentication and trusted host key are required; no settings were changed.')
            bundle = parse_remote_output(response.stdout, nonce)
        else:
            path = args.capture.expanduser().resolve()
            verification = json.loads(path.with_name('capture-verification.json').read_text())
            packed = path.read_bytes()
            if sha256(packed).hexdigest() != verification['compressed_file_sha256']:
                raise ValueError('Offline capture file checksum mismatch.')
            with gzip.open(path, 'rb') as stream:
                raw = stream.read(LIMIT + 1)
            if len(raw) > LIMIT or sha256(raw).hexdigest() != verification['capture_sha256']:
                raise ValueError('Offline capture content checksum mismatch.')
            bundle = validate(decode(raw))
        output = args.output.expanduser().resolve() if args.output else Path.home() / 'Downloads' / ('CampaignIQ-backfill-preflight-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
        if output.is_relative_to(repo):
            raise ValueError('Choose a new output folder outside the repository.')
        bundle, verification = save_capture(bundle, output)
        from campaigniq.import_draft_bridge import RestoredUpload
        supplements = {}
        for specification in args.forex_monthly:
            month, separator, filename = specification.partition('=')
            if not separator or month not in months or month in supplements:
                raise ValueError('Use a unique YYYY-MM=PATH supplemental statement for a 2025 month.')
            path = Path(filename).expanduser().resolve()
            if path.suffix.lower() not in ('.pdf', '.txt') or not 0 < path.stat().st_size <= 20*1024*1024:
                raise ValueError('Supplemental statement must be a PDF/text file, at most 20 MB.')
            content = path.read_bytes()
            supplements[month] = RestoredUpload(path.name, 'application/pdf' if path.suffix.lower() == '.pdf' else 'text/plain', content)
            folder = output / 'supplemental-evidence'
            folder.mkdir(mode=0o700, exist_ok=True)
            copy = folder / (month + path.suffix.lower())
            with copy.open('xb') as stream:
                os.chmod(copy, 0o600)
                stream.write(content)
            if copy.read_bytes() != content:
                raise ValueError('Supplemental evidence backup verification failed.')
        print('Workspace capture checksum and round-trip: PASS', flush=True)
        def progress(row):
            preview = row['independent_preview']
            state = 'inventory reconciles' if preview and preview['closing_reconciled'] else 'needs review'
            print(row['month'] + ': ' + state + '; ' + str(len(row['blocking_findings'])) + ' blocking findings', flush=True)
            if preview and preview.get('forex_controls', {}).get('settlement') is False:
                controls = preview['forex_report_controls']
                print('  Forex MTD USD: ' + controls['mtd_settled_pl_usd'] + '; settlement rows USD: ' + controls['settlement_row_total_usd'] + '; difference USD: ' + preview['forex_control_deltas_usd']['settlement'], flush=True)
            if row.get('opening_state_changes'):
                print('  Carried opening changes requiring review: ' + str(len(row['opening_state_changes'])), flush=True)
            continuity = row.get('campaign_continuity', {})
            if continuity.get('enabled') and continuity.get('mode') != 'initial_evidenced_anchor':
                print('  Campaign continuity: ' + continuity['mode'], flush=True)
                if 'trade_links' in continuity:
                    links = continuity['trade_links']
                    print('  Trade links supported by replay: ' + str(sum(bool(link['supported_by_fifo']) for link in links)) + '/' + str(len(links)), flush=True)
                for finding in continuity.get('blocking_findings', []):
                    print('  ' + finding, flush=True)
        report = run_preflight(bundle, forex_not_applicable=args.forex_not_applicable,
                               crypto_not_applicable=args.crypto_not_applicable, forex_monthly_evidence=supplements,
                               campaign_continuity=args.campaign_continuity, progress=progress)
        report['capture_verification'] = verification
        report['development_commit'] = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        _write_json(output / 'preflight-report.json', report)
        if report.get('boundary_plan', {}).get('status') == 'prepared_for_review':
            _write_json(output / 'boundary-plan.json', report['boundary_plan'])
            print('Boundary review plan:', output / 'boundary-plan.json')
            print('Boundary review plan file SHA256:', sha256((output / 'boundary-plan.json').read_bytes()).hexdigest())
            print('Boundary review plan evidence SHA256:', report['boundary_plan_sha256'])
        else:
            print('Boundary review plan not prepared:', report.get('boundary_plan', {}).get('reason', 'Incomplete evidence.'))
        print('Diagnostic position replay completed:', report['diagnostic_position_replay_completed'])
        print('Identity-preserving chain verified:', report['identity_preserving_chain_verified'])
        print('2025 draft checks passed:', report['all_2025_drafts_passed'])
        for edge in report['downstream']:
            audit = edge.get('mapping_and_basis_audit', {})
            print(edge['month'] + ': downstream candidate inventory match: ' + str(audit.get('complete_candidate_inventory_match', False))
                + '; basis continuity verified: ' + str(edge['basis_continuity_verified']))
            if audit.get('retained_opening_snapshot', {}).get('source_hash_verified'):
                snapshot = audit['retained_opening_snapshot']
                print('  Retained opening snapshot matches published input hash; known basis lots: '
                    + str(snapshot['known_basis_lots']) + '; unknown basis lots: ' + str(snapshot['unknown_basis_lots']))
        print('Full portfolio continuity verified:', report['full_portfolio_continuity_verified'])
        print('Publication authorized:', report['publication_authorized'])
        print('Report:', output / 'preflight-report.json')
        print('No data imported or published. ' + ('Production was only read.' if args.render_read_only else 'Analysis used the saved capture; no production connection was made.'))
    except Exception as error:
        # Never echo SSH output or database credentials in errors.
        p.exit(1, 'Preflight failed (' + type(error).__name__ + '). No publication was attempted.\n' +
               (str(error) + '\n' if isinstance(error, ValueError) else 'Check local configuration and installed development files.\n'))


if __name__ == '__main__':
    main()
