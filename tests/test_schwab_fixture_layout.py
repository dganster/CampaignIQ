from pathlib import Path


def test_schwab_fixtures_have_one_canonical_location() -> None:
    data_dir = Path("tests/data")
    canonical_dir = data_dir / "schwab"

    assert canonical_dir.is_dir()

    misplaced = sorted(
        path.name
        for path in data_dir.glob("schwab_*")
        if path.is_file()
    )

    assert misplaced == [], (
        "Schwab fixtures must live under tests/data/schwab/: "
        + ", ".join(misplaced)
    )


def test_schwab_fixture_references_use_canonical_paths() -> None:
    root = Path(".")
    legacy_names = (
        "tests/data/schwab_january_positions.txt",
        "tests/data/schwab_december_positions.txt",
    )

    stale_references: list[str] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path == Path("tests/test_schwab_fixture_layout.py"):
            continue

        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue

        for legacy_name in legacy_names:
            if legacy_name in text:
                stale_references.append(f"{path}: {legacy_name}")

    assert stale_references == [], (
        "Legacy Schwab fixture references remain:\n"
        + "\n".join(stale_references)
    )
