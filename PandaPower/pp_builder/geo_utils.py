# pp_builder/geo_utils.py
"""
Coordinate utilities for the network builder.

  - EOV (Hungarian national grid, EPSG:23700) → WGS84 (lat/lon) conversion
  - Bus coordinate lookup from the Geo_Coordinates_busbars sheet
  - Line geometry loader from EOV point CSV files
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd




# ── EOV → WGS84 ──────────────────────────────────────────────────────────────
# Projection parameters for the Hungarian EOV (EPSG:23700)
# based on the HD72 / EOV definition.

_EOV_FALSE_EASTING  = 650_000.0    # m
_EOV_FALSE_NORTHING = 200_000.0    # m
_EOV_SCALE          = 0.99993      # central meridian scale
_EOV_LAT0_DEG       = 47.14439372222222   # origin latitude  (47° 08' 39.8174")
_EOV_LON0_DEG       = 19.04857177777778   # central meridian (19° 02' 54.8584")

# Krassovsky ellipsoid (used by HD72)
_A  = 6_378_245.0      # semi-major axis [m]
_F  = 1.0 / 298.3      # flattening
_B  = _A * (1.0 - _F)
_E2 = 1.0 - (_B / _A) ** 2
_E  = math.sqrt(_E2)


def _meridian_arc(lat_rad: float) -> float:
    """Length of meridian arc from equator to *lat_rad* on Krassovsky ellipsoid."""
    e2 = _E2
    e4 = e2 * e2
    e6 = e4 * e2
    return _A * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat_rad)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat_rad)
        - (35 * e6 / 3072) * math.sin(6 * lat_rad)
    )


def eov_to_wgs84(eov_y: float, eov_x: float) -> tuple[float, float]:
    """
    Convert EOV (EPSG:23700) coordinates to WGS84 lat/lon (degrees).

    Parameters
    ----------
    eov_y : float   Easting  (the column labelled 'y' in the CSV, ~ 572000 … 640000)
    eov_x : float   Northing (the column labelled 'x' in the CSV, ~ 196000 … 280000)

    Returns
    -------
    (latitude_deg, longitude_deg)

    Note: The CSV uses (x, y) but in EOV terminology x=Northing, y=Easting.
    In the CSV file the columns are named 'x' and 'y' where x ≈ 572000 (Easting)
    and y ≈ 196000 (Northing). We re-map to EOV convention inside this function.
    Pass the CSV columns as (csv_x → eov_y,  csv_y → eov_x).
    """
    lat0 = math.radians(_EOV_LAT0_DEG)
    lon0 = math.radians(_EOV_LON0_DEG)

    # Remove false origin
    E = eov_y - _EOV_FALSE_EASTING
    N = eov_x - _EOV_FALSE_NORTHING

    # Footprint latitude (iterative)
    M0 = _meridian_arc(lat0)
    M  = M0 + N / _EOV_SCALE
    mu = M / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))

    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    lat_fp = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )

    sin_fp  = math.sin(lat_fp)
    cos_fp  = math.cos(lat_fp)
    tan_fp  = math.tan(lat_fp)
    N_fp    = _A / math.sqrt(1 - _E2 * sin_fp ** 2)
    R_fp    = _A * (1 - _E2) / (1 - _E2 * sin_fp ** 2) ** 1.5
    D       = E / (N_fp * _EOV_SCALE)
    C_fp    = _E2 / (1 - _E2) * cos_fp ** 2
    T_fp    = tan_fp ** 2

    lat = lat_fp - (N_fp * tan_fp / R_fp) * (
        D ** 2 / 2
        - (5 + 3 * T_fp + 10 * C_fp - 4 * C_fp ** 2 - 9 * _E2 / (1 - _E2)) * D ** 4 / 24
        + (61 + 90 * T_fp + 298 * C_fp + 45 * T_fp ** 2
           - 252 * _E2 / (1 - _E2) - 3 * C_fp ** 2) * D ** 6 / 720
    )
    lon = lon0 + (
        D
        - (1 + 2 * T_fp + C_fp) * D ** 3 / 6
        + (5 - 2 * C_fp + 28 * T_fp - 3 * C_fp ** 2 + 8 * _E2 / (1 - _E2)
           + 24 * T_fp ** 2) * D ** 5 / 120
    ) / cos_fp

    # HD72 → WGS84 3-parameter Helmert shift (approximate, ~1–2 m accuracy)
    lat_deg = math.degrees(lat) - 0.000068
    lon_deg = math.degrees(lon) + 0.000118
    return lat_deg, lon_deg


# ── Bus coordinate lookup ─────────────────────────────────────────────────────

def load_bus_geodata(geo_xlsx: str | Path) -> dict[str, tuple[float, float]]:
    """
    Load busbar coordinates from the Excel file.

    Returns
    -------
    dict[str, (latitude, longitude)]
        Key is the busbar name exactly as it appears in the file.
    """
    import zipfile
    path = Path(geo_xlsx)
    
    with open(path, "rb") as f:
        header = f.read(8)

    if header[:2] == b"PK":
        # Real xlsx (ZIP-based)
        df = pd.read_excel(path, dtype=str, engine="openpyxl")
    elif header[:8] in (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",):
        # Old binary .xls
        df = pd.read_excel(path, dtype=str, engine="xlrd")
    else:
        # Plaintext: CSV or TSV
        sep = "," if b"," in header else "\t"
        df = pd.read_csv(path, dtype=str, sep=sep)
    df.columns = [c.strip() for c in df.columns]

    coord_map: dict[str, tuple[float, float]] = {}
    for _, row in df.iterrows():
        name = str(row.get("Busbar_Name", "")).strip()
        try:
            lat = float(str(row.get("Latitude",  "")).replace(",", "."))
            lon = float(str(row.get("Longitude", "")).replace(",", "."))
        except (ValueError, TypeError):
            continue
        if name:
            coord_map[name] = (lat, lon)

    print(f"[geo_utils]  Loaded {len(coord_map)} busbar coordinates.")
    return coord_map


# ── Line geometry loader ───────────────────────────────────────────────────────

def load_line_geodata_from_eov(
    csv_path: str | Path,
    name_col: str = "name",
    x_col:    str = "x",      # EOV Easting  in the CSV  (~572000)
    y_col:    str = "y",      # EOV Northing in the CSV  (~196000)
) -> dict[str, list[tuple[float, float]]]:
    """
    Load line route points from an EOV CSV and convert to WGS84.

    The CSV must contain at minimum: name, x (Easting), y (Northing).
    Rows are assumed to be in vertex order (sorted by vertex_ind if present).

    Returns
    -------
    dict[str, list[(lat, lon)]]
        One list of (lat, lon) waypoints per line name.
    """
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]

    if "vertex_ind" in df.columns:
        df = df.sort_values([name_col, "vertex_ind"])

    geo: dict[str, list[tuple[float, float]]] = {}
    for name, group in df.groupby(name_col, sort=False):
        points = []
        for _, row in group.iterrows():
            try:
                eov_e = float(row[x_col])   # CSV 'x' column = EOV Easting
                eov_n = float(row[y_col])   # CSV 'y' column = EOV Northing
            except (ValueError, TypeError, KeyError):
                continue
            lat, lon = eov_to_wgs84(eov_e, eov_n)
            points.append((lat, lon))
        if points:
            geo[str(name)] = points

    print(f"[geo_utils]  Loaded {len(geo)} line route(s) from EOV CSV.")
    return geo