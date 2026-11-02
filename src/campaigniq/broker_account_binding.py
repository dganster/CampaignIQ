"""Evidence account identity for the synthetic Schwab import preview only."""
from dataclasses import dataclass
from hashlib import sha256
import re

ACTION='bind_broker_account_v1'
KINDS=('securities','forex')


def normalize(kind, number):
    if kind not in KINDS or not isinstance(number,str):
        raise ValueError('Supported account kind and full identifier required')
    value=number.strip()
    if kind=='securities' and re.fullmatch(r'[0-9]{4}-[0-9]{4}',value):
        value=value.replace('-','')
    pattern=r'[0-9]{8}' if kind=='securities' else r'[0-9]{6,12}'
    if not re.fullmatch(pattern,value):
        raise ValueError('Use the full supported account identifier; masked identifiers are not accepted')
    return value


@dataclass(frozen=True)
class BrokerBinding:
    kind: str
    digest: str
    suffix: str

    @classmethod
    def create(cls,kind,number):
        value=normalize(kind,number)
        return cls(kind,sha256(('schwab:'+kind+':'+value).encode()).hexdigest(),value[-4:])


def bindings(directory, account_id):
    result={}
    if not any(a.id==account_id for a in directory.accounts):
        raise ValueError('Unknown account')
    for event in directory.events:
        if event.action!=ACTION or event.target_id!=account_id: continue
        details=dict(event.details)
        if (set(details)!={'broker','kind','digest','suffix'} or details['broker']!='schwab'
                or details['kind'] not in KINDS or not re.fullmatch('[0-9a-f]{64}',details['digest'])
                or not re.fullmatch('[0-9]{4}',details['suffix'])):
            raise ValueError('Invalid broker-binding audit evidence')
        result[details['kind']]=BrokerBinding(details['kind'],details['digest'],details['suffix'])
    return result


def bind(directory,actor,account_id,kind,number,*,expected_revision,confirm_replace=False):
    acting=directory._admin(actor,expected_revision)
    if type(confirm_replace) is not bool:
        raise ValueError('Explicit replacement confirmation required')
    account=next((a for a in directory.accounts if a.id==account_id),None)
    if account is None or account.archived:
        raise ValueError('Active account required')
    candidate=BrokerBinding.create(kind,number)
    current=bindings(directory,account_id).get(kind)
    if current==candidate: return directory
    if current is not None and not confirm_replace:
        raise ValueError('Confirm replacement of the existing broker binding')
    for other in directory.accounts:
        if other.id!=account_id:
            other_binding=bindings(directory,other.id).get(kind)
            if other_binding and other_binding.digest==candidate.digest:
                raise ValueError('This broker account is already bound to another account in this registry')
    return directory._changed(acting,ACTION,account_id,details=(('broker','schwab'),('kind',kind),
        ('digest',candidate.digest),('suffix',candidate.suffix)))


def document_identity(role,text):
    """Recognize explicit account headers only; never search transaction amounts."""
    if role=='schwab_forex_transaction_report':
        kind='forex'; values=re.findall(r'Account \(USD\):\s*([^,\s]+)',text)
    elif role=='schwab_forex_monthly_statement':
        kind='forex'; values=[]
        for area in re.split(r'Account\s+Account Currency',text)[1:]:
            match=re.match(r'\s*([0-9]+)\s+USD\b',area)
            if not match: raise ValueError('Document account identifier is missing, masked, or ambiguous')
            values.append(match[1])
    elif role=='thinkorswim_trade_history':
        kind='securities'
        values=re.findall(r'(?im)^\s*"?Account Statement for\s+(?:Account\s+)?(.+?)\s+(?:since|from)\b',text)
        # Accept the explicit ToS Schwab suffix without discarding arbitrary text.
        values=[re.sub(r'(?i)^([0-9]{8}|[0-9]{4}-[0-9]{4})SCHW(?:[ \t]+\([^()\r\n]*\))?$',
                       r'\1',value) for value in values]
    elif role in {'schwab_brokerage_statement','schwab_opening_position_snapshot',
                  'schwab_next_month_assignment_evidence','schwab_realized_gain_loss'}:
        kind='securities';values=[]
        lines=text.splitlines()
        for index,line in enumerate(lines):
            if not re.search(r'Account (?:Number|number|No\.)\b',line): continue
            # A Schwab header can put the owner name between label and number.
            area='\n'.join(lines[index:index+4])
            candidates=re.findall(r'(?<![0-9*])([0-9]{4}-[0-9]{4})(?![0-9])',area)
            if len(set(candidates))!=1:
                raise ValueError('Document account identifier is missing, masked, or ambiguous')
            values.extend(candidates)
    else:
        raise ValueError('Account identity is not yet supported for this document role')
    normalized={normalize(kind,value) for value in values}
    if len(normalized)!=1:
        raise ValueError('Document account identifier is missing, masked, or ambiguous')
    return BrokerBinding.create(kind,next(iter(normalized)))


def verify_document(known,role,text):
    observed=document_identity(role,text)
    expected=known.get(observed.kind)
    if expected is None:
        raise ValueError('Bind the '+observed.kind+' account identifier before saving this document')
    if expected!=observed:
        raise ValueError('Document belongs to a different broker account')
    return observed.kind
