from datetime import date,datetime
from decimal import Decimal
import json
import pytest
from campaigniq.broker_report_period import validate_broker_report_date
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
from tests.test_account_legacy_results import retained,harness,local,read
from tests.test_account_signedin_results import results,signed,month,bound_context,context,FIXTURES,publish,load


@pytest.mark.parametrize('start,end,closed',[
 ('2026-01-01','2026-01-31','2026-02-02'),
 ('2026-04-01','2026-04-30','2026-05-01'),
 ('2026-08-01','2026-08-31','2026-09-01'),
 ('2025-08-01','2025-08-31','2025-09-02'),
 ('2026-12-01','2026-12-31','2027-01-04'),
 ('2026-08-01','2026-08-31','2026-08-01'),
 ('2026-08-01','2026-08-31','2026-08-31')])
def test_existing_report_and_first_business_day_calendar(start,end,closed):
    validate_broker_report_date(date.fromisoformat(closed),period_start=date.fromisoformat(start),period_end=date.fromisoformat(end))


@pytest.mark.parametrize('closed',['2026-01-31','2026-03-01','2026-03-03','2027-03-02'])
def test_unrelated_dates_still_rejected(closed):
    with pytest.raises(ValueError,match='outside'):
        validate_broker_report_date(date.fromisoformat(closed),period_start=date(2026,2,1),period_end=date(2026,2,28))


def report(closed):
    record=RealizedGainLossRecord(closed,Instrument('SPY'),Decimal('1'),Decimal('100.12'),
        Decimal('100.12'),Decimal('90.10'),Decimal('10.02'),'FIFO','Short')
    return serialize_realized_attributions(period_start=date(2025,1,1),period_end=date(2025,1,31),attributions=(RealizedAttribution(record,()),))


def test_legacy_reader_preserves_spillover_date_amount_and_source_bytes(retained):
    h=retained;key='2025-01-realized-attributions.json';h.rows[key]=report(date(2025,2,3));before=dict(h.rows)
    result=read(h)
    assert result.realized[0][0]=='2025-02-03' and result.realized[0][-1]=='10.02'
    assert Decimal(result.brokerage_realized_usd)==Decimal('10.02') and h.rows==before
    value=json.loads(h.rows[key]);value['period_end']='2025-02-28';h.rows[key]=json.dumps(value)
    with pytest.raises(ValueError,match='artifact period'):read(h)


def test_signedin_reader_preserves_spillover_date_amount_and_original_receipt(results):
    c=results;storage=c.artifacts[c.account];key='2025-01-realized-attributions.json'
    storage.rows[key]=report(date(2025,2,3));before=dict(storage.rows)
    result=load(c)
    assert result.realized[0][0]=='2025-02-03' and result.realized[0][-1]=='10.02'
    assert Decimal(result.brokerage_realized_usd)==Decimal('10.02') and storage.rows==before
    storage.rows[key]=report(date(2025,2,4))
    with pytest.raises(ValueError,match='outside'):load(c)


def test_legacy_reader_unrelated_later_date_still_blocks_entire_report(retained):
    h=retained;h.rows['2025-01-realized-attributions.json']=report(date(2025,2,4))
    with pytest.raises(ValueError,match='outside'):read(h)
