"""New-account deployment comparisons in one authorized read-only SQL snapshot."""
from datetime import date
from decimal import Decimal
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_results import _deployment_results_session
from campaigniq.account_signedin_comparison import PublishedSelection,PublishedComparison
from campaigniq.persistence.deployment_account_directory import DirectoryTransaction
from campaigniq.persistence.deployment_account_data import _open_session


def compare_deployment_accounts(service,user,year,month,selections,*,include_positions=False):
    if type(service) is not DeploymentMonthlyInputs:raise ValueError('Deployment monthly input service required')
    if type(year) is not int or type(month) is not int or not 1900<=year<=2200:raise ValueError('Integer reporting month required')
    date(year,month,1)
    if (type(selections) is not tuple or not 1<=len(selections)<=20
        or any(type(item) is not PublishedSelection for item in selections) or type(include_positions) is not bool):
        raise ValueError('Select one to twenty saved new accounts and explicit combined-position choice')
    ids=tuple(item.account_id for item in selections)
    if len(set(ids))!=len(ids):raise ValueError('Duplicate account selection')
    gateway=service.gateway;sessions=[]
    with gateway._guard(user,ids[0]):
        with gateway._connection(readonly=True) as conn:
            directory,actor=DirectoryTransaction(conn,gateway._directory.settings)._actor(user)
            directory.select(actor.identity,ids)
            try:
                drafts=[]
                for item in selections:
                    session,_=_open_session(conn,gateway._directory.settings,user,item.account_id)
                    sessions.append(session);draft=service._lock(session,item.draft_id)
                    if (draft.year,draft.month)!=(year,month) or draft.revision!=item.revision:
                        raise ValueError('Selected month or displayed source revision changed')
                    drafts.append(draft)
                results=tuple(_deployment_results_session(service,session,draft,item.account_id,item.revision)
                    for session,draft,item in zip(sessions,drafts,selections,strict=True))
                totals={}
                if include_positions:
                    for result in results:
                        for name,quantity in result.positions:totals[name]=totals.get(name,Decimal(0))+Decimal(quantity)
                for session in sessions:session._check()
                return PublishedComparison(f'{year:04d}-{month:02d}',results,tuple((k,str(v)) for k,v in sorted(totals.items())),include_positions)
            finally:
                for session in sessions:session._active=False
