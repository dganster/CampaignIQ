"""Immutable review plan bound to the captured workspace; no publishing API."""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import calendar
import json
import os

from campaigniq.historical_backfill_bundle import encode, validate
from campaigniq.historical_backfill_downstream_audit import audit_boundary
from campaigniq.persistence.lot_book_store import serialize_lot_book, deserialize_lot_book
from campaigniq.persistence.position_journal import deserialize_position_journal


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


def _digest(value):
    return sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class BoundaryPlan:
    """Return detached review copies so caller edits cannot change the plan."""
    _json: str

    def to_dict(self):
        return json.loads(self._json)

    @property
    def sha256(self):
        return sha256(self._json.encode()).hexdigest()


def _local(development_only):
    if development_only is not True or os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        raise ValueError('Boundary plans require explicit local development execution.')


def _report_evidence(report):
    return {key: report[key] for key in ('capture_sha256', 'months', 'downstream',
        'protected_artifact_sha256', 'all_2025_drafts_passed', 'identity_preserving_chain_verified')}


def build_boundary_plan(bundle, report, december_lot_book, *, opening_snapshot_text, development_only=False):
    """Recompute boundary candidates from retained artifacts, never from labels alone.

    Existing identities/basis stay in separate historical and published views.
    The plan lists review relationships, not aliases or mutations. Preparation
    proves input consistency; it never constitutes publication authorization.
    """
    _local(development_only); validate(bundle)
    capture_hash = sha256(encode(bundle)).hexdigest()
    if report.get('capture_sha256') != capture_hash or report.get('workspace') != bundle['workspace']:
        raise ValueError('Report and workspace capture differ.')
    if report.get('all_2025_drafts_passed') is not True or report.get('identity_preserving_chain_verified') is not True:
        raise ValueError('All 2025 draft and identity checks are required.')
    if report.get('publication_authorized') is not False or report.get('full_portfolio_continuity_verified') is not False:
        raise ValueError('A diagnostic report cannot already authorize publication or certify full continuity.')
    months = report['months']
    if [row['month'] for row in months] != [f'2025-{m:02}' for m in range(1,13)]:
        raise ValueError('Exactly twelve ordered 2025 drafts are required.')
    drafts = [draft for draft in bundle['drafts'] if draft['year'] == 2025 and draft['state'] == 'draft']
    if len(drafts) != 12:
        raise ValueError('The capture must retain twelve active 2025 drafts.')
    draft_manifest = []
    for row in months:
        matches = [draft for draft in drafts if draft['id'] == row['draft_id']]
        if len(matches) != 1 or matches[0]['revision'] != row['revision'] or f"2025-{matches[0]['month']:02}" != row['month']:
            raise ValueError('Draft revision or month differs from the report.')
        if (row['blocking_findings'] or not row['continuous_preview']['closing_reconciled']
                or not row['independent_preview']['closing_reconciled'] or row.get('opening_state_changes')
                or row.get('closing_quantity_differences') or row.get('opening_quantity_differences')):
            raise ValueError('An unresolved monthly edge cannot enter a boundary plan.')
        docs = [doc for doc in bundle['documents'] if doc['draft_id'] == row['draft_id'] and not doc['superseded']]
        fields = ('id','role','filename','revision','sha256')
        manifest = sorted(({key: doc[key] for key in fields} for doc in docs),key=lambda d:d['id'])
        if manifest != sorted(row['documents'],key=lambda d:d['id']):
            raise ValueError('Saved draft documents differ from verified report inputs.')
        draft_manifest.append(dict(month=row['month'],draft_id=row['draft_id'],revision=row['revision'],documents=manifest))
    artifacts = bundle['artifacts']
    protected = {key:sha256(text.encode()).hexdigest() for key,text in artifacts.items()
                 if len(key)>=7 and key[:4].isdigit() and int(key[:4])>=2026}
    if not protected or protected != report['protected_artifact_sha256']:
        raise ValueError('Protected published artifact set or bytes differ.')
    edges = report['downstream']
    if [edge['month'] for edge in edges] != [f'2026-{m:02}' for m in range(1,10)]:
        raise ValueError('All nine published downstream boundaries are required.')
    from campaigniq.historical_backfill_draft_preview import _FrozenStorage
    from campaigniq.persistence.monthly_publication import is_month_published_in_storage
    storage = _FrozenStorage(bundle)
    prior = december_lot_book
    from campaigniq.historical_backfill_preflight import _state
    if sha256(_state(prior,date(2025,12,31)).encode()).hexdigest() != months[-1]['closing_state_sha256']:
        raise ValueError('December state differs from the verified monthly replay.')
    boundaries = []
    for month, edge in enumerate(edges,1):
        start=date(2026,month,1);end=date(2026,month,calendar.monthrange(2026,month)[1]);key=edge['month']
        if not is_month_published_in_storage(storage,period_end=end):
            raise ValueError('A protected downstream month is not published.')
        entries=deserialize_position_journal(artifacts[key+'-position-journal.json'],period_start=start,period_end=end)
        recomputed=audit_boundary(prior,entries,previous_end=start-date.resolution,
            opening_snapshot_text=opening_snapshot_text if month==1 else None,
            provenance_text=artifacts.get(key+'-import-provenance.json') if month==1 else None)
        if not recomputed['complete_candidate_inventory_match'] or canonical(recomputed) != canonical(edge.get('mapping_and_basis_audit')):
            raise ValueError('Boundary candidates differ from the independently recomputed inventory audit.')
        if month==1:
            snapshot=recomputed['retained_opening_snapshot']
            if not snapshot['source_hash_verified'] or snapshot['source_sha256'] != months[-1]['prepared_source_sha256'].get('schwab_closing_position_snapshot'):
                raise ValueError('January requires the exact retained December closing source.')
        boundaries.append(dict(month=key,existing_records_action='preserve_exact_bytes',
            review_groups=recomputed['candidates'],campaign_relationships=recomputed['candidate_campaign_relations'],
            campaign_relationship_conflicts=recomputed['campaign_relation_conflicts'],
            retained_opening_snapshot=recomputed['retained_opening_snapshot'],
            audit_sha256=_digest(recomputed),mapping_approved=False,basis_replacement_authorized=False))
        persisted=deserialize_lot_book(artifacts[key+'-lot-book.json'])
        if persisted.period_end != end:
            raise ValueError('Protected closing period differs.')
        prior=persisted.lot_book
    result=dict(format='campaigniq.historical_boundary_review_plan',version=1,status='prepared_for_review',
        workspace=bundle['workspace'],capture_sha256=capture_hash,report_evidence_sha256=_digest(_report_evidence(report)),
        december_state_sha256=sha256(serialize_lot_book(period_end=date(2025,12,31),lot_book=december_lot_book).encode()).hexdigest(),
        drafts=draft_manifest,boundaries=boundaries,protected_artifact_sha256=protected,
        existing_artifact_sha256={key:sha256(value.encode()).hexdigest() for key,value in artifacts.items()},
        retained_document_versions_sha256=_digest(bundle['documents']),
        constraints=dict(existing_campaign_ids='preserve',existing_basis='preserve',global_campaign_aliases_allowed=False,
            replace_unknown_replay_basis=False,publication_protocol_change_authorized=False,
            production_writes_authorized=False,publication_authorized=False),
        remaining_requirements=['Review boundary-scoped relationships; do not merge existing campaign identities.',
            'Retain broker snapshot basis separately from unknown historical replay basis.',
            'Test database backup restoration and rollback.',
            'Implement and verify a transactional historical publisher, including publication protocol and protected-reader behavior.'],
        basis_continuity_verified=False,full_portfolio_continuity_verified=False,publication_authorized=False)
    if sha256(encode(bundle)).hexdigest()!=capture_hash:
        raise RuntimeError('Plan preparation changed the capture.')
    return BoundaryPlan(canonical(result))


def verify_boundary_plan(plan,bundle,report,december_lot_book,*,opening_snapshot_text,development_only=False):
    """Reject stale or altered plans by rebuilding against the exact frozen inputs."""
    _local(development_only)
    if not isinstance(plan,BoundaryPlan):
        raise ValueError('An immutable boundary review plan is required.')
    rebuilt=build_boundary_plan(bundle,report,december_lot_book,
        opening_snapshot_text=opening_snapshot_text,development_only=True)
    if plan.sha256!=rebuilt.sha256:
        raise ValueError('Boundary plan differs from verified source-bound review evidence.')
    return True
