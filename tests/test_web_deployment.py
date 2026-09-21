"""Deployment contract for the CampaignIQ web process."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT_ROOT / "scripts" / "start_web.sh"


def test_web_start_script_exists_and_is_executable() -> None:
    assert START_SCRIPT.is_file()
    assert START_SCRIPT.stat().st_mode & 0o111


def test_web_start_script_uses_streamlit_dashboard() -> None:
    text = START_SCRIPT.read_text()

    assert "python -m streamlit run src/campaigniq/ui/dashboard.py" in text
    assert "--server.address=0.0.0.0" in text
    assert '--server.port="${PORT}"' in text
    assert "--server.headless=true" in text


def test_web_start_script_has_safe_default_port() -> None:
    text = START_SCRIPT.read_text()

    assert ': "${PORT:=8501}"' in text
