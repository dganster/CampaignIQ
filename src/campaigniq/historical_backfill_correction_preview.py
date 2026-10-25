"""Read-only, source-bound preview of the two identified 2026 corrections."""
import argparse
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import gzip
import json
import os
from pathlib import Path
import subprocess
import uuid

from campaigniq.historical_backfill_bundle import decode, validate, LIMIT
from campaigniq.historical_backfill_publication_package import digest
from campaigniq.historical_backfill_restored_publisher import connect_local, require_workspace, table_manifest, save
from campaigniq.historical_backfill_downstream_audit import instrument_key
from campaigniq.persistence.lot_book_store import _deserialize_instrument, deserialize_lot_book
from campaigniq.persistence.realized_attribution_store import deserialize_realized_attributions, _serialize_attribution
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section


def key(raw):
    return instrument_key(_deserialize_instrument(raw))


def journal(artifacts,month):
    obj=decode(artifacts[month+'-position-journal.json'].encode())
    if obj.get('format')!='campaigniq.position_journal' or obj.get('version')!=1:
        raise ValueError('Invalid position journal.')
    return obj['entries']


def compatible_date(row,record):
    executed=datetime.fromisoformat(row['occurred_at']).date()
    expected=RealizedLotAttributor._next_business_day(executed) if isinstance(record.instrument,OptionContract) else executed
    return record.closed_date==expected


def missing_record_proposal(artifacts,records):
    """Require exactly one additional, fully corroborated April stock sale."""
    name='2026-04-realized-attributions.json'
    old=deserialize_realized_attributions(artifacts[name])
    if len(set(records))!=len(records):raise ValueError('Duplicate broker report records.')
    previous=[a.record for a in old.attributions]
    if any(previous.count(record)!=1 or records.count(record)!=1 for record in previous):
        raise ValueError('An existing April broker record is missing, changed or ambiguous.')
    added=[record for record in records if record not in previous]
    if len(added)!=1 or isinstance(added[0].instrument,OptionContract):
        raise ValueError('Expected exactly one additional stock sale; broader corrections require separate review.')
    record=added[0]
    if record.expected_gain_loss!=record.gain_loss:raise ValueError('New broker record economics do not reconcile.')
    rows=[row for row in journal(artifacts,'2026-04') if row['action']=='Close / reduce'
        and key(row['instrument'])==instrument_key(record.instrument)
        and -Decimal(row['quantity_change'])==record.quantity and compatible_date(row,record)
        and row['price'] is not None and Decimal(row['price'])==record.closing_price]
    if len(rows)!=1:raise ValueError('The new record does not have one exact journal closure.')
    closure=rows[0]
    book=deserialize_lot_book(artifacts['2026-03-lot-book.json']).lot_book
    available={lot.lot_id:lot for lot in book.lots(record.instrument)}
    allocations=[]
    for ref in closure['lots']:
        lot=available.get(ref['lot_id']);quantity=Decimal(ref['quantity'])
        if lot is None or lot.quantity!=quantity or lot.basis_total is None:
            raise ValueError('New sale lacks exact retained opening lot quantities and basis.')
        allocations.append(LotAllocation(lot.lot_id,quantity,lot.basis_total,
            'SCHWAB_REALIZED_GAIN_LOSS',closure['campaign_id']))
    proposed=RealizedAttribution(record,tuple(allocations))
    if proposed.allocated_quantity!=record.quantity or not proposed.basis_reconciled:
        raise ValueError('New sale allocations do not reconcile to broker quantity and basis.')
    # Preserve every original record and allocation as its original JSON object.
    payload=decode(artifacts[name].encode());by_record=dict(zip(previous,payload['attributions']))
    payload['attributions']=[_serialize_attribution(proposed) if row==record else by_record[row] for row in records]
    result=json.dumps(payload,sort_keys=True,indent=2)+'\n'
    deserialize_realized_attributions(result)
    return name,result,dict(kind='add_missing_broker_record',original_record_count=len(previous),
        proposed_record_count=len(records),added_record=_serialize_attribution(proposed),
        existing_records_and_allocations_unchanged=True,
        source_artifacts=['2026-04-position-journal.json','2026-03-lot-book.json',name])


def reference_proposal(artifacts):
    """Find the cross-instrument February reference; refuse ambiguous replacements."""
    name='2026-02-realized-attributions.json';old=deserialize_realized_attributions(artifacts[name])
    rows=journal(artifacts,'2026-02');references={}
    for row in rows:
        for lot in row['lots']:references.setdefault(lot['lot_id'],set()).add(key(row['instrument']))
    conflicts=[]
    for index,attribution in enumerate(old.attributions):
        for number,allocation in enumerate(attribution.allocations):
            known=references.get(allocation.lot_id)
            if known and instrument_key(attribution.record.instrument) not in known:
                conflicts.append((index,number,attribution,allocation))
    if len(conflicts)!=1:raise ValueError('Expected one identified February cross-instrument reference.')
    index,number,attribution,allocation=conflicts[0];record=attribution.record
    if len(attribution.allocations)!=1 or allocation.quantity!=record.quantity:
        raise ValueError('Split realized allocations require additional source evidence.')
    candidates=[row for row in rows if row['action'] in ('Close / reduce','Roll: close')
        and key(row['instrument'])==instrument_key(record.instrument)
        and abs(Decimal(row['quantity_change']))==allocation.quantity
        and row['campaign_id']==allocation.campaign_id and compatible_date(row,record)
        and len(row['lots'])==1 and Decimal(row['lots'][0]['quantity'])==allocation.quantity]
    if len(candidates)!=1:raise ValueError('Replacement reference is not uniquely established by the journal.')
    closure=candidates[0];target=closure['lots'][0]['lot_id']
    opens=[row for row in rows if row['action'] in ('Open / add','Roll: open')
        and key(row['instrument'])==instrument_key(record.instrument)
        and row['campaign_id']==allocation.campaign_id
        and row['occurred_at']<closure['occurred_at']
        and any(lot['lot_id']==target and Decimal(lot['quantity'])==allocation.quantity for lot in row['lots'])]
    if len(opens)!=1 or references[target]!={instrument_key(record.instrument)}:
        raise ValueError('Replacement lacks an unambiguous opening and closing lot chain.')
    payload=decode(artifacts[name].encode())
    payload['attributions'][index]['allocations'][number]['lot_id']=target
    result=json.dumps(payload,sort_keys=True,indent=2)+'\n'
    parsed=deserialize_realized_attributions(result)
    expected=replace(attribution,allocations=(replace(allocation,lot_id=target),))
    check=list(old.attributions);check[index]=expected
    if tuple(check)!=parsed.attributions:raise ValueError('Reference correction changed other fields.')
    return name,result,dict(kind='correct_cross_instrument_reference',attribution_index=index,allocation_index=number,
        old_lot_id=allocation.lot_id,new_lot_id=target,broker_record_and_monetary_amounts_unchanged=True,
        campaign_id_unchanged=True,source_artifacts=[name,'2026-02-position-journal.json'])


def build_preview(artifacts,records,source_hash):
    if len(source_hash)!=64 or any(c not in '0123456789abcdef' for c in source_hash):raise ValueError('Invalid PDF hash.')
    proposals={};changes=[]
    for name,text,change in (missing_record_proposal(artifacts,records),reference_proposal(artifacts)):
        proposals[name]=text
        change.update(artifact=name,original_sha256=digest(artifacts[name].encode()),proposed_sha256=digest(text.encode()),
            supporting_artifact_sha256={k:digest(artifacts[k].encode()) for k in change['source_artifacts']})
        changes.append(change)
    return dict(format='campaigniq.published_correction_preview',version=1,workspace='workspace-dennis',
        april_source_pdf_sha256=source_hash,changes=changes,
        existing_artifact_sha256={k:digest(v.encode()) for k,v in artifacts.items()},
        proposed_artifact_sha256={k:digest(v.encode()) for k,v in proposals.items()},
        original_records_action='retain_as_original_evidence',corrections_approved=False,
        full_portfolio_continuity_verified=False,publication_authorized=False),proposals


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--local-restored-database',required=True)
    parser.add_argument('--april-realized-report',type=Path,required=True)
    parser.add_argument('--reviewed-report-sha256',required=True)
    parser.add_argument('--development-only',required=True,action='store_true')
    args=parser.parse_args();repo=args.repo.expanduser().resolve()
    if Path(__file__).resolve().parents[2]!=repo:parser.error('Run the installed development module.')
    def git(*parts):return subprocess.check_output(['git','-C',str(repo),*parts],text=True).strip()
    if git('branch','--show-current')!='feature/historical-backfill-2025' or git('status','--porcelain'):
        parser.error('Use the clean, committed development checkout.')
    capture=args.capture.expanduser();verification=decode(capture.with_name('capture-verification.json').read_bytes())
    if digest(capture.read_bytes())!=verification['compressed_file_sha256']:parser.error('Capture checksum differs.')
    with gzip.open(capture,'rb') as stream:raw=stream.read(LIMIT+1)
    if len(raw)>LIMIT or digest(raw)!=verification['capture_sha256']:parser.error('Capture content checksum differs.')
    bundle=validate(decode(raw));pdf=args.april_realized_report.expanduser()
    if digest(pdf.read_bytes())!=args.reviewed_report_sha256:parser.error('Reviewed PDF checksum differs.')
    result=subprocess.run(['pdftotext','-layout',str(pdf),'-'],check=True,capture_output=True,text=True,timeout=60)
    if '04/01/2026' not in result.stdout or '04/30/2026' not in result.stdout:parser.error('Expected the reviewed April 2026 report.')
    records=read_realized_gain_loss_section(result.stdout.splitlines())
    with connect_local(args.local_restored_database) as connection:
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            require_workspace(connection,bundle);before=table_manifest(connection)
            plan,proposals=build_preview(bundle['artifacts'],records,args.reviewed_report_sha256)
            if table_manifest(connection)!=before:raise ValueError('Read-only database verification differs.')
    output=Path.home()/'Downloads'/('CampaignIQ-correction-preview-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    output.mkdir(mode=0o700,exist_ok=False)
    for name,text in proposals.items():
        with (output/name).open('x') as stream:os.chmod(stream.name,0o600);stream.write(text)
    plan.update(capture_sha256=verification['capture_sha256'],development_commit=git('rev-parse','HEAD'),
        local_restored_database=args.local_restored_database,read_only_database_verified=True,
        all_user_table_manifest=before)
    save(output/'correction-preview.json',plan)
    print('Original database capture match: PASS\nApril: one missing stock-sale record proposed; existing records unchanged.\nFebruary: one lot reference proposed; all monetary amounts unchanged.\nRead-only database verification: PASS')
    print('Preview:',output/'correction-preview.json')
    print('No database records changed. Corrections and publication remain unapproved.')


if __name__=='__main__':main()
