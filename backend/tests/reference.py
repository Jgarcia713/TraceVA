"""Independent reference readings of the fixture using only the standard library.

Tool outputs are compared against these, so that tests do not merely check pandas
against pandas.
"""

from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_PATH = FIXTURES_DIR / "adult_sample.data"


def raw_rows(path: Path = FIXTURE_PATH) -> list[list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.split(", ") for line in lines if line.strip()]
