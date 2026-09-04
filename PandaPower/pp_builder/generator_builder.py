"""
generator_builder.py — creates pandapower generators / static generators.

Generator type mapping
----------------------
* Voltage-controlled (PV node): pp.create_gen   → for synchronous machines,
  hydro, gas — any technology that regulates terminal voltage.
* PQ (constant P+Q injection): pp.create_sgen  → wind, solar, battery —
  converter-connected, no voltage control.

Data sources
------------
* Dispatchable / conventional generators (gas, hydro, coal, nuclear, etc.)
  are still loaded from the MAVIR "Generators" sheet via build_generators().
  Every gen/sgen created from this sheet is tagged source="MAVIR" (a
  "source" column, mirroring the "source" tag used for MEKH/HMKE-created
  elements), so all three origins can be distinguished downstream by a
  single column instead of having to infer origin from technology/name.
* Weather-dependent generation — solar, wind and battery storage — is
  loaded from the MEKH connection-capacity list ("MEKH production projects"
  workbook) via build_mekh_generators(), NOT from the MAVIR sheet.
  Solar/wind/battery rows found in the MAVIR sheet are skipped with a
  warning so the two sources are never double-counted. Matching is done
  against the RAW MAVIR "Technológia" label set
  (_MAVIR_RAW_SOLAR_WIND_BATTERY_LABELS — e.g. 'SOLARPHOTOVO',
  'ROOFTOPPV', 'WINDONSHORE', 'BATTERYSTRG'), NOT against the internal
  TECHNOLOGY_SOLAR/WIND/BATTERY constants (those are only ever assigned
  to elements created by the MEKH/HMKE builders and never appear as raw
  MAVIR sheet values — comparing against them silently fails to match).
* Among MEKH-sourced renewables/battery units, any unit with installed
  capacity above 5 MW is modelled as voltage-controllable (pp.create_gen,
  PV node) rather than a plain PQ injection, with reactive power limits
  of +/- 30% of its rated (sn_mva) capacity, per grid-code requirements.
  Units at or below 5 MW remain PQ (pp.create_sgen).
* MEKH connection points not yet in service have a future connection date
  in column J ("... legkorábbi igénybevételi időpontja" — earliest date
  the grid connection may be utilized). Rows whose column-J date falls
  AFTER 2035-12-31 are treated as too speculative/far out to model and are
  skipped entirely (both the generation and any co-located battery
  capacity on that row) — see "Future connection date filtering" below.
  Rows with no date in column J (already in service, or no committed
  date) are NOT skipped by this rule.
* Small-scale household PV ("HMKE" — Háztartási Méretű Kiserőmű) is loaded
  separately via build_hmke_generators() from the HMKE municipal
  statistics workbook, aggregated per substation. These are always
  modelled as PQ (pp.create_sgen) — HMKE units are far below any
  voltage-control threshold and never provide reactive support.
* Large cities (Budapest, Debrecen, etc.) accumulate HMKE PV capacity far
  beyond what a single distribution substation could plausibly host.
  Municipalities whose total 2025 HMKE capacity exceeds
  _HMKE_MULTI_SUBSTATION_THRESHOLD_MW are therefore split across ALL
  substations found within a capacity-scaled search radius of the
  geocoded city center, weighted by inverse distance — see
  "Multi-substation distribution for large cities" below.

MEKH sheet column mapping (0-indexed / Excel letter)
-----------------------------------------------------
  A (col 0) — "Település megnevezése, amelyhez a csatlakozási pont
               tartozik" → municipality name, used as a geocoding
               fallback when the substation name (col B) can't be
               matched directly against bus_map.
  B (col 1) — "Csatlakozási pontot ellátó alállomás neve"
               → connecting substation name, matched against bus_map by
               comparing the leading substation-code prefix of both
               names (bus names look like 'ALBF 1      220.00').
  E (col 4) — "Csatlakozási ponton rendelkezésre álló betáplálási
               kapacitás (MVA)" → generation (feed-in) capacity, used as
               sn_mva / p_mw for solar & wind rows. Values look like
               "4,32 MVA" (Hungarian decimal comma + unit suffix).
  F (col 5) — "... villamosenergia-tároló és segédüzem részére ...
               vételezési kapacitás (MVA)" → storage capacity, used as
               sn_mva / p_mw for battery rows.
  G (col 6) — "Csatlakozási feszültségszint" → voltage level text,
               "középfeszültségű"/"középfeszültség" (MV) or
               "nagyfeszültségű"/"nagyfeszültség" (HV) or
               "kisfeszültség" (LV). Used both for MV/HV bus disambiguation
               (in both the prefix-match AND geocoding fallback paths) and
               stored on the sgen/gen row for reference/QA.
  H (col 7) — "... erőmű tekintetében az energiahordozó" → primary
               technology: "nap" = solar, "szél" = wind,
               "nem értelmezhető" = not applicable (row is battery-only).
  I (col 8) — "... tároló tekintetében a technológia" → storage
               technology: "akkumulátor" = battery, "egyéb"/others
               currently mapped to generic "storage", "nem értelmezhető"
               = no storage at this connection point.
  J (col 9) — "Üzembe helyezés előtt álló csatlakozási pont esetén a
               hálózati csatlakozás legkorábbi igénybevételi időpontja"
               → earliest date the connection may be commissioned, for
               points not yet in service. NaN/blank for points already in
               service (or without a committed date). Rows with a date
               after _MEKH_MAX_CONNECTION_YEAR (2035) are skipped — see
               "Future connection date filtering" below.

A single MEKH row can contain BOTH a generation capacity (col E, solar or
wind) AND a storage capacity (col F, battery) at the same connection point.
Such rows are split into two separate sgen/gen elements — one for the
generation technology, one for the battery — each keeping the shared
substation/voltage metadata.

Future connection date filtering (MEKH)
------------------------------------------
Column J gives the earliest date a not-yet-commissioned connection point
may be utilized. Many entries carry a placeholder-like far-future date
(e.g. 2050-01-01) representing long-term/speculative reservations rather
than concrete near-term projects. Modelling these alongside firm,
near-term capacity would overstate expected renewable/battery buildout.

_parse_mekh_connection_date() parses column J (already a datetime in the
source workbook, but parsed defensively in case it's read as text).
Rows whose parsed date falls after 2035-12-31
(_MEKH_MAX_CONNECTION_YEAR) are skipped entirely, for BOTH the
generation (col E) and battery (col F) elements on that row, since a
future connection point that won't be built until after 2035 has no firm
grid capacity to model. Rows with no date (blank/NaN) are NOT
skipped by this rule — an empty column J means either the point is
already in service, or has no committed future date, neither of which
implies "distant future" on its own.

Substation matching strategy (MEKH and HMKE)
----------------------------------------------
Both the MEKH list and the HMKE municipal PV statistics only give a
municipality/settlement name — not always a precise substation name — so
resolution to a pandapower bus follows the same two-stage strategy:
  1. Prefix match (MEKH only, since it has an explicit substation column) —
     compare the first 5 characters of the MEKH substation name against
     the leading substation-code prefix of each bus name (e.g. "ALBF" in
     'ALBF 1      220.00'), disambiguated by voltage level where the
     source data specifies one (col G in MEKH; HMKE has no voltage column
     so this stage is skipped for HMKE — see below).
  2. Geocoding fallback (MEKH's fallback, and HMKE's only method) — the
     place name is geocoded via GeoPy/Nominatim, and the geographically
     nearest bus is chosen using each bus's stored geo-coordinates
     (net.bus["geo"], a GeoJSON string parsed with json.loads()). For MEKH
     this is filtered to the MV/HV band implied by col G; HMKE household
     PV has no voltage-level field in the source sheet, so it is always
     connected to the nearest LV/MV distribution bus (the lowest-voltage
     bus at/near the matched location) since these are small,
     low-voltage-connected installations.

Multi-substation distribution for large cities (HMKE)
--------------------------------------------------------
A single municipality name (e.g. "Budapest") maps to one geocoded point,
but large cities are served by many distribution substations spread over
a wide area — dumping all of a city's HMKE capacity onto whichever single
substation happens to be nearest the city's geocoded centroid is
physically unrealistic (e.g. Budapest ≈260 MW, Debrecen ≈68 MW in the
2025 data) and would create an artificial concentration of injected power.

To address this, build_hmke_generators() applies proportional
multi-substation distribution to any municipality whose total 2025 HMKE
capacity exceeds _HMKE_MULTI_SUBSTATION_THRESHOLD_MW (default 5 MW):
  1. A search radius is derived from installed capacity via
     _city_search_radius_km() — larger cities get a wider search radius
     (capped at _HMKE_MAX_SEARCH_RADIUS_KM), since they physically cover
     more ground and are served by more substations.
  2. All distinct substations (grouped by the same _BUS_PREFIX_LEN-char
     prefix used elsewhere) with at least one bus within that radius of
     the geocoded city center are collected via
     _BusGeoSpatialIndex.within_radius_km(), picking each substation's
     lowest-voltage bus (household PV connects at distribution level).
  3. The municipality's total capacity is split across these substations
     using inverse-distance weighting (closer substations receive a
     proportionally larger share), each becoming its own sgen.
  4. If only one substation is found within the radius (small towns,
     even if they exceed the MW threshold), this degrades gracefully to
     the normal single-substation behavior.
This is a heuristic — it has no ground truth for how HMKE capacity is
actually distributed within a city, since the source data is only
available at municipality granularity. It merely avoids the more
obviously wrong assumption that an entire city's rooftop solar sits on
one single feeder.

Geocoding cache (persistent, on-disk)
--------------------------------------
Geocoding a municipality name via Nominatim requires a network round-trip
and is rate-limited (Nominatim's usage policy caps requests at ~1/sec and
will start silently dropping/blocking a client that hammers it too fast
or without a distinct User-Agent). To avoid re-geocoding the same towns on
every run — and to keep working even if Nominatim is temporarily
unreachable, blocked, or rate-limiting the requests — geocoding results
are persisted to a JSON file on disk (default:
"municipality_geocode_cache.json", override via geocode_cache_path). Both
build_mekh_generators() and build_hmke_generators() share the same
on-disk cache format (and, by default, the same file) since both geocode
Hungarian settlement names — so a town geocoded once while processing the
MEKH list is instantly reused when processing the HMKE list, and vice
versa.

The cache is loaded at the start of each build_*_generators() call and
merged into the in-memory cache used during that run; any newly geocoded
municipalities are written back to disk both incrementally (so a crash
mid-run doesn't lose already-geocoded results) and at the end of the run.
A cached `None` result (a municipality that failed to geocode) is also
persisted, so it is not silently retried forever — delete the
corresponding entry from the cache file if you want it retried.

Nearest-bus lookup performance
---------------------------------
Resolving thousands of MEKH/HMKE rows against thousands of buses via a
per-row linear scan with geopy.distance.geodesic() is extremely slow
(O(rows * buses) geodesic calls). Both geo-fallback paths instead use a
_BusGeoSpatialIndex — a sklearn.neighbors.BallTree (haversine metric)
built once per run — giving O(log n) nearest-neighbor and radius queries.
"""
from __future__ import annotations

import json
import os
import re
import time
import numpy as np
import pandas as pd
import pandapower as pp
from sklearn.neighbors import BallTree

from .config import (
    SHEET_GENERATORS,
    DEFAULT_GEN_SCALING,
    TECH_SCALING_DEFAULTS,
    RATING_SEASON_SUMMER,
    TECHNOLOGY_WIND, TECHNOLOGY_SOLAR, TECHNOLOGY_BATTERY, VOLTAGE_CONTROL_TECHS
)
from .excel_reader import _float, _bool, _str
from .busbar_builder import voltage_reader

# Technologies that should be modelled as PQ static generators
_PQ_TECHNOLOGIES = {TECHNOLOGY_WIND, TECHNOLOGY_SOLAR, TECHNOLOGY_BATTERY}

# These are now sourced exclusively from the MEKH list, never from MAVIR.
_MEKH_ONLY_TECHNOLOGIES = {TECHNOLOGY_WIND, TECHNOLOGY_SOLAR, TECHNOLOGY_BATTERY}

# The MAVIR "Generators" sheet's raw "Technológia" column uses a DIFFERENT
# label set than the internal TECHNOLOGY_SOLAR/WIND/BATTERY constants above
# (those are only ever assigned to MEKH-sourced elements created by this
# module). Comparing raw MAVIR labels directly against the internal
# constants silently fails to match, letting solar/wind/battery rows slip
# through from MAVIR uncaught — this is the actual raw label set observed
# in the MAVIR sheet and must be kept in sync with it. Matching is
# case-insensitive and whitespace-trimmed.
_MAVIR_RAW_SOLAR_WIND_BATTERY_LABELS = {
    "SOLARPHOTOVO",   # utility-scale / ground-mounted solar PV
    "ROOFTOPPV",      # rooftop solar PV
    "WINDONSHORE",    # onshore wind
    "WINDOFFSHORE",   # offshore wind (not expected in HU data, kept for safety)
    "BATTERYSTRG",    # battery storage
}


def _is_mavir_solar_wind_battery(raw_technology: str) -> bool:
    """True if a raw MAVIR 'Technológia' value denotes solar, wind or
    battery generation (i.e. should be skipped from build_generators()
    since it is sourced from the MEKH list instead)."""
    return raw_technology.strip().upper() in _MAVIR_RAW_SOLAR_WIND_BATTERY_LABELS


# Source tag stored on every gen/sgen row created by build_generators(),
# mirroring the "source" tag used for MEKH- and HMKE-created elements so
# the origin of any generator/sgen can always be read from one column.
_MAVIR_SOURCE_TAG = "MAVIR"


# MEKH sheet — column positions (0-indexed) per the header row (row 2 in
# Excel, i.e. header=1 when read with pandas).
_MEKH_COL_MUNICIPALITY    = 0  # A
_MEKH_COL_SUBSTATION      = 1  # B
_MEKH_COL_NAME_OWNER      = 2  # C — owner / applicant name, used for element naming
_MEKH_COL_CAPACITY_GEN    = 4  # E — generation feed-in capacity (MVA)
_MEKH_COL_CAPACITY_STORE  = 5  # F — storage capacity (MVA)
_MEKH_COL_VOLTAGE_LEVEL   = 6  # G
_MEKH_COL_ENERGY_SOURCE   = 7  # H — "nap" / "szél" / "nem értelmezhető"
_MEKH_COL_STORAGE_TECH    = 8  # I — "akkumulátor" / "egyéb" / "nem értelmezhető"
_MEKH_COL_CONNECTION_DATE = 9  # J — earliest future grid-connection utilization date

_NOT_APPLICABLE = "nem értelmezhető"

_ENERGY_SOURCE_MAP = {
    "nap": TECHNOLOGY_SOLAR,
    "szél": TECHNOLOGY_WIND,
}

_STORAGE_TECH_MAP = {
    "akkumulátor": TECHNOLOGY_BATTERY,
}

# Rows in the MEKH sheet whose column-J connection date (col J, "earliest
# utilization date for a not-yet-commissioned connection point") falls
# after December 31 of this year are skipped entirely (too far out /
# speculative to model) — see "Future connection date filtering" in the
# module docstring. Rows with no date in column J are NOT affected by
# this rule.
_MEKH_MAX_CONNECTION_YEAR = 2035


def _parse_mekh_connection_date(value) -> pd.Timestamp | None:
    """Parse the MEKH column-J future-connection-date value into a
    pandas Timestamp, defensively handling it already being a
    datetime/Timestamp (typical when read via pandas.read_excel with a
    real Excel date cell) as well as a string fallback. Returns None for
    blank/NaN/unparseable values."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value
    try:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=False)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed


def _is_mekh_connection_too_far_future(connection_date: pd.Timestamp | None) -> bool:
    """True if a parsed MEKH column-J connection date falls after
    _MEKH_MAX_CONNECTION_YEAR (2035) — such rows are skipped entirely.
    A missing date (None) is NOT considered too-far-future — it means
    the point is already in service or has no committed date."""
    if connection_date is None:
        return False
    return connection_date.year > _MEKH_MAX_CONNECTION_YEAR


# Number of leading characters used to match a MEKH substation name against
# the pandapower bus name prefix (bus names look like
# "ALBF 1      220.00" — a 4-letter substation code followed by bus number
# and rated voltage; comparing the first 5 characters is enough to isolate
# the substation code regardless of bus numbering). Also used to group
# buses into distinct substations for HMKE multi-substation distribution.
_BUS_PREFIX_LEN = 5

# Voltage bands (kV) used to disambiguate which bus at a multi-voltage
# substation a MEKH connection point belongs to, based on column G text.
_MV_MAX_KV = 35.0   # "középfeszültségű" / "középfeszültség" -> <= 35 kV
# anything above _MV_MAX_KV is treated as "nagyfeszültségű" / "nagyfeszültség"

# Threshold above which a MEKH-sourced renewable/battery unit is treated as
# voltage-controllable (PV node, pp.create_gen) rather than a plain PQ
# injection (pp.create_sgen). Per MEKH/MAVIR grid-code requirements, units
# above this size must be able to provide reactive power support.
_MEKH_VOLTAGE_CONTROL_THRESHOLD_MVA = 5.0

# Reactive power capability required from voltage-controllable MEKH units:
# +/- 30% of installed (rated) capacity.
_MEKH_REACTIVE_CAPABILITY_RATIO = 0.30

# Country code hint used to speed up / disambiguate Nominatim geocoding of
# Hungarian settlement names (all MEKH/HMKE connection points are in HU).
_GEOCODE_COUNTRY_HINT = ", Hungary"

# Default path for the persistent, on-disk geocode cache (municipality
# name -> [lat, lon] or null). Stored next to this module unless overridden.
# Shared by build_mekh_generators() and build_hmke_generators().
_DEFAULT_GEOCODE_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "municipality_geocode_cache.json"
)

# Minimum delay (seconds) between live Nominatim requests, per its usage
# policy (max ~1 request/sec). Only applied to cache misses.
_GEOCODE_MIN_DELAY_SEC = 1.0

# ---------------------------------------------------------------------------
# HMKE (household-size PV) sheet mapping
# ---------------------------------------------------------------------------
SHEET_HMKE = "HMKE PV települési bontásban"

# The sheet has two header rows (year, then metric name) followed by data.
# After reading with header=None, data rows start here (0-indexed).
_HMKE_DATA_START_ROW = 5

_HMKE_COL_MUNICIPALITY = 0  # "Település neve"
# Columns 1-2 = 2024 (BT kW, darabszám); columns 3-4 = 2025 (BT kW, darabszám)
_HMKE_COL_BT_KW_2025 = 3    # "HMKE PV BT (kW)" for 2025
_HMKE_COL_COUNT_2025 = 4    # "HMKE PV darabszám (db)" for 2025

_HMKE_TECHNOLOGY = TECHNOLOGY_SOLAR
_KW_TO_MW = 1.0 / 1000.0

# Municipalities whose total 2025 HMKE capacity exceeds this threshold are
# split across multiple substations instead of connected to a single
# nearest bus (see "Multi-substation distribution for large cities" in the
# module docstring).
_HMKE_MULTI_SUBSTATION_THRESHOLD_MW = 5.0

# Search radius (km) used to find candidate substations for large-city
# HMKE distribution, as a function of installed capacity:
#   radius_km = min(_HMKE_MAX_SEARCH_RADIUS_KM,
#                    _HMKE_BASE_SEARCH_RADIUS_KM + _HMKE_RADIUS_PER_SQRT_MW * sqrt(capacity_mw))
# This scales gently with city size (sqrt, not linear) so a handful of
# very large cities (Budapest ~260 MW) get a wide radius while
# mid-sized towns just over the threshold don't get an excessively large
# one.
_HMKE_BASE_SEARCH_RADIUS_KM = 3.0
_HMKE_RADIUS_PER_SQRT_MW = 1.2
_HMKE_MAX_SEARCH_RADIUS_KM = 25.0

# Small epsilon (km) added to distances before inverse-distance weighting,
# to avoid division by zero for a substation essentially at the city center.
_HMKE_DISTANCE_WEIGHT_EPSILON_KM = 0.5


def _get_scale(row: pd.Series, technology: str, season: str) -> float:
    """Resolve scaling factor: Excel override → config default → 1.0."""
    explicit = row.get("ScalingFactor", None)
    if explicit is not None and not pd.isna(explicit):
        return float(explicit)
    return TECH_SCALING_DEFAULTS.get(technology, {}).get(season, DEFAULT_GEN_SCALING)


def _parse_mva(value) -> float | None:
    """Parse MEKH capacity strings like '4,32 MVA' or '0 MVA' into float MVA."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def _is_mv(voltage_level_text: str) -> bool:
    """True if the MEKH voltage-level text (col G) denotes medium voltage
    ("középfeszültségű"/"középfeszültség"), False if high voltage
    ("nagyfeszültségű"/"nagyfeszültség")."""
    return "közép" in voltage_level_text.lower()


def _extract_bus_kv(bus_name: str) -> float | None:
    """Extract the rated voltage (kV) trailing a pandapower bus name,
    e.g. 'ALBF 1      220.00' -> 220.0."""
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*$", bus_name.strip())
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _bus_substation_prefix(bus_name: str) -> str:
    """First _BUS_PREFIX_LEN characters of a bus name, used to group buses
    belonging to the same substation regardless of voltage level/number
    suffix, e.g. 'ALBF 1      220.00' and 'ALBF B1     120.00' -> 'ALBF'."""
    return bus_name[:_BUS_PREFIX_LEN].strip().upper()


def _build_bus_prefix_index(bus_map: dict[str, int]) -> dict[str, list[tuple[str, float, int]]]:
    """Group bus_map entries by their leading substation-code prefix.

    Returns
    -------
    dict[str, list[(bus_name, kv, bus_idx)]]
        prefix -> candidate buses at that substation, each with parsed kV.
    """
    index: dict[str, list[tuple[str, float, int]]] = {}
    for bus_name, bus_idx in bus_map.items():
        prefix = _bus_substation_prefix(bus_name)
        if not prefix:
            continue
        kv = _extract_bus_kv(bus_name)
        if kv is None:
            continue
        index.setdefault(prefix, []).append((bus_name, kv, bus_idx))
    return index


def _resolve_mekh_bus_by_prefix(
    prefix_index: dict[str, list[tuple[str, float, int]]],
    mekh_substation_name: str,
    voltage_level_text: str,
) -> tuple[str, int] | None:
    """Resolve a MEKH substation name + voltage-level text to a bus using
    prefix matching. Returns (bus_name, bus_idx) or None if no candidate
    substation prefix matches at all.

    If several buses share the matched substation prefix, the one whose
    rated voltage falls in the MV/HV band implied by voltage_level_text is
    preferred; the highest-voltage bus in that band is chosen if there is
    more than one. Falls back to the highest-voltage candidate overall if
    no band match exists.
    """
    prefix = mekh_substation_name[:_BUS_PREFIX_LEN].strip().upper()
    candidates = prefix_index.get(prefix)
    if not candidates:
        return None

    is_mv = _is_mv(voltage_level_text)
    band_candidates = [c for c in candidates if (c[1] <= _MV_MAX_KV) == is_mv]

    pool = band_candidates if band_candidates else candidates
    bus_name, _kv, bus_idx = max(pool, key=lambda c: c[1])
    return bus_name, bus_idx


def _parse_bus_geo(geo_value) -> tuple[float, float] | None:
    """Parse a bus's geo-coordinates, stored as a GeoJSON string, e.g.
    '{"type":"Point","coordinates":[lon,lat]}'. Returns (lat, lon) or
    None if unavailable/unparseable."""
    if geo_value is None or (isinstance(geo_value, float) and pd.isna(geo_value)):
        return None
    try:
        geo = json.loads(geo_value) if isinstance(geo_value, str) else geo_value
        lon, lat = geo["coordinates"][0], geo["coordinates"][1]
        return float(lat), float(lon)
    except (TypeError, ValueError, KeyError, IndexError, json.JSONDecodeError):
        return None


def _build_bus_coord_index(
    net: pp.pandapowerNet,
    bus_map: dict[str, int],
) -> dict[int, tuple[float, float, float | None]]:
    """Build {bus_idx: (lat, lon, kv)} for every bus with parseable geo-data
    in net.bus['geo'] (GeoJSON string per row). kv is the bus's rated
    voltage extracted from its name (may be None if unparseable), used
    later to filter geo-candidates by MV/HV band."""
    coord_index: dict[int, tuple[float, float, float | None]] = {}
    if "geo" not in net.bus.columns:
        return coord_index
    for bus_name, bus_idx in bus_map.items():
        if bus_idx not in net.bus.index:
            continue
        coords = _parse_bus_geo(net.bus.at[bus_idx, "geo"])
        if coords is not None:
            kv = _extract_bus_kv(bus_name)
            coord_index[bus_idx] = (coords[0], coords[1], kv)
    return coord_index


_EARTH_RADIUS_KM = 6371.0088


class _BusGeoSpatialIndex:
    """
    Spatial nearest-neighbor index over bus geo-coordinates, built once
    per call and reused for every row instead of doing a linear
    geopy.distance.geodesic() scan over all buses per row (which is the
    main cost driver when resolving thousands of MEKH/HMKE rows against
    thousands of buses — O(rows * buses) geodesic calls).

    Uses sklearn.neighbors.BallTree with the haversine metric, which
    needs coordinates in radians and returns distances in radians;
    converting to km via _EARTH_RADIUS_KM keeps results consistent with
    geopy-based distances (haversine, not geodesic/vincenty, but the
    difference is negligible — well under 0.5% — at this scale and
    irrelevant for choosing the nearest substation).

    Builds three trees lazily: "all" (every bus with geo-data), "mv"
    (rated voltage <= _MV_MAX_KV) and "hv" (rated voltage > _MV_MAX_KV),
    so voltage-band-restricted lookups (used by both MEKH and HMKE
    resolution) are also O(log n) instead of filtering a list per row.
    """

    def __init__(self, bus_coord_index: dict[int, tuple[float, float, float | None]]):
        self._bus_coord_index = bus_coord_index
        self._trees: dict[str, tuple[BallTree, list[int]] | None] = {}

    def _get_tree(self, band: str) -> tuple[BallTree, list[int]] | None:
        if band in self._trees:
            return self._trees[band]

        if band == "all":
            items = list(self._bus_coord_index.items())
        elif band == "mv":
            items = [(idx, c) for idx, c in self._bus_coord_index.items()
                     if c[2] is not None and c[2] <= _MV_MAX_KV]
        elif band == "hv":
            items = [(idx, c) for idx, c in self._bus_coord_index.items()
                     if c[2] is not None and c[2] > _MV_MAX_KV]
        else:
            raise ValueError(f"unknown band '{band}'")

        if not items:
            self._trees[band] = None
            return None

        bus_indices = [idx for idx, _c in items]
        coords_rad = np.radians([[c[0], c[1]] for _idx, c in items])
        tree = BallTree(coords_rad, metric="haversine")
        self._trees[band] = (tree, bus_indices)
        return self._trees[band]

    def nearest(self, lat: float, lon: float, band: str = "all") -> int | None:
        """Return the bus_idx nearest to (lat, lon), restricted to `band`
        ("all", "mv", "hv"), or None if no bus is available in that band."""
        entry = self._get_tree(band)
        if entry is None:
            return None
        tree, bus_indices = entry
        query = np.radians([[lat, lon]])
        _dist, ind = tree.query(query, k=1)
        return bus_indices[int(ind[0][0])]

    def nearest_with_distance_km(self, lat: float, lon: float, band: str = "all") -> tuple[int, float] | None:
        """Like nearest(), but also returns the distance in km."""
        entry = self._get_tree(band)
        if entry is None:
            return None
        tree, bus_indices = entry
        query = np.radians([[lat, lon]])
        dist, ind = tree.query(query, k=1)
        bus_idx = bus_indices[int(ind[0][0])]
        distance_km = float(dist[0][0]) * _EARTH_RADIUS_KM
        return bus_idx, distance_km

    def within_radius_km(self, lat: float, lon: float, radius_km: float, band: str = "all") -> list[tuple[int, float]]:
        """Return [(bus_idx, distance_km), ...] for all buses in `band`
        within radius_km of (lat, lon), sorted by distance ascending."""
        entry = self._get_tree(band)
        if entry is None:
            return []
        tree, bus_indices = entry
        query = np.radians([[lat, lon]])
        radius_rad = radius_km / _EARTH_RADIUS_KM
        ind, dist = tree.query_radius(query, r=radius_rad, return_distance=True, sort_results=True)
        return [(bus_indices[int(i)], float(d) * _EARTH_RADIUS_KM) for i, d in zip(ind[0], dist[0])]


def _load_geocode_cache(path: str) -> dict[str, tuple[float, float] | None]:
    """Load the persistent on-disk geocode cache. Returns an empty dict if
    the file doesn't exist or can't be parsed."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"[generator_builder]  WARNING — could not read geocode cache "
              f"at '{path}', starting with an empty cache.")
        return {}

    cache: dict[str, tuple[float, float] | None] = {}
    for municipality, coords in raw.items():
        cache[municipality] = tuple(coords) if coords is not None else None
    return cache


def _save_geocode_cache(path: str, cache: dict[str, tuple[float, float] | None]) -> None:
    """Persist the geocode cache to disk as JSON. Best-effort — a failure
    to write is logged but never raised, so it can't break the build."""
    try:
        serializable = {
            municipality: (list(coords) if coords is not None else None)
            for municipality, coords in cache.items()
        }
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except OSError as exc:
        print(f"[generator_builder]  WARNING — could not write geocode cache "
              f"to '{path}': {exc}")


def _geocode_municipality(
    municipality: str,
    geolocator,
    cache: dict[str, tuple[float, float] | None],
) -> tuple[float, float] | None:
    """Geocode a Hungarian municipality name to (lat, lon) using GeoPy,
    checking the (persistent) cache first. Only municipalities not already
    present in the cache trigger a live Nominatim request, throttled to
    respect its usage policy (~1 request/sec)."""
    if municipality in cache:
        return cache[municipality]

    coords = None
    try:
        time.sleep(_GEOCODE_MIN_DELAY_SEC)
        location = geolocator.geocode(municipality + _GEOCODE_COUNTRY_HINT)
        if location is not None:
            coords = (location.latitude, location.longitude)
    except Exception as exc:
        print(f"[generator_builder]  WARNING — geocoding '{municipality}' failed: {exc}")
        coords = None

    cache[municipality] = coords
    return coords


def _resolve_mekh_bus_by_geo(
    municipality: str,
    voltage_level_text: str,
    bus_map: dict[str, int],
    spatial_index: "_BusGeoSpatialIndex",
    geolocator,
    geocode_cache: dict[str, tuple[float, float] | None],
    idx_to_name: dict[int, str],
) -> tuple[str, int] | None:
    """Fallback resolution: geocode the MEKH municipality name (col A) and
    return the nearest bus by geographic distance, restricted to the
    MV/HV voltage band implied by voltage_level_text (col G) whenever
    possible.

    Uses a prebuilt _BusGeoSpatialIndex (BallTree, haversine metric) for
    O(log n) nearest-neighbor lookup instead of a linear geodesic scan
    over every bus, which is the dominant cost when resolving thousands
    of MEKH rows. Geocoding results are read from / written to
    geocode_cache (backed by an on-disk JSON file — see
    _load_geocode_cache/_save_geocode_cache), so repeated runs and
    repeated municipalities never re-hit Nominatim.
    """
    municipality_coords = _geocode_municipality(municipality, geolocator, geocode_cache)
    if municipality_coords is None:
        return None

    lat, lon = municipality_coords
    is_mv = _is_mv(voltage_level_text)
    band = "mv" if is_mv else "hv"

    best_bus_idx = spatial_index.nearest(lat, lon, band=band)
    # Fall back to any bus with geo-data if no in-band candidate exists.
    if best_bus_idx is None:
        best_bus_idx = spatial_index.nearest(lat, lon, band="all")

    if best_bus_idx is None:
        return None
    return idx_to_name[best_bus_idx], best_bus_idx


def _resolve_nearest_bus_by_geo(
    place_name: str,
    bus_map: dict[str, int],
    spatial_index: "_BusGeoSpatialIndex",
    geolocator,
    geocode_cache: dict[str, tuple[float, float] | None],
    idx_to_name: dict[int, str],
    bus_coord_index: dict[int, tuple[float, float, float | None]],
    prefer_lowest_voltage: bool = False,
) -> tuple[str, int] | None:
    """Generic geocode-and-snap-to-nearest-bus resolver, used by
    build_hmke_generators() for municipalities below the multi-substation
    threshold (no voltage-level column to filter on).

    Uses a prebuilt _BusGeoSpatialIndex (BallTree, haversine metric) for
    O(log n) nearest-neighbor lookup instead of a linear geodesic scan
    over every bus — the dominant cost when resolving thousands of HMKE
    rows against thousands of buses.

    If prefer_lowest_voltage is True, ties/near-ties are broken towards
    the lowest-voltage bus at the nearest location (appropriate for
    small household PV, which connects at LV/MV distribution level, not
    at transmission voltage): the single nearest bus is found first, then
    all buses within a small radius of that location are queried via
    within_radius_km() and the lowest-voltage one among them is chosen.
    If no voltage info is available, simply falls back to plain
    nearest-distance.
    """
    place_coords = _geocode_municipality(place_name, geolocator, geocode_cache)
    if place_coords is None:
        return None

    lat, lon = place_coords
    nearest = spatial_index.nearest_with_distance_km(lat, lon, band="all")
    if nearest is None:
        return None
    nearest_bus_idx, nearest_distance = nearest

    if not prefer_lowest_voltage:
        return idx_to_name[nearest_bus_idx], nearest_bus_idx

    # Consider buses at (essentially) the same location as the nearest hit
    # — i.e. the same substation — and pick the lowest voltage among them,
    # since household PV connects at distribution level.
    same_site_radius_km = 2.0
    same_site = spatial_index.within_radius_km(
        lat, lon, nearest_distance + same_site_radius_km, band="all"
    )
    same_site_with_kv = [
        (bus_idx, bus_coord_index[bus_idx][2])
        for bus_idx, _dist in same_site
        if bus_coord_index[bus_idx][2] is not None
    ]

    if same_site_with_kv:
        best_bus_idx, _kv = min(same_site_with_kv, key=lambda item: item[1])
    else:
        best_bus_idx = nearest_bus_idx

    return idx_to_name[best_bus_idx], best_bus_idx


def _city_search_radius_km(capacity_mw: float) -> float:
    """Derive a search radius (km) for large-city HMKE multi-substation
    distribution, scaling gently (sqrt) with installed capacity so that
    very large cities (e.g. Budapest, ~260 MW) get a wide radius while
    towns just over the threshold get a modest one. Capped at
    _HMKE_MAX_SEARCH_RADIUS_KM."""
    radius = _HMKE_BASE_SEARCH_RADIUS_KM + _HMKE_RADIUS_PER_SQRT_MW * (capacity_mw ** 0.5)
    return min(radius, _HMKE_MAX_SEARCH_RADIUS_KM)


def _find_city_substations(
    spatial_index: "_BusGeoSpatialIndex",
    bus_coord_index: dict[int, tuple[float, float, float | None]],
    idx_to_name: dict[int, str],
    lat: float,
    lon: float,
    radius_km: float,
) -> list[tuple[int, str, float]]:
    """
    Find distinct substations (grouped by _bus_substation_prefix) with at
    least one bus within radius_km of (lat, lon), suitable for
    distribution-level (household PV) connection.

    For each distinct substation found, the lowest-voltage bus at that
    substation (among those within radius) is selected as the connection
    point, matching the same "household PV connects at distribution
    level" reasoning as _resolve_nearest_bus_by_geo(prefer_lowest_voltage=True).

    Returns
    -------
    list[(bus_idx, bus_name, distance_km)]
        One entry per distinct substation, sorted by distance ascending.
    """
    nearby = spatial_index.within_radius_km(lat, lon, radius_km, band="all")
    if not nearby:
        return []

    best_per_substation: dict[str, tuple[int, str, float, float | None]] = {}
    for bus_idx, distance_km in nearby:
        bus_name = idx_to_name[bus_idx]
        prefix = _bus_substation_prefix(bus_name)
        kv = bus_coord_index[bus_idx][2]

        existing = best_per_substation.get(prefix)
        if existing is None:
            best_per_substation[prefix] = (bus_idx, bus_name, distance_km, kv)
            continue

        _ex_idx, _ex_name, _ex_dist, ex_kv = existing
        # Prefer the lowest-voltage bus at this substation; among equal
        # voltage (or both unknown), keep the closer one.
        if kv is not None and (ex_kv is None or kv < ex_kv):
            best_per_substation[prefix] = (bus_idx, bus_name, distance_km, kv)
        elif kv == ex_kv and distance_km < _ex_dist:
            best_per_substation[prefix] = (bus_idx, bus_name, distance_km, kv)

    result = [(idx, name, dist) for idx, name, dist, _kv in best_per_substation.values()]
    result.sort(key=lambda item: item[2])
    return result


def _distribute_capacity_by_inverse_distance(
    total_mw: float,
    substations: list[tuple[int, str, float]],
) -> list[tuple[int, str, float]]:
    """
    Split total_mw across substations using inverse-distance weighting —
    closer substations receive a proportionally larger share of the
    municipality's total HMKE capacity. This is a heuristic (the source
    data has no sub-municipality granularity) but is more realistic than
    concentrating all capacity on a single bus.

    Returns
    -------
    list[(bus_idx, bus_name, allocated_mw)]
    """
    weights = [1.0 / (distance_km + _HMKE_DISTANCE_WEIGHT_EPSILON_KM) for _idx, _name, distance_km in substations]
    weight_sum = sum(weights)
    return [
        (bus_idx, bus_name, total_mw * weight / weight_sum)
        for (bus_idx, bus_name, _distance_km), weight in zip(substations, weights)
    ]


def _create_mekh_element(
    net: pp.pandapowerNet,
    bus_idx: int,
    sn_mva: float,
    technology: str,
    name: str,
    season: str,
    voltage_level: str,
) -> tuple[int, str]:
    """
    Create a single MEKH-sourced generation/storage element.

    Units with installed capacity (sn_mva) above
    _MEKH_VOLTAGE_CONTROL_THRESHOLD_MVA (5 MW) are modelled as
    voltage-controllable PV nodes (pp.create_gen), with reactive power
    limits of +/- 30% of their rated capacity (_MEKH_REACTIVE_CAPABILITY_RATIO).
    Smaller units remain plain PQ injections (pp.create_sgen).

    Returns
    -------
    (index, table) : tuple[int, str]
        table is "gen" or "sgen".
    """
    scaling = TECH_SCALING_DEFAULTS.get(technology, {}).get(season, DEFAULT_GEN_SCALING)

    if sn_mva > _MEKH_VOLTAGE_CONTROL_THRESHOLD_MVA:
        q_capability = sn_mva * _MEKH_REACTIVE_CAPABILITY_RATIO
        idx = pp.create_gen(
            net,
            bus=bus_idx,
            p_mw=sn_mva,
            vm_pu=1.0,
            sn_mva=sn_mva,
            scaling=scaling,
            name=name,
            type=technology,
            in_service=True,
            max_q_mvar=q_capability,
            min_q_mvar=-q_capability,
        )
        net.gen.at[idx, "technology"] = technology
        net.gen.at[idx, "voltage_level"] = voltage_level
        net.gen.at[idx, "source"] = "MEKH"
        return idx, "gen"

    idx = pp.create_sgen(
        net,
        bus=bus_idx,
        p_mw=sn_mva,
        q_mvar=0.0,
        sn_mva=sn_mva,
        scaling=scaling,
        name=name,
        type=technology,
        in_service=True,
    )
    net.sgen.at[idx, "technology"] = technology
    net.sgen.at[idx, "voltage_level"] = voltage_level
    net.sgen.at[idx, "source"] = "MEKH"
    return idx, "sgen"


def build_generators(
    net: pp.pandapowerNet,
    sheets: dict[str, pd.DataFrame],
    bus_map: dict[str, int],
    season: str = RATING_SEASON_SUMMER,
) -> dict[str, int]:
    """
    Create gen / sgen elements from the Generators sheet (MAVIR source).

    Every element created here is tagged source="MAVIR" (net.gen/sgen
    "source" column), mirroring the tagging used for MEKH- and
    HMKE-created elements, so all three data sources can be distinguished
    downstream from a single column.

    Solar, wind and battery rows are skipped here — they are loaded
    separately from the MEKH list via build_mekh_generators(), so they
    are not double-counted. Matched against the RAW MAVIR "Technológia"
    label set (_MAVIR_RAW_SOLAR_WIND_BATTERY_LABELS), not the internal
    TECHNOLOGY_SOLAR/WIND/BATTERY constants.

    Returns
    -------
    dict[str, int]
        gen_map: {generator_name → (table, index)}
        where table is "gen" or "sgen".
    """
    df = sheets[SHEET_GENERATORS]
    gen_map: dict[str, tuple[str, int]] = {}
    skipped: list[str] = []
    ext_grid: list[str] = []
    mekh_sourced: list[str] = []

    for _, row in df.iterrows():
        name       = _str(row, "Engedélyesi azonosító")
        bus_name   = _str(row, "Végpont")
        technology = _str(row, "Technológia", "gas")
        in_service = _bool(row, "Bent", True)

        if _is_mavir_solar_wind_battery(technology):
            # Solar/wind/battery now come exclusively from the MEKH list.
            # NOTE: matched against the raw MAVIR label set
            # (_MAVIR_RAW_SOLAR_WIND_BATTERY_LABELS), NOT the internal
            # TECHNOLOGY_SOLAR/WIND/BATTERY constants — those constants are
            # only ever assigned to MEKH/HMKE-created elements and never
            # match the raw MAVIR "Technológia" column values (e.g.
            # 'SOLARPHOTOVO', 'ROOFTOPPV'), which was the root cause of
            # these rows previously slipping through unfiltered.
            mekh_sourced.append(f"{name} (technology='{technology}')")
            continue

        if bus_name not in bus_map:
            skipped.append(f"{name} (Bus '{bus_name}' not found)")
            continue

        if bus_name[0] == "X":
            ext_grid.append(f"{name} (Bus '{bus_name}' is external grid)")
            continue

        bus_idx = bus_map[bus_name]
        p_mw    = _float(row, "P",  0.0)
        q_mvar  = _float(row, "Q",  0.0)
        reg_bus_name = _str(row, "Szabpont", "")
        vn_kv_reg_bus = voltage_reader(reg_bus_name)
        vm_pu   = _float(row, "Uszab",  1.0) / vn_kv_reg_bus  # per-unit voltage setpoint
        sn_mva  = _float(row, "MVA", float("nan"))
        scaling = _get_scale(row, technology, season)

        max_q_mvar = _float(row, "Qmax",  0.0)
        min_q_mvar = _float(row, "Qmin",  0.0)

        max_p_mw   = _float(row, "Pmax",  0.0)
        min_p_mw   = _float(row, "Pmin",  0.0)

        if technology in VOLTAGE_CONTROL_TECHS and sn_mva is not None and sn_mva > 10 and ((max_q_mvar is not None and max_q_mvar != 0) or (min_q_mvar is not None and min_q_mvar != 0)):
            # Synchronous / voltage-controlled generator — PV node
            idx = pp.create_gen(
                net,
                bus=bus_idx,
                p_mw=p_mw,
                vm_pu=vm_pu,
                sn_mva=sn_mva,
                scaling=scaling,
                name=name,
                type=technology,
                in_service=in_service,
                max_q_mvar = max_q_mvar,
                min_q_mvar = min_q_mvar,
                max_p_mw   = max_p_mw,
                min_p_mw   = min_p_mw,
            )
            # Store technology for later use by apply_gen_scaling
            net.gen.at[idx, "technology"] = technology
            net.gen.at[idx, "source"] = _MAVIR_SOURCE_TAG
            gen_map[name] = ("gen", idx)

        else:
            idx = pp.create_sgen(
                net,
                bus=bus_idx,
                p_mw=p_mw,
                q_mvar=q_mvar,
                sn_mva=sn_mva,
                scaling=scaling,
                name=name,
                type=technology,
                in_service=in_service,
            )
            # Store technology for later use by apply_gen_scaling
            net.sgen.at[idx, "technology"] = technology
            net.sgen.at[idx, "source"] = _MAVIR_SOURCE_TAG
            gen_map[name] = ("sgen", idx)

    if skipped:
        print(f"[generator_builder]  WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))

    if ext_grid:
        print(f"[generator_builder]  omitted {len(ext_grid)} external grids")

    if mekh_sourced:
        print(f"[generator_builder]  skipped {len(mekh_sourced)} solar/wind/"
              f"battery rows from MAVIR sheet (now sourced from MEKH list)")

    n_sgen = sum(1 for v in gen_map.values() if v[0] == "sgen")
    n_gen  = sum(1 for v in gen_map.values() if v[0] == "gen")
    print(f"[generator_builder]  Created {n_gen} gen (PV) + "
          f"{n_sgen} sgen (PQ), source='{_MAVIR_SOURCE_TAG}', season={season}.")

    return gen_map


def build_mekh_generators(
    net: pp.pandapowerNet,
    mekh_df: pd.DataFrame,
    bus_map: dict[str, int],
    season: str = RATING_SEASON_SUMMER,
    geolocator=None,
    geocode_cache_path: str = _DEFAULT_GEOCODE_CACHE_PATH,
) -> dict[str, int]:
    """
    Create gen/sgen elements (solar, wind, battery) from the MEKH
    connection capacity list.

    Rows whose column-J future connection date falls after
    _MEKH_MAX_CONNECTION_YEAR (2035) are skipped entirely — see "Future
    connection date filtering" in the module docstring. Rows with no date
    in column J are not affected by this rule.

    Substation resolution happens in two stages per row:
      1. Prefix match on the first 5 characters of the substation name
         (col B) against pandapower bus names, disambiguated by voltage
         level (col G).
      2. If that fails (imprecise/alternate MEKH naming), fall back to
         geocoding the municipality name (col A) via GeoPy and picking
         the nearest bus using each bus's stored geo-coordinates
         (net.bus['geo'], a GeoJSON string parsed with json.loads()),
         also filtered by voltage level (col G) whenever possible.
         Geocoding results are cached on disk (geocode_cache_path) so
         repeated runs never re-geocode the same municipality.

    Parameters
    ----------
    mekh_df : pd.DataFrame
        The MEKH sheet loaded with header=1 (so the Hungarian header row
        becomes the column names), e.g.:
            pd.read_excel(path, sheet_name="Table 1", header=1)
    bus_map : dict[str, int]
        Substation name → pandapower bus index.
    season : str
        RATING_SEASON_SUMMER or RATING_SEASON_WINTER — used to resolve the
        per-technology scaling factor.
    geolocator : geopy geocoder instance, optional
        Defaults to geopy.geocoders.Nominatim(user_agent="mekh_generator_builder")
        if not provided. Only instantiated/used if the prefix match fails
        for at least one row not already present in the on-disk cache.
    geocode_cache_path : str, optional
        Path to the persistent JSON geocode cache. Defaults to
        "municipality_geocode_cache.json" next to this module. Pass a
        project-specific path to keep caches separate across different
        networks/datasets. Shared format with build_hmke_generators().

    Returns
    -------
    dict[str, int]
        gen_map: {generator_name → (table, index)}
    """
    gen_map: dict[str, tuple[str, int]] = {}
    skipped: list[str] = []
    geo_resolved: list[str] = []
    future_skipped: list[str] = []

    cols = mekh_df.columns.tolist()
    col_municipality = cols[_MEKH_COL_MUNICIPALITY]
    col_sub    = cols[_MEKH_COL_SUBSTATION]
    col_cap_g  = cols[_MEKH_COL_CAPACITY_GEN]
    col_cap_s  = cols[_MEKH_COL_CAPACITY_STORE]
    col_volt   = cols[_MEKH_COL_VOLTAGE_LEVEL]
    col_source = cols[_MEKH_COL_ENERGY_SOURCE]
    col_stech  = cols[_MEKH_COL_STORAGE_TECH]
    col_owner  = cols[_MEKH_COL_NAME_OWNER]
    col_conn_date = cols[_MEKH_COL_CONNECTION_DATE]

    prefix_index = _build_bus_prefix_index(bus_map)
    bus_coord_index: dict[int, tuple[float, float, float | None]] | None = None
    spatial_index: _BusGeoSpatialIndex | None = None
    idx_to_name: dict[int, str] | None = None
    geocode_cache = _load_geocode_cache(geocode_cache_path)
    cache_size_at_start = len(geocode_cache)
    n_cache_hits = 0
    n_live_geocodes = 0

    for i, row in mekh_df.iterrows():
        owner = str(row.get(col_owner, "")).strip()

        connection_date = _parse_mekh_connection_date(row.get(col_conn_date))
        if _is_mekh_connection_too_far_future(connection_date):
            future_skipped.append(
                f"row {i} '{owner}' (connection date {connection_date.date()} "
                f"after {_MEKH_MAX_CONNECTION_YEAR})"
            )
            continue

        mekh_sub_name = str(row.get(col_sub, "")).strip()
        municipality  = str(row.get(col_municipality, "")).strip()
        owner    = str(row.get(col_owner, "")).strip()
        voltage_level = str(row.get(col_volt, "")).strip()

        resolved = _resolve_mekh_bus_by_prefix(prefix_index, mekh_sub_name, voltage_level)

        if resolved is None and municipality:
            # Lazily build the bus coordinate index, spatial index and
            # geolocator only once we actually need the geo fallback —
            # all built a single time and reused for every subsequent row.
            if bus_coord_index is None:
                bus_coord_index = _build_bus_coord_index(net, bus_map)
                spatial_index = _BusGeoSpatialIndex(bus_coord_index)
                idx_to_name = {idx: name for name, idx in bus_map.items()}
                if geolocator is None:
                    from geopy.geocoders import Nominatim
                    geolocator = Nominatim(user_agent="mekh_generator_builder")

            was_cached = municipality in geocode_cache
            resolved = _resolve_mekh_bus_by_geo(
                municipality, voltage_level, bus_map, spatial_index,
                geolocator, geocode_cache, idx_to_name
            )
            if was_cached:
                n_cache_hits += 1
            else:
                n_live_geocodes += 1
                # Persist incrementally so a crash mid-run doesn't lose
                # already-geocoded municipalities.
                _save_geocode_cache(geocode_cache_path, geocode_cache)

            if resolved is not None:
                geo_resolved.append(
                    f"row {i} '{owner}' (Substation '{mekh_sub_name}' → "
                    f"geo-matched via municipality '{municipality}' "
                    f"[{voltage_level}] to bus '{resolved[0]}')"
                )

        if resolved is None:
            skipped.append(f"row {i} '{owner}' (Substation '{mekh_sub_name}' not found, "
                            f"municipality '{municipality}' geocoding failed)")
            continue
        bus_name, bus_idx = resolved

        energy_source = str(row.get(col_source, "")).strip()
        storage_tech  = str(row.get(col_stech, "")).strip()

        # Solar / wind generation capacity (column E)
        technology = _ENERGY_SOURCE_MAP.get(energy_source)
        if technology is not None and energy_source != _NOT_APPLICABLE:
            sn_mva = _parse_mva(row.get(col_cap_g))
            if sn_mva:
                name = f"MEKH_{technology}_{bus_name}_{owner}_{i}"
                idx, table = _create_mekh_element(
                    net, bus_idx, sn_mva, technology, name, season, voltage_level
                )
                gen_map[name] = (table, idx)

        # Battery storage capacity (column F)
        battery_technology = _STORAGE_TECH_MAP.get(storage_tech)
        if battery_technology is not None and storage_tech != _NOT_APPLICABLE:
            sn_mva = _parse_mva(row.get(col_cap_s))
            if sn_mva:
                name = f"MEKH_{battery_technology}_{bus_name}_{owner}_{i}"
                idx, table = _create_mekh_element(
                    net, bus_idx, sn_mva, battery_technology, name, season, voltage_level
                )
                gen_map[name] = (table, idx)

    # Final save to make sure the on-disk cache reflects everything
    # geocoded in this run (harmless if already saved incrementally).
    if len(geocode_cache) != cache_size_at_start:
        _save_geocode_cache(geocode_cache_path, geocode_cache)

    if future_skipped:
        print(f"[generator_builder]  MEKH INFO — skipped {len(future_skipped)} rows "
              f"with connection date after {_MEKH_MAX_CONNECTION_YEAR}: "
              + "; ".join(future_skipped))

    if geo_resolved:
        print(f"[generator_builder]  MEKH INFO — {len(geo_resolved)} rows resolved "
              f"via geocoding fallback ({n_cache_hits} from on-disk cache, "
              f"{n_live_geocodes} newly geocoded via Nominatim)")

    if skipped:
        print(f"[generator_builder]  MEKH WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))

    n_gen  = sum(1 for v in gen_map.values() if v[0] == "gen")
    n_sgen = sum(1 for v in gen_map.values() if v[0] == "sgen")
    print(f"[generator_builder]  Created {n_gen} gen (PV, >{_MEKH_VOLTAGE_CONTROL_THRESHOLD_MVA:.0f} MW, "
          f"+/-{_MEKH_REACTIVE_CAPABILITY_RATIO:.0%} Q capability) + "
          f"{n_sgen} sgen (PQ, <= {_MEKH_VOLTAGE_CONTROL_THRESHOLD_MVA:.0f} MW) from MEKH list "
          f"(solar/wind/battery), season={season}. "
          f"Geocode cache: '{geocode_cache_path}' ({len(geocode_cache)} entries).")

    return gen_map


def _load_hmke_sheet(hmke_xlsx_path: str, sheet_name: str = SHEET_HMKE) -> pd.DataFrame:
    """
    Load and clean the HMKE municipal PV statistics sheet.

    The raw sheet has two stacked header rows (year, then metric label) —
    this reads it headerless and slices off the data starting at row
    _HMKE_DATA_START_ROW, assigning clean column names.

    Returns
    -------
    pd.DataFrame with columns:
        municipality, bt_2024_kw, count_2024, bt_2025_kw, count_2025
    """
    raw = pd.read_excel(hmke_xlsx_path, sheet_name=sheet_name, header=None)
    df = raw.iloc[_HMKE_DATA_START_ROW:].reset_index(drop=True)
    df.columns = ["municipality", "bt_2024_kw", "count_2024", "bt_2025_kw", "count_2025"]
    df = df.dropna(subset=["municipality"])
    df["municipality"] = df["municipality"].astype(str).str.strip()
    for col in ("bt_2024_kw", "count_2024", "bt_2025_kw", "count_2025"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def build_hmke_generators(
    net: pp.pandapowerNet,
    hmke_source: str | pd.DataFrame,
    bus_map: dict[str, int],
    season: str = RATING_SEASON_SUMMER,
    geolocator=None,
    geocode_cache_path: str = _DEFAULT_GEOCODE_CACHE_PATH,
    sheet_name: str = SHEET_HMKE,
) -> dict[str, int]:
    """
    Create sgen elements for household-size PV (HMKE — Háztartási Méretű
    Kiserőmű) from the HMKE municipal statistics workbook, using 2025
    installed capacity data (column "HMKE PV BT (kW)" for 2025).

    Each municipality's total installed HMKE PV capacity (kW, converted to
    MW) is geocoded once, then handled in one of two ways:
      * Below _HMKE_MULTI_SUBSTATION_THRESHOLD_MW (default 5 MW): the
        entire municipality's capacity is connected to the single nearest
        distribution bus (lowest-voltage bus near the matched location),
        as a single aggregated sgen.
      * At or above the threshold (typically larger towns/cities): the
        capacity is instead distributed across ALL distinct substations
        found within a capacity-scaled search radius of the geocoded
        location, weighted by inverse distance, each becoming its own
        sgen — see "Multi-substation distribution for large cities" in
        the module docstring. If only one substation is found within the
        radius even though the threshold is exceeded, this degrades
        gracefully to a single sgen.

    HMKE units are always PQ (pp.create_sgen) — they are far below the
    voltage-control threshold and never provide reactive support.

    Parameters
    ----------
    hmke_source : str or pd.DataFrame
        Path to the HMKE statistics .xlsx file, or an already-loaded
        DataFrame with columns
        ["municipality", "bt_2024_kw", "count_2024", "bt_2025_kw", "count_2025"]
        (as produced by _load_hmke_sheet()).
    bus_map : dict[str, int]
        Substation/bus name → pandapower bus index.
    season : str
        RATING_SEASON_SUMMER or RATING_SEASON_WINTER — used to resolve the
        scaling factor for solar (HMKE is always modelled as solar PV).
    geolocator : geopy geocoder instance, optional
        Defaults to geopy.geocoders.Nominatim(user_agent="mekh_generator_builder").
    geocode_cache_path : str, optional
        Path to the persistent JSON geocode cache — shared format (and, by
        default, the same file) as build_mekh_generators(), so
        municipalities geocoded for MEKH are reused here and vice versa.
    sheet_name : str, optional
        Overrides the sheet name to read from, if hmke_source is a path.

    Returns
    -------
    dict[str, int]
        gen_map: {generator_name → ("sgen", index)}
    """
    if isinstance(hmke_source, str):
        hmke_df = _load_hmke_sheet(hmke_source, sheet_name=sheet_name)
    else:
        hmke_df = hmke_source

    gen_map: dict[str, tuple[str, int]] = {}
    skipped: list[str] = []
    multi_substation_cities: list[str] = []

    bus_coord_index = _build_bus_coord_index(net, bus_map)
    spatial_index = _BusGeoSpatialIndex(bus_coord_index)
    idx_to_name = {idx: name for name, idx in bus_map.items()}
    if geolocator is None:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="mekh_generator_builder")

    geocode_cache = _load_geocode_cache(geocode_cache_path)
    cache_size_at_start = len(geocode_cache)
    n_cache_hits = 0
    n_live_geocodes = 0

    print(f"[generator_builder]  Building HMKE PV sgen from {len(hmke_df)} rows "
          f"({len(geocode_cache)} municipalities already cached in "f"'{geocode_cache_path}').")

    def _create_hmke_sgen(bus_idx: int, bus_name: str, municipality: str, p_mw: float, row_idx, suffix: str = "") -> None:
        name = f"HMKE_{_HMKE_TECHNOLOGY}_{bus_name}_{municipality}{suffix}_{row_idx}"
        scaling = TECH_SCALING_DEFAULTS.get(_HMKE_TECHNOLOGY, {}).get(season, DEFAULT_GEN_SCALING)
        idx = pp.create_sgen(
            net,
            bus=bus_idx,
            p_mw=p_mw,
            q_mvar=0.0,
            sn_mva=p_mw,
            scaling=scaling,
            name=name,
            type=_HMKE_TECHNOLOGY,
            in_service=True,
        )
        net.sgen.at[idx, "technology"] = _HMKE_TECHNOLOGY
        net.sgen.at[idx, "source"] = "HMKE"
        net.sgen.at[idx, "municipality"] = municipality
        gen_map[name] = ("sgen", idx)

    for i, row in hmke_df.iterrows():
        municipality = str(row["municipality"]).strip()
        bt_kw_2025 = row["bt_2025_kw"]

        if not municipality or pd.isna(bt_kw_2025) or bt_kw_2025 <= 0:
            continue

        p_mw = float(bt_kw_2025) * _KW_TO_MW  # kW -> MW

        was_cached = municipality in geocode_cache
        place_coords = _geocode_municipality(municipality, geolocator, geocode_cache)
        if was_cached:
            n_cache_hits += 1
        else:
            n_live_geocodes += 1
            _save_geocode_cache(geocode_cache_path, geocode_cache)

        if place_coords is None:
            skipped.append(f"row {i} '{municipality}' (geocoding failed, "
                            f"{bt_kw_2025:.1f} kW / {p_mw:.4f} MW skipped)")
            continue

        lat, lon = place_coords

        if p_mw >= _HMKE_MULTI_SUBSTATION_THRESHOLD_MW:
            radius_km = _city_search_radius_km(p_mw)
            substations = _find_city_substations(
                spatial_index, bus_coord_index, idx_to_name, lat, lon, radius_km
            )

            if len(substations) > 1:
                allocations = _distribute_capacity_by_inverse_distance(p_mw, substations)
                for share_idx, (bus_idx, bus_name, allocated_mw) in enumerate(allocations):
                    _create_hmke_sgen(bus_idx, bus_name, municipality, allocated_mw, i, suffix=f"_s{share_idx}")
                multi_substation_cities.append(
                    f"'{municipality}' ({p_mw:.1f} MW -> {len(allocations)} substations "
                    f"within {radius_km:.1f} km)"
                )
                continue

            if len(substations) == 1:
                bus_idx, bus_name, _dist = substations[0]
                _create_hmke_sgen(bus_idx, bus_name, municipality, p_mw, i)
                continue
            # No substation found within radius at all — fall through to
            # the standard nearest-bus resolution below.

        resolved = _resolve_nearest_bus_by_geo(
            municipality, bus_map, spatial_index, geolocator, geocode_cache,
            idx_to_name, bus_coord_index, prefer_lowest_voltage=True,
        )

        if resolved is None:
            skipped.append(f"row {i} '{municipality}' (no bus found near geocoded "
                            f"location, {bt_kw_2025:.1f} kW / {p_mw:.4f} MW skipped)")
            continue

        bus_name, bus_idx = resolved
        _create_hmke_sgen(bus_idx, bus_name, municipality, p_mw, i)

    if len(geocode_cache) != cache_size_at_start:
        _save_geocode_cache(geocode_cache_path, geocode_cache)

    if multi_substation_cities:
        print(f"[generator_builder]  HMKE INFO — {len(multi_substation_cities)} "
              f"large municipalities distributed across multiple substations: "
              + "; ".join(multi_substation_cities))

    if skipped:
        print(f"[generator_builder]  HMKE WARNING — skipped {len(skipped)}: "
              + "; ".join(skipped))

    total_mw = sum(net.sgen.at[idx, "p_mw"] for _n, (_t, idx) in gen_map.items())
    print(f"[generator_builder]  Created {len(gen_map)} sgen from HMKE list "
          f"(household PV, {n_cache_hits} geocode cache hits, "
          f"{n_live_geocodes} newly geocoded), total {total_mw:.3f} MW, "
          f"season={season}. Geocode cache: '{geocode_cache_path}' "
          f"({len(geocode_cache)} entries).")

    return gen_map


def apply_gen_scaling(
    net: pp.pandapowerNet,
    season: str,
    tech_overrides: dict[str, float] | None = None,
) -> None:
    """
    Update the `scaling` column in net.gen and net.sgen for all generators
    based on per-technology factors for the given season.

    Parameters
    ----------
    season : str
        RATING_SEASON_SUMMER or RATING_SEASON_WINTER.
    tech_overrides : dict, optional
        {technology_label: scale_factor} — overrides config defaults
        for specific technologies in this call only.
    """
    overrides = tech_overrides or {}

    def _scale(technology) -> float:
        if isinstance(technology, list):
            technology = technology[0] if technology else None
        if technology in overrides:
            return overrides[technology]
        return TECH_SCALING_DEFAULTS.get(technology, {}).get(season, DEFAULT_GEN_SCALING)

    if "technology" in net.gen.columns:
        net.gen["scaling"] = net.gen["technology"].apply(_scale)

    if "technology" in net.sgen.columns:
        net.sgen["scaling"] = net.sgen["technology"].apply(_scale)

    print(f"[generator_builder]  Generator scaling updated for season={season}.")
