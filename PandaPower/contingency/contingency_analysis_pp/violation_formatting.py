"""Excel highlighting for the Violations worksheet.

delta_percent = |post - base| / |base| * 100, a formula in column M.
The threshold sits in an editable cell (P1), so colours update live in Excel.
  yellow : delta_percent <= threshold (violation essentially unchanged)
  red    : delta_percent  > threshold, or no usable base value (new/unknown)
"""
from __future__ import annotations
import math
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

YELLOW = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
RED = PatternFill(start_color="FF5050", end_color="FF5050", fill_type="solid")


def validate_threshold(value) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("violation_change_threshold_percent must be a number") from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("violation_change_threshold_percent must be finite and >= 0")
    return threshold


def highlight_violations(ws, threshold_percent: float = 0.5) -> None:
    """Add delta_percent, the threshold cell and conditional fills to ws."""
    threshold = validate_threshold(threshold_percent)
    headers = {cell.value: cell.column for cell in ws[1]}
    for name in ("base_value", "post_value"):
        if name not in headers:
            raise KeyError(f"Violations sheet has no {name!r} column")
    base = get_column_letter(headers["base_value"])
    post = get_column_letter(headers["post_value"])
    pct_col = ws.max_column + 1
    pct = get_column_letter(pct_col)
    last_data = ws.max_row
    header = ws.cell(row=1, column=pct_col, value="delta_percent")
    header.fill = ws["A1"].fill.copy()
    header.font = ws["A1"].font.copy()
    for row in range(2, last_data + 1):
        cell = ws.cell(row=row, column=pct_col)
        cell.value = (f'=IF(AND(ISNUMBER({base}{row}),ISNUMBER({post}{row}),{base}{row}<>0),'
                      f'ABS({post}{row}-{base}{row})/ABS({base}{row})*100,"")')
        cell.number_format = "0.0000"
    label_col, value_col = pct_col + 2, pct_col + 3
    label = ws.cell(row=1, column=label_col, value="Threshold [%]")
    label.font = Font(bold=True)
    value = ws.cell(row=1, column=value_col, value=threshold)
    value.font = Font(bold=True, color="0000FF")
    value.number_format = "0.0000"
    ws.cell(row=2, column=label_col, value="Yellow: delta_percent <= threshold").fill = YELLOW
    ws.cell(row=3, column=label_col, value="Red: delta_percent > threshold or no base").fill = RED
    ws.column_dimensions[pct].width = 14
    ws.column_dimensions[get_column_letter(label_col)].width = 42
    ws.column_dimensions[get_column_letter(value_col)].width = 12
    if last_data >= 2:
        first_col = get_column_letter(1)
        area = f"{first_col}2:{pct}{last_data}"
        t = f"${get_column_letter(value_col)}$1"
        ws.conditional_formatting.add(area, FormulaRule(
            formula=[f'AND(${pct}2<>"",${pct}2<={t})'], fill=YELLOW, stopIfTrue=True))
        ws.conditional_formatting.add(area, FormulaRule(
            formula=[f'OR(${pct}2="",${pct}2>{t})'], fill=RED, stopIfTrue=True))
    ws.auto_filter.ref = f"A1:{pct}{max(last_data, 1)}"
