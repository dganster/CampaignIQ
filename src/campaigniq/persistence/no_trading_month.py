"""Read-only dashboard envelope checks; authenticated readers also rebuild sources."""
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from campaigniq.persistence.monthly_publication import is_month_published_in_storage

SUFFIXES={'lot-book.json','position-journal.json','realized-attributions.json','forex-settlement-attributions.json',
    'lifecycle-transitions.json','boundary-completeness.json','market-applicability.json','import-provenance.json'}
def load_income(storage,*,period_end):
    prefix=period_end.strftime('%Y-%m')+'-';key=prefix+'no-trading-month.json'
    receipt_key=prefix+'account-publication-receipt.json'
    receipt=json.loads(storage.read_text(receipt_key)) if storage.exists(receipt_key) else {}
    descriptor=receipt.get('no_trading_generation')
    if not storage.exists(key) and descriptor is None:return None
    if not storage.exists(key) or descriptor is None or not is_month_published_in_storage(storage,period_end=period_end):
        raise ValueError('Complete published no-trading generation required')
    text=storage.read_text(key);body=json.loads(text)
    try:
        if (body['format']!='campaigniq.no_trading_month' or body['version']!=1 or
            body['review']['period_end']!=str(period_end) or body['review']['period_start']!=str(period_end.replace(day=1)) or
            descriptor!=dict(artifact_key=key,sha256=sha256(text.encode()).hexdigest())):
            raise ValueError('No-trading generation descriptor differs')
        hashes=body['canonical_artifact_sha256']
        if set(hashes)!={prefix+s for s in SUFFIXES}:raise ValueError('Complete no-trading artifact inventory required')
        if any(not storage.exists(k) or sha256(storage.read_text(k).encode()).hexdigest()!=v for k,v in hashes.items()):
            raise ValueError('Published no-trading artifacts differ')
        values=body['review']['financials'];income=Decimal(values['income_usd'])
        if (not income.is_finite() or Decimal(values['realized_trading_pnl_usd'])!=0 or
            Decimal(values['opening_cash_usd'])+income!=Decimal(values['ending_cash_usd']) or
            sum((Decimal(r['amount_usd']) for r in values['cash_income']),Decimal(0))!=income):
            raise ValueError('No-trading cash income equation differs')
        return income
    except (KeyError,TypeError) as error:raise ValueError('Invalid no-trading financial artifact') from error
