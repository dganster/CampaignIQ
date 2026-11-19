"""Readable currency formatting while retaining numeric table values for sorting."""
import pandas as pd
from decimal import Decimal
from campaigniq.ui.table_layout import render_dataframe


def display_money(value):
    if value is None or pd.isna(value):
        return 'Unavailable'
    if value == 0:
        return '$0.00'
    return f'-${abs(value):,.2f}' if value < 0 else f'${value:,.2f}'


def display_quantity(value):
    """Group digits without rounding away fractional shares or crypto units."""
    if value is None or pd.isna(value):
        return 'Unavailable'
    number = Decimal(str(value))
    if not number.is_finite():
        return 'Unavailable'
    text = format(number, ',f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if number == 0 else text


def display_exact_money(value):
    """Keep execution-price precision, with at least two currency decimals."""
    if value is None or pd.isna(value):
        return 'Unavailable'
    number = Decimal(str(value))
    if not number.is_finite():
        return 'Unavailable'
    places = max(2, -number.as_tuple().exponent)
    sign = '-' if number < 0 else ''
    return f'{sign}${abs(number):,.{places}f}'


def render_numeric_table(ui, rows, *, quantities=(), money=(), **kwargs):
    """Format display only; retain numeric values and row order for sorting."""
    frame = rows.copy(deep=True) if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    formats = {column: display_quantity for column in quantities if column in frame}
    formats.update({column: display_money for column in money if column in frame})
    return render_dataframe(ui, frame.style.format(formats, na_rep='Unavailable'), **kwargs)


def render_money_table(ui, rows, money_columns):
    frame=pd.DataFrame(rows)
    styled=frame.style.format({column:display_money for column in money_columns},na_rep='Unavailable')
    return render_dataframe(ui,styled,hide_index=True,use_container_width=True)
