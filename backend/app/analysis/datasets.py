"""Dataset parsing, integrity checks, and the in-process dataframe cache.

Parsing rules are part of the reproducibility contract (ADR-001): any change to how a
file is parsed must bump the parser version so that derived results remain attributable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from threading import Lock

import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype

Parser = Callable[[Path], pd.DataFrame]


def column_kind(series: pd.Series) -> str:
    """Numeric (excluding booleans) vs categorical; determines applicable metrics."""
    return "numeric" if is_numeric_dtype(series) and not is_bool_dtype(series) else "categorical"


class DatasetIntegrityError(RuntimeError):
    """File missing, hash mismatch, path escape, or content not matching the parser."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_data_path(data_dir: Path, relative_path: str) -> Path:
    """Resolve a registry path under DATA_DIR, rejecting anything that escapes it."""
    base = data_dir.resolve()
    candidate = (base / relative_path).resolve()
    if not candidate.is_relative_to(base):
        raise DatasetIntegrityError(f"path escapes DATA_DIR: {relative_path!r}")
    return candidate


# ---------------------------------------------------------------------------
# UCI Adult, parser "adult-v1"
# ---------------------------------------------------------------------------

ADULT_PARSER_VERSION = "adult-v1"

ADULT_NUMERIC = [
    "age",
    "fnlwgt",
    "education_num",
    "capital_gain",
    "capital_loss",
    "hours_per_week",
]
ADULT_COLUMNS = [
    "age",
    "workclass",
    "fnlwgt",
    "education",
    "education_num",
    "marital_status",
    "occupation",
    "relationship",
    "race",
    "sex",
    "capital_gain",
    "capital_loss",
    "hours_per_week",
    "native_country",
    "income",
]
ADULT_INCOME_LABELS = {"<=50K", ">50K"}


def parse_adult(path: Path) -> pd.DataFrame:
    """Parse the UCI `adult.data` format.

    - no header; fields separated by ", " (comma + space)
    - "?" means missing; no other token is treated as missing
    - blank lines (e.g. the trailing one) are skipped
    - numeric columns -> nullable Int64; others -> categorical
    - income labels must be exactly "<=50K" or ">50K" (the adult.test variant with a
      trailing period is rejected, not silently normalized)
    """
    dtypes: dict[str, str] = {
        col: ("Int64" if col in ADULT_NUMERIC else "category") for col in ADULT_COLUMNS
    }
    try:
        frame = pd.read_csv(
            path,
            header=None,
            names=ADULT_COLUMNS,
            sep=",",
            skipinitialspace=True,
            na_values=["?"],
            keep_default_na=False,
            skip_blank_lines=True,
            dtype=dtypes,  # type: ignore[arg-type]
            on_bad_lines="error",
        )
    except (pd.errors.ParserError, ValueError) as exc:
        raise DatasetIntegrityError(f"malformed adult file {path.name}: {exc}") from exc
    # With keep_default_na=False, pandas pads short rows and empty fields with "" rather
    # than failing, which would silently create a "" category. Only "?" means missing.
    empty_fields = {
        col: frame.index[frame[col] == ""].tolist()[:5]
        for col in ADULT_COLUMNS
        if col not in ADULT_NUMERIC and (frame[col] == "").any()
    }
    if empty_fields:
        raise DatasetIntegrityError(f"empty fields (short or malformed rows): {empty_fields}")
    if frame["income"].isna().any():
        raise DatasetIntegrityError("income label is never missing in Adult ('?' found)")
    labels = set(frame["income"].unique())
    unexpected = labels - ADULT_INCOME_LABELS
    if unexpected:
        raise DatasetIntegrityError(f"unexpected income labels: {sorted(unexpected)}")
    return frame


# ---------------------------------------------------------------------------
# Cache: keyed by content identity, verified once per process
# ---------------------------------------------------------------------------

_cache: dict[tuple[str, str, str], pd.DataFrame] = {}
_cache_lock = Lock()


def load_frame(
    path: Path, expected_sha256: str, parser_version: str, parser: Parser
) -> pd.DataFrame:
    """Return the parsed frame, verifying the file hash on first load in this process."""
    key = (str(path), expected_sha256, parser_version)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    if not path.is_file():
        raise DatasetIntegrityError(f"dataset file not found: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise DatasetIntegrityError(
            f"hash mismatch for {path.name}: expected {expected_sha256}, got {actual}"
        )
    frame = parser(path)
    with _cache_lock:
        _cache[key] = frame
    return frame


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
