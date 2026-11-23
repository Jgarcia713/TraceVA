"""Parser 'adult-v1' checked against an independent stdlib reading of the fixture."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.analysis.datasets import (
    ADULT_COLUMNS,
    ADULT_NUMERIC,
    DatasetIntegrityError,
    parse_adult,
    resolve_data_path,
)
from tests.reference import FIXTURE_PATH, raw_rows


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return parse_adult(FIXTURE_PATH)


def test_shape_and_columns(frame: pd.DataFrame) -> None:
    assert list(frame.columns) == ADULT_COLUMNS
    assert len(frame) == len(raw_rows()) == 50  # trailing blank line not counted


def test_dtypes(frame: pd.DataFrame) -> None:
    for col in ADULT_COLUMNS:
        expected = "Int64" if col in ADULT_NUMERIC else "category"
        assert str(frame[col].dtype) == expected, col


def test_question_mark_becomes_missing(frame: pd.DataFrame) -> None:
    rows = raw_rows()
    for i, col in enumerate(ADULT_COLUMNS):
        expected_missing = sum(1 for r in rows if r[i] == "?")
        assert int(frame[col].isna().sum()) == expected_missing, col
        if col not in ADULT_NUMERIC:
            assert "?" not in set(frame[col].cat.categories), col
    assert int(frame["workclass"].isna().sum()) > 0  # fixture really exercises this


def test_values_have_no_leading_whitespace(frame: pd.DataFrame) -> None:
    assert set(frame["sex"].cat.categories) == {"Female", "Male"}
    assert set(frame["income"].cat.categories) <= {"<=50K", ">50K"}


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bad.data"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


GOOD_ROW = (
    "39, State-gov, 77516, Bachelors, 13, Never-married, Adm-clerical, Not-in-family, "
    "White, Male, 2174, 0, 40, United-States, <=50K"
)


def test_rejects_test_split_label_format(tmp_path: Path) -> None:
    with pytest.raises(DatasetIntegrityError, match="income labels"):
        parse_adult(_write(tmp_path, GOOD_ROW + ".\n"))


def test_rejects_short_row(tmp_path: Path) -> None:
    short = GOOD_ROW.rsplit(", ", 1)[0]
    with pytest.raises(DatasetIntegrityError, match="empty fields"):
        parse_adult(_write(tmp_path, f"{GOOD_ROW}\n{short}\n"))


def test_rejects_empty_field(tmp_path: Path) -> None:
    with pytest.raises(DatasetIntegrityError, match="empty fields"):
        parse_adult(_write(tmp_path, GOOD_ROW.replace("State-gov", "", 1) + "\n"))


def test_rejects_long_row(tmp_path: Path) -> None:
    with pytest.raises(DatasetIntegrityError, match="malformed"):
        parse_adult(_write(tmp_path, f"{GOOD_ROW}\n{GOOD_ROW}, extra\n"))


def test_rejects_non_numeric_in_numeric_column(tmp_path: Path) -> None:
    with pytest.raises(DatasetIntegrityError, match="malformed"):
        parse_adult(_write(tmp_path, GOOD_ROW.replace("39,", "thirty-nine,", 1) + "\n"))


def test_resolve_data_path_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(DatasetIntegrityError, match="escapes"):
        resolve_data_path(tmp_path, "../outside.data")
    assert resolve_data_path(tmp_path, "adult/adult.data") == (tmp_path / "adult/adult.data")
