"""Regression coverage for option strike labels without importing Streamlit."""
import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from campaigniq.domain.option_contract import OptionContract, OptionType
from campaigniq.domain.value_objects.instrument import Instrument


def formatter():
    source = Path("src/campaigniq/ui/dashboard.py").read_text()
    tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"_display_decimal", "_display_instrument"}]
    scope = {"OptionContract": OptionContract, "Instrument": Instrument}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "dashboard_formatters", "exec"), scope)
    return scope["_display_instrument"]


@pytest.mark.parametrize("strike, expected", [("270", "270"), ("970", "970"), ("1000", "1000"), ("270.00", "270"), ("27.50", "27.5"), ("0.50", "0.5")])
def test_option_strike_label_preserves_significant_zeros(strike, expected):
    contract = OptionContract(underlying="APD", expiration=date(2026, 7, 17), strike=Decimal(strike), option_type=OptionType.CALL)
    assert formatter()(contract) == f"APD Jul 17 2026 ${expected} Call"
