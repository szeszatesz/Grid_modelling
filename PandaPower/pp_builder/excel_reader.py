"""
excel_reader.py — reads and validates the MAVIR Excel workbook.
Returns a dict {sheet_name: DataFrame} after stripping blank rows
and checking that all mandatory columns are present.
"""
from __future__ import annotations

import pandas as pd
import math

from .config import REQUIRED_COLUMNS


class ExcelReaderError(ValueError):
    """Raised when the workbook fails validation."""


def read_excel(path: str) -> dict[str, pd.DataFrame]:
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
