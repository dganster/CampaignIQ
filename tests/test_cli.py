from pathlib import Path

from campaigniq.cli import _build_parser


def test_cli_import_period_parser_accepts_demo_arguments() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "import-period",
            "--start", "2026-01-01",
            "--end", "2026-01-31",
            "--trades", "jan.csv",
            "--opening-snapshot", "dec.txt",
            "--snapshot-at", "2025-12-31T00:00:00",
            "--realized", "realized.txt",
            "--assignments", "assignments.txt",
            "--history", "dec.csv",
            "--history", "nov.csv",
            "--history-start", "2025-11-01",
            "--historical-source-root", "tests/data/thinkorswim",
        ]
    )

    assert args.command == "import-period"
    assert args.start.isoformat() == "2026-01-01"
    assert args.end.isoformat() == "2026-01-31"
    assert args.history == [Path("dec.csv"), Path("nov.csv")]
    assert args.historical_source_root == Path("tests/data/thinkorswim")
