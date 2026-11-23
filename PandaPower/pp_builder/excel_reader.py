"""
excel_reader.py — reads and validates the MAVIR Excel workbook.
Returns a dict {sheet_name: DataFrame} after stripping blank rows
and checking that all mandatory columns are present.
"""
from __future__ import annotations

import pandas as pd
import math

import re
import unicodedata
import warnings

import numpy as np

from .config import (
    REQUIRED_COLUMNS, MEKH_SHEET_NAME,
    MEKH_HEADER_ROW, MEKH_COLUMN_KEYWORDS, MEKH_REQUIRED_KEYS,
    MEKH_MEZOCSAT_SITE, MEKH_MEZOCSAT_WRONG_MVA, MEKH_MEZOCSAT_FIXED_MVA,
    MEKH_MEZOCSAT_EXPECTED_ROWS,
    MEKH_DETK_SITE, MEKH_DETK_OWNER_KEY, MEKH_DETK_WRONG_MVA, MEKH_DETK_FIXED_MVA,
)


class ExcelReaderError(ValueError):
    """Raised when the workbook fails validation."""

class DataAlarm(UserWarning):
    """Auto-corrected or suspicious input data (alarm only, no abort)."""


def read_excelsheets(path: str) -> dict[str, pd.DataFrame]:
    """
    Read all builder sheets from *path* and return a validated dict.

    Parameters
    ----------
    path : str
        Path to the MAVIR Excel workbook.

    Returns
    -------
    dict[str, pd.DataFrame]
        Keys are the sheet-name constants from config.py.
    """
    try:
        raw = pd.read_excel(path, sheet_name=None, header=0)
    except FileNotFoundError:
        raise ExcelReaderError(f"Excel file not found: {path!r}")

    sheets: dict[str, pd.DataFrame] = {}

    for sheet_name, required_cols in REQUIRED_COLUMNS.items():
        if sheet_name not in raw:
            raise ExcelReaderError(
                f"Sheet '{sheet_name}' not found in workbook '{path}'. "
                f"Available sheets: {list(raw.keys())}"
            )
        

        df = raw[sheet_name].copy()

        # Drop entirely blank rows
        df.dropna(how="all", inplace=True)
        df.reset_index(drop=True, inplace=True)

        # Drop the units/description row (always the first row after the header)
        df = df.iloc[1:].reset_index(drop=True)

        # Validate mandatory columns
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ExcelReaderError(
                f"Sheet '{sheet_name}' is missing required columns: {missing}. "
                f"Found columns: {list(df.columns)}"
            )

        sheets[sheet_name] = df

    return sheets


def _float(row: pd.Series, col: str, default: float = 0.0) -> float:
    """Return float value from row[col], falling back to default on NaN/missing."""
    val = row.get(col, default)
    if pd.isna(val):
        return default
    return float(val)


def _bool(row: pd.Series, col: str, default: bool = True) -> bool:
    """Return bool-like value from row[col]."""
    val = row.get(col, default)
    if pd.isna(val):
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return str(val).strip().lower() in ("1", "true", "yes", "ja", "igen")


def _str(row: pd.Series, col: str, default: str = "") -> str:
    val = row.get(col, default)
    if pd.isna(val):
        return default
    return str(val).strip()

def _int(row: pd.Series, col: str, default: int = 0) -> int:
    v = _float(row, col, float("nan"))
    return int(v) if not math.isnan(v) else default

def read_mekh_projects(path: str) -> pd.DataFrame:
    """Read the MEKH project list, drop the numbering column, auto-fix known errors."""
    try:
        raw = pd.read_excel(path, sheet_name=MEKH_SHEET_NAME, header=MEKH_HEADER_ROW)
    except FileNotFoundError:
        raise ExcelReaderError(f"Excel file not found: {path!r}")

    # 1) ignore numbering column(s): by name, or by "1." / "2." ... values
    drop = []
    for c in raw.columns:
        n = _norm(c)
        vals = raw[c].dropna().astype(str).str.strip()
        looks_seq = (len(vals) > 5 and vals.str.fullmatch(r"\d+\.?").all()
                     and pd.to_numeric(vals.str.rstrip("."), errors="coerce")
                           .is_monotonic_increasing)
        if n.startswith("sorsz") or n.startswith("unnamed") or looks_seq:
            drop.append(c)
    if drop:
        _alarm(f"MEKH: numbering column(s) ignored: {drop}")
    df = raw.drop(columns=drop).dropna(how="all").reset_index(drop=True)

    # 2) map logical columns by header keywords
    cols: dict[str, str] = {}
    for key, kws in MEKH_COLUMN_KEYWORDS.items():
        for c in df.columns:
            if all(k in _norm(c) for k in kws):
                cols[key] = c
                break
    missing = [k for k in MEKH_REQUIRED_KEYS if k not in cols]
    if missing:
        raise ExcelReaderError(
            f"MEKH list is missing columns {missing}. Found: {list(df.columns)}")

    cap = cols["feed_mva"]
    site = df[cols["site"]].map(_norm)
    owner = df[cols["owner"]].map(_norm)
    val = df[cap].map(_parse_mva)          # csak az összehasonlításhoz

    # 3) Mezőcsát: 5 x 46,7 MW
    mz = site.str.startswith(MEKH_MEZOCSAT_SITE)
    bad = mz & np.isclose(val, MEKH_MEZOCSAT_WRONG_MVA)
    if bad.any():
        df.loc[bad, cap] = [_fmt_like(MEKH_MEZOCSAT_FIXED_MVA, o) for o in df.loc[bad, cap]]
        val[bad] = MEKH_MEZOCSAT_FIXED_MVA
        _alarm(f"MEKH Mezőcsát: {int(bad.sum())} rows corrected "
               f"{MEKH_MEZOCSAT_WRONG_MVA} -> {MEKH_MEZOCSAT_FIXED_MVA} MVA")
    n_ok = int((mz & np.isclose(val, MEKH_MEZOCSAT_FIXED_MVA)).sum())
    if n_ok != MEKH_MEZOCSAT_EXPECTED_ROWS:
        _alarm(f"MEKH Mezőcsát: {n_ok} rows of {MEKH_MEZOCSAT_FIXED_MVA} MVA, "
               f"expected {MEKH_MEZOCSAT_EXPECTED_ROWS}")

    # 4) Detk – MVM Zöld Generáció: 125 -> 1,25 MW
    dk = (site.str.startswith(MEKH_DETK_SITE)
          & owner.str.contains(MEKH_DETK_OWNER_KEY, regex=False)
          & np.isclose(val, MEKH_DETK_WRONG_MVA))
    if dk.any():
        df.loc[dk, cap] = [_fmt_like(MEKH_DETK_FIXED_MVA, o) for o in df.loc[dk, cap]]
        _alarm(f"MEKH Detk/MVM Zöld Generáció: {int(dk.sum())} row corrected "
               f"{MEKH_DETK_WRONG_MVA} -> {MEKH_DETK_FIXED_MVA} MVA")
    else:
        _alarm("MEKH Detk/MVM Zöld Generáció row with 125 MVA not found "
               "(already fixed or file changed?)")
    return df


def _alarm(msg: str) -> None:
    warnings.warn(msg, DataAlarm, stacklevel=3)


def _norm(text) -> str:
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _parse_mva(v) -> float:
    """'233,5 MVA' / '0,9985 MVA' / 233.5 -> float (NaN, ha nem értelmezhető)."""
    if isinstance(v, (int, float, np.number)):
        return float(v) if not pd.isna(v) else np.nan
    m = _NUM_RE.search(str(v).replace("\xa0", " "))
    return float(m.group().replace(",", ".")) if m else np.nan


def _fmt_like(x: float, like) -> object:
    """x kiírása az eredeti cella formátumában ('46,7 MVA')."""
    if isinstance(like, (int, float, np.number)):
        return float(x)
    txt = f"{x:.6g}".replace(".", ",")
    return f"{txt} MVA" if "mva" in str(like).lower() else txt
