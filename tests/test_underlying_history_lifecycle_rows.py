"""Exercise underlying history rendering with the lifecycle row contract."""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import pytest


def renderer(monkeypatch):
    tree = ast.parse(Path("src/campaigniq/ui/dashboard.py").read_text())
    names = {"lifecycle_timeline_rows", "_render_position_history"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    shown = []
    class Values(list):
        def dropna(self):
            return [v for v in self if v is not None]
    class Frame:
        def __init__(self, rows):
            self.rows = rows
        def __getitem__(self, column):
            return Values(row[column] for row in self.rows)
    columns = SimpleNamespace(DateColumn=lambda *a, **k: None, TimeColumn=lambda *a, **k: None, NumberColumn=lambda *a, **k: None)
    scope = {"SymbolLifecycleSummary": object,
             "_lifecycle_transition_details": lambda t: ("Verified position evidence", None),
             "history_rows": lambda *a, **k: [], "_display_instrument": str,
             "pd": SimpleNamespace(DataFrame=Frame),
             "st": SimpleNamespace(info=lambda text: None, caption=lambda text: None, column_config=columns),
             "render_dataframe": lambda st, frame, **kwargs: shown.extend(frame.rows)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "history_renderer", "exec"), scope)
    return scope, shown


@pytest.mark.parametrize("symbol", ["SPOT", "MCD", "GS"])
@pytest.mark.parametrize("kind", ["CORPORATE_ACTION", "COVERED_POSITION", "POSITION_EXIT"])
def test_underlying_history_accepts_published_lifecycle_rows(monkeypatch, symbol, kind):
    scope, shown = renderer(monkeypatch)
    transition = SimpleNamespace(occurred_at=datetime(2026, 4, 1), kind=SimpleNamespace(value=kind))
    rows = scope["lifecycle_timeline_rows"](SimpleNamespace(transitions=(transition,)))
    scope["_render_position_history"]((), include_campaign=True, lifecycle_rows=rows)
    if kind in {"CORPORATE_ACTION", "COVERED_POSITION"}:
        assert len(shown) == 1
        assert shown[0]["Action"] == kind.replace("_", " ").title()
        assert shown[0]["Evidence"] == "Published lifecycle evidence"
    else:
        assert shown == []
