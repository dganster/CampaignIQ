"""Project dependency metadata tests."""

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


def optional_dependencies() -> dict[str, list[str]]:
    with PYPROJECT.open("rb") as stream:
        project = tomllib.load(stream)["project"]
    return project["optional-dependencies"]


def package_names(requirements: list[str]) -> set[str]:
    names = set()
    for requirement in requirements:
        name = requirement.split(">=", 1)[0]
        name = name.split("==", 1)[0]
        name = name.split("<", 1)[0]
        names.add(name)
    return names


def test_ui_extra_declares_direct_dashboard_dependencies() -> None:
    extras = optional_dependencies()

    assert package_names(extras["ui"]) == {
        "Authlib",
        "altair",
        "httpx",
        "pandas",
        "streamlit",
    }


def test_dev_extra_declares_test_and_lint_tools() -> None:
    extras = optional_dependencies()

    assert package_names(extras["dev"]) == {
        "pytest",
        "ruff",
    }
