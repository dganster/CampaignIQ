"""Cash-posted income and statement-account performance, with explicit coverage."""
from decimal import Decimal

def money(value):return ('−' if value<0 else '')+f'${abs(value):,.2f}'

def show_statement(st,evidence,*,realized=None,known_forex_financing=None):
    if evidence is None or evidence.status!='verified' or evidence.income is None:
        st.info('Dividend, interest and expense coverage is unverified for this statement. Income-inclusive results are unavailable; missing income is not treated as zero.')
        return
    v=evidence.income
    st.subheader('Income and financing')
    st.table([dict(Component=label,USD=money(value)) for label,value in (
        ('Cash dividends',v.dividends),('Credited interest',v.credited_interest),('Interest expense',v.interest_expense),
        ('Other statement expenses / withholding',v.other_expense),('Net statement income',v.net_income))])
    if realized is not None:
        combined=Decimal(realized)+v.net_income
        st.write('Realized trading result plus net statement income: '+money(combined))
        if known_forex_financing is not None:
            st.write('Including separately approved Forex financing: '+money(combined+Decimal(known_forex_financing)))
        st.caption('These are realized results plus cash-posted income and expenses. They exclude unrealized changes and are not total return. Expenses are statement expense rows; trading commissions already reflected in trading results are not added again.')
    st.dataframe([dict(Date=r.date,Type=r.category,Symbol=r.symbol or 'Account',Description=r.description,
        USD=money(r.amount_usd),Campaign=r.campaign_id or 'Unallocated: entitlement evidence unavailable') for r in v.items],hide_index=True)
    st.caption('Income uses statement payment/process dates. A payment date alone does not prove which campaign earned a dividend. Account interest and charges remain account-level; trade-only campaign win rates are unchanged.')
    if v.account_investment_result is not None:
        st.write('Brokerage statement investment result: '+money(v.account_investment_result))
        st.caption('Ending account value minus beginning value and net cash deposits/withdrawals. Income and expenses are already included in account value and are not added again. This is the brokerage statement scope, not a combined brokerage/Forex/crypto return.')
        if v.simple_return is not None:st.write(f'Return with no external cash flows: {v.simple_return:.2%}')
        else:st.info('No percentage return calculated: external cash flows or a nonpositive opening value require a suitable valuation and timing method.')
    else:st.info('Statement investment result unavailable: complete account valuations and zero non-cash activity are required.')

def show_summary(st,summaries):
    verified=[s for s in summaries if s.statement_income_status=='verified']
    st.caption(f'Statement income coverage: {len(verified):,} of {len(summaries):,} published months verified. Missing or unsupported sources are not treated as zero.')
    if not verified:return
    rows=[dict(Month=s.period_start.strftime('%Y-%m'),Dividends=money(s.statement_income.dividends),
        Credited_interest=money(s.statement_income.credited_interest),Interest_expense=money(s.statement_income.interest_expense),
        Other_expenses=money(s.statement_income.other_expense),Net_statement_income=money(s.statement_income.net_income),
        Realized_plus_net_income=money(s.realized_plus_statement_income)) for s in verified]
    st.dataframe(rows,hide_index=True)
    net=sum((s.statement_income.net_income for s in verified),Decimal(0))
    st.metric('Net statement income · verified months',money(net))
    if len(verified)==len(summaries):
        st.metric('Realized trading results + net statement income',money(sum((s.realized_plus_statement_income for s in verified),Decimal(0))))
        if any(s.forex_policy_approved for s in verified):
            st.metric('Including known approved Forex financing',money(sum((s.realized_plus_income_and_known_financing for s in verified),Decimal(0))))
    else:st.info('An all-period income-inclusive total is withheld until every displayed month has verified statement income.')
    st.caption('Income-inclusive realized figures exclude unrealized changes. Forex financing is included only where separately verified; a missing financing source is not proof of zero cost. Crypto remains separate.')
