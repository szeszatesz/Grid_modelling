from __future__ import annotations
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Hashable
import pandas as pd

Selector = Hashable | Iterable[Hashable] | Mapping[str, Any] | Callable[[pd.Series], bool] | None

def _table(net, element_type: str) -> pd.DataFrame:
    if element_type not in net or not isinstance(net[element_type], pd.DataFrame):
        available = sorted(k for k, v in net.items() if isinstance(v, pd.DataFrame))
        raise KeyError(f"Unknown element table {element_type!r}. Available tables: {available}")
    return net[element_type]

def select_elements(net, element_type: str, selector: Selector = None) -> list[Hashable]:
    """Select rows by index, name, iterable, mapping, pandas query, or predicate.

    Examples: ``3``, ``"LINE_A"``, ``[1, 4]``, ``{"vn_kv": 132}``,
    ``"vn_kv >= 220 and in_service"``, or ``lambda row: row.name.startswith('A')``.
    ``None`` selects every row.
    """
    df = _table(net, element_type)
    if selector is None:
        return df.index.tolist()
    if callable(selector):
        return df.index[df.apply(selector, axis=1).astype(bool)].tolist()
    if isinstance(selector, Mapping):
        mask = pd.Series(True, index=df.index)
        for column, expected in selector.items():
            if column not in df.columns:
                raise KeyError(f"{element_type}.{column} does not exist")
            if callable(expected):
                mask &= df[column].map(expected).astype(bool)
            elif isinstance(expected, (set, list, tuple, pd.Index)):
                mask &= df[column].isin(expected)
            else:
                mask &= df[column].eq(expected)
        return df.index[mask].tolist()
    if isinstance(selector, str):
        if "name" in df.columns:
            exact = df.index[df["name"].astype(str).str.strip().eq(selector.strip())].tolist()
            if exact:
                return exact
        try:
            return df.query(selector, engine="python").index.tolist()
        except Exception as exc:
            raise KeyError(f"No {element_type} named {selector!r}; not a valid query either") from exc
    if isinstance(selector, Iterable) and not isinstance(selector, (bytes, bytearray)):
        selected: list[Hashable] = []
        for item in selector:
            selected.extend(select_elements(net, element_type, item))
        return list(dict.fromkeys(selected))
    if selector in df.index:
        return [selector]
    raise KeyError(f"Index {selector!r} not found in net.{element_type}")

def resolve_one(net, element_type: str, selector: Selector) -> Hashable:
    selected = select_elements(net, element_type, selector)
    if len(selected) != 1:
        raise ValueError(f"Expected exactly one {element_type}, selected {len(selected)}: {selected}")
    return selected[0]
