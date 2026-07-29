"""Pytest configuration."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"

sys.path.insert(0, str(SRC))
