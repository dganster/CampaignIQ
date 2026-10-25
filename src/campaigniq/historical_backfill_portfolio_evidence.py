"""Rebuild the reviewed market and basis evidence from retained source bytes."""
import json

from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.historical_backfill_market_continuity import audit_markets
from campaigniq.historical_backfill_basis_provenance import build_provenance, retained_texts
from campaigniq.historical_backfill_basis_consumption import verify_consumption

MARKET_KEY = 'historical-market-continuity-v1.json'
BASIS_KEY = 'historical-basis-provenance-v1.json'
CONSUMPTION_KEY = 'historical-basis-consumption-v1.json'
CHECK_KEYS = {MARKET_KEY, BASIS_KEY, CONSUMPTION_KEY}


def validate_check_payloads(checks):
    if set(checks) != CHECK_KEYS:
        raise ValueError('Combined continuity evidence scope differs.')
    formats = {MARKET_KEY:'campaigniq.market_continuity_review', BASIS_KEY:'campaigniq.aggregate_basis_provenance',
        CONSUMPTION_KEY:'campaigniq.downstream_basis_consumption'}
    for key, text in checks.items():
        payload = json.loads(text)
        if payload.get('format') != formats[key] or payload.get('version') != 1 or payload.get('publication_authorized') is not False:
            raise ValueError('Invalid or authorized continuity evidence payload.')


def rebuild_checks(bundle, package, artifacts, evidence, preview_sha256, corrections):
    market = audit_markets(artifacts)
    market.update(capture_sha256=package['capture_sha256'], publication_package_sha256=digest(canonical(package)))
    basis = build_provenance(package['boundary_plan'], artifacts, evidence, retained_texts(bundle))
    consumption = verify_consumption(basis, package['boundary_plan'], artifacts)
    consumption.update(correction_preview_sha256=preview_sha256,
        proposed_correction_sha256={k:digest(v.encode()) for k,v in corrections.items()},
        production_connection_made=False, correction_scope='Previously reviewed proposals, applied in memory only.')
    checks = {key:canonical(value).decode()+'\n' for key,value in
        ((MARKET_KEY,market),(BASIS_KEY,basis),(CONSUMPTION_KEY,consumption))}
    validate_check_payloads(checks)
    return checks


def require_reviewed_checks(rebuilt, supplied):
    validate_check_payloads(rebuilt)
    if set(supplied) != CHECK_KEYS:
        raise ValueError('All three previously reviewed reports are required.')
    for key in CHECK_KEYS:
        if json.loads(rebuilt[key]) != json.loads(supplied[key]):
            raise ValueError('Reviewed continuity evidence differs from independently rebuilt sources: '+key)


class ArtifactView:
    def __init__(self, artifacts): self.artifacts=artifacts
    def exists(self, key): return key in self.artifacts
    def read_text(self, key): return self.artifacts[key]
    def list_keys(self, *, prefix='', suffix=''):
        return tuple(sorted(k for k in self.artifacts if k.startswith(prefix) and k.endswith(suffix)))


def verify_committed_checks(bundle, package, artifacts, archive, checks, corrections, preview_sha256):
    """Read the staged proof artifacts and dashboard through its actual loader."""
    if archive.get('basis_evidence_manifest') is None:
        raise ValueError('Committed source archive lacks the basis evidence manifest.')
    rebuilt = rebuild_checks(bundle, package, artifacts, archive['basis_evidence_manifest'], preview_sha256, corrections)
    actual = {key:artifacts[key] for key in CHECK_KEYS}
    require_reviewed_checks(rebuilt, actual)
    require_reviewed_checks(rebuilt, checks)
    from campaigniq.analytics.linked_campaign_history import load_linked_history
    from campaigniq.historical_backfill_continuity_links import verify_partition
    reader = load_linked_history(ArtifactView(artifacts))
    if reader is None:
        raise ValueError('Committed dashboard continuity reader is unavailable.')
    partition = verify_partition(reader.graph)
    for history in partition['histories']:
        month,cid=history['campaign_nodes'][0]
        selected=reader.selection(month+'/'+cid)
        if selected is None or selected['record_count']!=history['record_count'] or selected['totals']!=history['totals']:
            raise ValueError('Committed dashboard history totals differ.')
    return dict(committed_market_ledger_continuity_verified=True,
        committed_aggregate_basis_provenance_verified=True, committed_downstream_basis_consumption_verified=True,
        committed_dashboard_source_loader_verified=True, year_end_basis_group_count=json.loads(actual[BASIS_KEY])['position_count'],
        consumed_basis_allocation_count=json.loads(actual[CONSUMPTION_KEY])['consumed_broker_allocation_count'],
        independent_crypto_month_end_evidence_complete=json.loads(actual[MARKET_KEY])['independent_crypto_month_end_evidence_complete'])
