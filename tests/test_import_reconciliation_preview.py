"""Structural guards for the read-only import preview."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_preview_returns_before_any_publication():
    tree = ast.parse((ROOT / "src/campaigniq/monthly_import_execution.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "execute_monthly_import")
    assert any(a.arg == "preview_only" for a in fn.args.kwonlyargs)
    src = ast.get_source_segment((ROOT / "src/campaigniq/monthly_import_execution.py").read_text(), fn)
    assert src.index("if preview_only:") < src.index("_archive_thinkorswim_trade_history(", src.index("if preview_only:"))
    assert src.index("if preview_only:") < src.index("ensure_publication_protocol_in_storage(")


def test_dashboard_preview_uses_read_only_execution():
    src = (ROOT / "src/campaigniq/ui/dashboard.py").read_text()
    assert 'key="monthly_import_preview"' in src
    assert 'preview_only=True' in src
    assert 'Saved now: {stored.name}' in src
