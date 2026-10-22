"""Dashboard integration regression checks for the import draft workflow."""
from pathlib import Path
import ast

def test_dashboard_compiles_with_draft_integration():
    source = Path(__file__).resolve().parents[1] / 'src/campaigniq/ui/dashboard.py'
    ast.parse(source.read_text())

def test_draft_is_workspace_scoped_and_validation_is_revision_bound():
    source = (Path(__file__).resolve().parents[1] / 'src/campaigniq/ui/dashboard.py').read_text()
    assert 'access_workspace=ACCESS.workspace_id if ACCESS is not None else None' in source
    assert 'restore_uploads(draft_repo, draft.id)' in source
    assert 'monthly_import_validated_draft_revision' in source
    assert source.count('assert_revision_unchanged(') >= 2

def test_local_filesystem_import_path_is_preserved():
    source = (Path(__file__).resolve().parents[1] / 'src/campaigniq/ui/dashboard.py').read_text()
    assert 'uploads[role] = stored if draft_repo is not None else new_upload' in source
