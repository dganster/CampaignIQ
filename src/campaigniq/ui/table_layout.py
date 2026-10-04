"""Consistent table sizing without changing row selection or numeric types."""
from datetime import date, datetime
from decimal import Decimal
from copy import deepcopy
import math
import re
from hashlib import sha256
import pandas as pd

FIT_KEY = "campaigniq_fit_table_columns"


def _state(ui):
    state = getattr(ui, "session_state", {})
    return state if hasattr(state, "get") else {}


def selection_table_key(ui, key):
    return key + ("_fit" if _state(ui).get(FIT_KEY, False) else "")


def fitted_column_config(table, config):
    """Size against displayed values, including formatted money and dates."""
    frame = table.data if hasattr(table, "data") and isinstance(table.data, pd.DataFrame) else table
    if isinstance(frame, (list, tuple)):
        frame = pd.DataFrame(frame)
    if not isinstance(frame, pd.DataFrame):
        return config
    result = deepcopy(config or {})
    for column in frame.columns:
        existing = result.get(column)
        if existing is None or isinstance(existing, str):
            existing = {"label": existing or str(column)}
        else:
            existing = dict(existing)
        options = existing.get("type_config", {})
        fmt = options.get("format", "") or ""
        label = existing.get("label") or str(column)
        def display(value):
            if value is None or (isinstance(value, float) and math.isnan(value)):
                return "—"
            if isinstance(value, (date, datetime)):
                return value.strftime("%b %d, %Y")
            if isinstance(value, (float, int, Decimal)) and not isinstance(value, bool):
                if "$" in fmt or "P&L" in str(column):
                    return f"${value:,.2f}"
                if fmt.endswith("%%") or fmt == "percent":
                    return f"{value:,.1f}%"
                decimals = re.search(r"\.(\d+)f", fmt)
                if decimals:
                    sign = "+" if "+" in fmt else ""
                    return format(value, f"{sign},.{int(decimals[1])}f")
                return f"{value:,}"
            return str(value)
        length = max([len(str(label))] + [max(map(len, display(value).splitlines()), default=0)
                                         for value in frame[column]])
        existing["width"] = max(70, length * 8 + 36)
        result[column] = existing
    return result


def render_dataframe(ui, table, **kwargs):
    state = _state(ui)
    if state.get(FIT_KEY, False):
        kwargs["column_config"] = fitted_column_config(table, kwargs.get("column_config"))
        kwargs.pop("use_container_width", None)
        kwargs["width"] = "stretch"
        # Explicit keys used by selection callbacks are already resolved by
        # selection_table_key. Give other tables a fresh client layout too.
        if "key" not in kwargs:
            number = state.get("campaigniq_table_render_number", 0)
            state["campaigniq_table_render_number"] = number + 1
            frame = table.data if hasattr(table, "data") and isinstance(table.data, pd.DataFrame) else table
            schema = repr(tuple(frame.columns) if isinstance(frame, pd.DataFrame) else tuple(frame[0]) if frame else ())
            fingerprint = sha256(schema.encode()).hexdigest()[:12]
            kwargs["key"] = f"campaigniq_fitted_table_{fingerprint}_{number}"
        elif "on_select" not in kwargs:
            kwargs["key"] = selection_table_key(ui, kwargs["key"])
    return ui.dataframe(table, **kwargs)
