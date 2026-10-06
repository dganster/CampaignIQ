"""Readable currency formatting while retaining numeric table values for sorting."""
import pandas as pd
from campaigniq.ui.table_layout import render_dataframe


def display_money(value):
    if value == 0:
        return '$0.00'
    return f'-${abs(value):,.2f}' if value < 0 else f'${value:,.2f}'


def render_money_table(ui, rows, money_columns):
    frame=pd.DataFrame(rows)
    styled=frame.style.format({column:display_money for column in money_columns},na_rep='Unavailable')
    return render_dataframe(ui,styled,hide_index=True,use_container_width=True)
