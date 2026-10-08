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
* Random small/medium distributed solar can be added with
  build_distributed_solar_22kv(). It creates non-voltage-controlling
  pp.create_sgen elements only on in-service 22 kV busbars. Individual
  project sizes are random within 50-500 kW by default, while their sum
  is forced to the requested total (default 1460 MW).
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
    TECHNOLOGY_WIND, TECHNOLOGY_SOLAR, TECHNOLOGY_BATTERY, VOLTAGE_CONTROL_TECHS,
    _DISTRIBUTED_PV_DEFAULT_TOTAL_MW,
    _DISTRIBUTED_PV_TARGET_KV,
    _DISTRIBUTED_PV_KV_TOLERANCE
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
    battery_mode: float = 0.0,
) -> tuple[int, str]:
    """
    Create one MEKH generation or battery element.

    Battery operation is controlled by battery_mode:

        -1.0 <= battery_mode < 0.0:
            Charging/consumption.

        battery_mode == 0.0:
            Idle; no active or reactive power exchange.

        0.0 < battery_mode <= 1.0:
            Discharging/production.

    Battery active power is:

        sn_mva * abs(battery_mode)

    Batteries are always non-voltage-controlling.

    Returns
    -------
    tuple[int, str]
        Element index and pandapower table name:
        "gen", "sgen", or "load".
    """

    sn_mva = float(sn_mva)
    battery_mode = float(battery_mode)

    if not np.isfinite(sn_mva) or sn_mva <= 0.0:
        raise ValueError(
            f"Invalid MEKH capacity for '{name}': "
            f"sn_mva={sn_mva!r}."
        )

    if not np.isfinite(battery_mode):
        raise ValueError(
            "battery_mode must be a finite number."
        )

    if not -1.0 <= battery_mode <= 1.0:
        raise ValueError(
            f"battery_mode must be between -1.0 and +1.0; "
            f"received {battery_mode!r}."
        )

    # --------------------------------------------------------------
    # Battery
    # --------------------------------------------------------------
    if technology == TECHNOLOGY_BATTERY:
        operating_power_mw = (
            sn_mva * abs(battery_mode)
        )

        if battery_mode < 0.0:
            # Positive load means consumption in pandapower.
            battery_state = "charging"

            idx = pp.create_load(
                net,
                bus=bus_idx,
                p_mw=operating_power_mw,
                q_mvar=0.0,
                sn_mva=sn_mva,
                scaling=1.0,
                name=name,
                type=technology,
                in_service=True,
            )

            net.load.at[
                idx, "technology"
            ] = TECHNOLOGY_BATTERY

            net.load.at[
                idx, "voltage_level"
            ] = voltage_level

            net.load.at[
                idx, "source"
            ] = "MEKH"

            net.load.at[
                idx, "battery_mode"
            ] = battery_mode

            net.load.at[
                idx, "battery_state"
            ] = battery_state

            net.load.at[
                idx, "rated_power_mw"
            ] = sn_mva

            net.load.at[
                idx, "operating_power_mw"
            ] = operating_power_mw

            return idx, "load"

        # Idle and discharging batteries are represented as sgen.
        if battery_mode > 0.0:
            battery_state = "discharging"
        else:
            battery_state = "idle"

        idx = pp.create_sgen(
            net,
            bus=bus_idx,
            p_mw=operating_power_mw,
            q_mvar=0.0,
            sn_mva=sn_mva,
            scaling=1.0,
            name=name,
            type=technology,
            in_service=True,
        )

        net.sgen.at[
            idx, "technology"
        ] = TECHNOLOGY_BATTERY

        net.sgen.at[
            idx, "voltage_level"
        ] = voltage_level

        net.sgen.at[
            idx, "source"
        ] = "MEKH"

        net.sgen.at[
            idx, "battery_mode"
        ] = battery_mode

        net.sgen.at[
            idx, "battery_state"
        ] = battery_state

        net.sgen.at[
            idx, "rated_power_mw"
        ] = sn_mva

        net.sgen.at[
            idx, "operating_power_mw"
        ] = operating_power_mw

        return idx, "sgen"

    # --------------------------------------------------------------
    # Solar and wind
    # --------------------------------------------------------------
    scaling = TECH_SCALING_DEFAULTS.get(
        technology,
        {},
    ).get(
        season,
        DEFAULT_GEN_SCALING,
    )

    if (
        sn_mva
        > _MEKH_VOLTAGE_CONTROL_THRESHOLD_MVA
    ):
        q_capability = (
            sn_mva
            * _MEKH_REACTIVE_CAPABILITY_RATIO
        )

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

        net.gen.at[
            idx, "technology"
        ] = technology

        net.gen.at[
            idx, "voltage_level"
        ] = voltage_level

        net.gen.at[
            idx, "source"
        ] = "MEKH"

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

    net.sgen.at[
        idx, "technology"
    ] = technology

    net.sgen.at[
        idx, "voltage_level"
    ] = voltage_level

    net.sgen.at[
        idx, "source"
    ] = "MEKH"

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
    battery_mode: float = 0.0,
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

    battery_mode = float(battery_mode)

    if not np.isfinite(battery_mode):
        raise ValueError(
            "battery_mode must be a finite number."
        )

    if not -1.0 <= battery_mode <= 1.0:
        raise ValueError(
            f"battery_mode must be between -1.0 and +1.0; "
            f"received {battery_mode!r}."
        )

    if battery_mode < 0.0:
        battery_state = "charging"
    elif battery_mode > 0.0:
        battery_state = "discharging"
    else:
        battery_state = "idle"

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
                    net, bus_idx, sn_mva, battery_technology, name, season, voltage_level, battery_mode
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
    n_load = sum(1 for v in gen_map.values() if v[0] == "load")
    print(
        f"[generator_builder] Created "
        f"{n_gen} gen + {n_sgen} sgen + "
        f"{n_load} battery load elements from MEKH; "
        f"battery_mode={battery_mode:+.3f}, "
        f"battery_state={battery_state}, "
        f"season={season}."
    )

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


def build_distributed_solar_22kv(
    net: pp.pandapowerNet,
    bus_map: dict[str, int],
    total_capacity_mw: float = 1460.0,
    season: str = RATING_SEASON_SUMMER,
    min_project_kw: float = 50.0,
    max_project_kw: float = 500.0,
    random_seed: int | None = None,
) -> dict[str, tuple[str, int]]:
    """
    Create randomly sized distributed solar projects on 22 kV busbars.

    Multiple projects may be connected to the same busbar. Projects are
    distributed as evenly as possible: the number of projects connected
    to any two eligible busbars differs by no more than one.

    Individual project capacities are randomly generated between
    min_project_kw and max_project_kw. Their combined installed capacity
    is adjusted to equal total_capacity_mw exactly.

    All projects are non-voltage-controlling pp.create_sgen elements with:

        q_mvar = 0.0
        scaling = 1.0
        in_service = True

    Parameters
    ----------
    net : pandapowerNet
        Network to which the distributed generators are added.

    bus_map : dict[str, int]
        Mapping from bus names to pandapower bus indices.

    total_capacity_mw : float, default 1460.0
        Required total installed distributed-solar capacity in MW.

    season : str
        Retained for compatibility and reporting. Distributed projects
        are created with scaling=1.0.

    min_project_kw : float, default 50.0
        Minimum individual project capacity in kW.

    max_project_kw : float, default 500.0
        Maximum individual project capacity in kW.

    random_seed : int or None
        Random seed for repeatable project sizes and locations.

    Returns
    -------
    dict[str, tuple[str, int]]
        Mapping:

            project_name -> ("sgen", sgen_index)

    Raises
    ------
    ValueError
        If parameters are invalid or no in-service 22 kV busbars exist.

    RuntimeError
        If the exact requested total cannot be created.
    """

    source_tag = "DISTRIBUTED_22KV"
    target_voltage_kv = 22.0
    voltage_tolerance_kv = 1e-6
    capacity_tolerance_mw = 1e-9

    total_capacity_mw = float(total_capacity_mw)
    min_project_mw = float(min_project_kw) / 1000.0
    max_project_mw = float(max_project_kw) / 1000.0

    # --------------------------------------------------------------
    # Validate parameters
    # --------------------------------------------------------------
    if not np.isfinite(total_capacity_mw):
        raise ValueError(
            "total_capacity_mw must be a finite number."
        )

    if total_capacity_mw < 0.0:
        raise ValueError(
            "total_capacity_mw cannot be negative."
        )

    if not np.isfinite(min_project_mw):
        raise ValueError(
            "min_project_kw must be a finite number."
        )

    if not np.isfinite(max_project_mw):
        raise ValueError(
            "max_project_kw must be a finite number."
        )

    if min_project_mw <= 0.0:
        raise ValueError(
            "min_project_kw must be greater than zero."
        )

    if max_project_mw < min_project_mw:
        raise ValueError(
            "max_project_kw must be greater than or equal to "
            "min_project_kw."
        )

    if total_capacity_mw == 0.0:
        print(
            "[generator_builder] Distributed 22 kV solar requested "
            "capacity is 0 MW; no projects were created."
        )
        return {}

    # --------------------------------------------------------------
    # Find eligible in-service 22 kV busbars
    # --------------------------------------------------------------
    bus_name_by_index = {
        bus_idx: bus_name
        for bus_name, bus_idx in bus_map.items()
    }

    candidate_buses: list[tuple[int, str]] = []

    for bus_idx in net.bus.index:
        try:
            vn_kv = float(net.bus.at[bus_idx, "vn_kv"])
        except (KeyError, TypeError, ValueError):
            continue

        if not np.isfinite(vn_kv):
            continue

        if "in_service" in net.bus.columns:
            in_service_value = net.bus.at[
                bus_idx, "in_service"
            ]

            in_service = (
                True
                if pd.isna(in_service_value)
                else bool(in_service_value)
            )
        else:
            in_service = True

        if not in_service:
            continue

        if not np.isclose(
            vn_kv,
            target_voltage_kv,
            atol=voltage_tolerance_kv,
            rtol=0.0,
        ):
            continue

        bus_name = bus_name_by_index.get(bus_idx)

        if bus_name is None:
            if "name" in net.bus.columns:
                network_bus_name = net.bus.at[bus_idx, "name"]

                bus_name = (
                    str(network_bus_name)
                    if pd.notna(network_bus_name)
                    else str(bus_idx)
                )
            else:
                bus_name = str(bus_idx)

        candidate_buses.append(
            (int(bus_idx), bus_name)
        )

    if not candidate_buses:
        raise ValueError(
            "No in-service 22 kV busbars were found. Check "
            "net.bus['vn_kv'] and net.bus['in_service']."
        )

    bus_count = len(candidate_buses)
    rng = np.random.default_rng(random_seed)

    # --------------------------------------------------------------
    # Determine the number of individual projects
    # --------------------------------------------------------------
    minimum_project_count = int(
        np.ceil(
            total_capacity_mw / max_project_mw - 1e-12
        )
    )

    maximum_project_count = int(
        np.floor(
            total_capacity_mw / min_project_mw + 1e-12
        )
    )

    if minimum_project_count > maximum_project_count:
        raise ValueError(
            f"Requested capacity {total_capacity_mw:.6f} MW cannot "
            f"be represented using project capacities between "
            f"{min_project_kw:.3f} and "
            f"{max_project_kw:.3f} kW."
        )

    target_average_project_mw = (
        min_project_mw + max_project_mw
    ) / 2.0

    estimated_project_count = int(
        round(
            total_capacity_mw
            / target_average_project_mw
        )
    )

    project_count = int(
        np.clip(
            estimated_project_count,
            minimum_project_count,
            maximum_project_count,
        )
    )

    minimum_total_for_count = (
        project_count * min_project_mw
    )

    maximum_total_for_count = (
        project_count * max_project_mw
    )

    if not (
        minimum_total_for_count
        <= total_capacity_mw
        <= maximum_total_for_count
    ):
        raise RuntimeError(
            f"Internal project-count error: "
            f"{project_count} projects can represent only "
            f"{minimum_total_for_count:.6f}-"
            f"{maximum_total_for_count:.6f} MW, but "
            f"{total_capacity_mw:.6f} MW was requested."
        )

    # --------------------------------------------------------------
    # Generate random capacities with an exact combined total
    # --------------------------------------------------------------
    raw_capacities_mw = rng.uniform(
        min_project_mw,
        max_project_mw,
        size=project_count,
    )

    # Add a common offset to the random capacities and clip them to
    # the permitted range. Bisection finds the offset that produces
    # the requested total.
    lower_offset = (
        min_project_mw
        - float(raw_capacities_mw.max())
    )

    upper_offset = (
        max_project_mw
        - float(raw_capacities_mw.min())
    )

    capacities_mw = raw_capacities_mw.copy()

    for _ in range(100):
        offset = (
            lower_offset + upper_offset
        ) / 2.0

        capacities_mw = np.clip(
            raw_capacities_mw + offset,
            min_project_mw,
            max_project_mw,
        )

        current_total_mw = float(
            capacities_mw.sum()
        )

        if current_total_mw < total_capacity_mw:
            lower_offset = offset
        else:
            upper_offset = offset

    final_offset = (
        lower_offset + upper_offset
    ) / 2.0

    capacities_mw = np.clip(
        raw_capacities_mw + final_offset,
        min_project_mw,
        max_project_mw,
    )

    # Close the remaining floating-point residual while respecting
    # the capacity limits.
    residual_mw = (
        total_capacity_mw
        - float(capacities_mw.sum())
    )

    if abs(residual_mw) > capacity_tolerance_mw:
        random_project_order = rng.permutation(
            project_count
        )

        for project_position in random_project_order:
            if (
                abs(residual_mw)
                <= capacity_tolerance_mw
            ):
                break

            if residual_mw > 0.0:
                available_headroom_mw = (
                    max_project_mw
                    - capacities_mw[project_position]
                )

                adjustment_mw = min(
                    residual_mw,
                    available_headroom_mw,
                )

                capacities_mw[
                    project_position
                ] += adjustment_mw

                residual_mw -= adjustment_mw

            else:
                available_reduction_mw = (
                    capacities_mw[project_position]
                    - min_project_mw
                )

                adjustment_mw = min(
                    abs(residual_mw),
                    available_reduction_mw,
                )

                capacities_mw[
                    project_position
                ] -= adjustment_mw

                residual_mw += adjustment_mw

    generated_total_mw = float(
        capacities_mw.sum()
    )

    if not np.isclose(
        generated_total_mw,
        total_capacity_mw,
        atol=capacity_tolerance_mw,
        rtol=0.0,
    ):
        raise RuntimeError(
            f"Could not create the exact requested capacity. "
            f"Requested={total_capacity_mw:.9f} MW, "
            f"generated={generated_total_mw:.9f} MW."
        )

    if float(capacities_mw.min()) < (
        min_project_mw - capacity_tolerance_mw
    ):
        raise RuntimeError(
            "At least one generated project is below "
            "min_project_kw."
        )

    if float(capacities_mw.max()) > (
        max_project_mw + capacity_tolerance_mw
    ):
        raise RuntimeError(
            "At least one generated project is above "
            "max_project_kw."
        )

    # Randomize the order of project capacities before assigning
    # them to busbars.
    rng.shuffle(capacities_mw)

    # --------------------------------------------------------------
    # Create an evenly distributed list of bus assignments
    # --------------------------------------------------------------
    complete_rounds = (
        project_count // bus_count
    )

    remaining_projects = (
        project_count % bus_count
    )

    bus_assignments: list[tuple[int, str]] = []

    # Every complete round assigns exactly one new project to every
    # candidate busbar. A fresh permutation is used for each round.
    for _ in range(complete_rounds):
        round_order = rng.permutation(bus_count)

        for candidate_position in round_order:
            bus_assignments.append(
                candidate_buses[
                    int(candidate_position)
                ]
            )

    # Any remaining projects are placed on a random subset of buses,
    # with no bus receiving two projects in this partial round.
    if remaining_projects:
        partial_round_order = rng.permutation(
            bus_count
        )[:remaining_projects]

        for candidate_position in partial_round_order:
            bus_assignments.append(
                candidate_buses[
                    int(candidate_position)
                ]
            )

    if len(bus_assignments) != project_count:
        raise RuntimeError(
            f"Bus-assignment error: expected "
            f"{project_count} assignments, created "
            f"{len(bus_assignments)}."
        )

    # Shuffle assignments and capacities together only through the
    # assignment list. Project counts per bus remain balanced.
    rng.shuffle(bus_assignments)

    # --------------------------------------------------------------
    # Create non-voltage-controlling sgen elements
    # --------------------------------------------------------------
    generator_map: dict[
        str,
        tuple[str, int],
    ] = {}

    created_indices: list[int] = []

    project_number_at_bus: dict[int, int] = {
        bus_idx: 0
        for bus_idx, _bus_name in candidate_buses
    }

    for project_number, (
        capacity_mw,
        bus_assignment,
    ) in enumerate(
        zip(capacities_mw, bus_assignments),
        start=1,
    ):
        bus_idx, bus_name = bus_assignment

        project_number_at_bus[bus_idx] += 1
        local_project_number = (
            project_number_at_bus[bus_idx]
        )

        project_name = (
            f"DIST22_SOLAR_"
            f"{project_number:05d}_"
            f"{bus_idx}_"
            f"{local_project_number:02d}"
        )

        sgen_idx = pp.create_sgen(
            net,
            bus=bus_idx,
            p_mw=float(capacity_mw),
            q_mvar=0.0,
            sn_mva=float(capacity_mw),
            scaling=1.0,
            name=project_name,
            type=TECHNOLOGY_SOLAR,
            in_service=True,
        )

        net.sgen.at[
            sgen_idx, "technology"
        ] = TECHNOLOGY_SOLAR

        net.sgen.at[
            sgen_idx, "source"
        ] = source_tag

        net.sgen.at[
            sgen_idx, "voltage_level"
        ] = "22 kV"

        net.sgen.at[
            sgen_idx, "project_capacity_kw"
        ] = float(capacity_mw) * 1000.0

        net.sgen.at[
            sgen_idx, "connection_bus_name"
        ] = bus_name

        net.sgen.at[
            sgen_idx, "project_number_at_bus"
        ] = local_project_number

        generator_map[project_name] = (
            "sgen",
            sgen_idx,
        )

        created_indices.append(sgen_idx)

    # --------------------------------------------------------------
    # Verify the actual pandapower table
    # --------------------------------------------------------------
    created_sgens = net.sgen.loc[
        created_indices
    ]

    created_total_mw = float(
        created_sgens["p_mw"].sum()
    )

    created_minimum_kw = float(
        created_sgens["p_mw"].min()
        * 1000.0
    )

    created_maximum_kw = float(
        created_sgens["p_mw"].max()
        * 1000.0
    )

    projects_per_bus = (
        created_sgens.groupby("bus").size()
    )

    minimum_projects_per_bus = int(
        projects_per_bus.min()
    )

    maximum_projects_per_bus = int(
        projects_per_bus.max()
    )

    if not np.isclose(
        created_total_mw,
        total_capacity_mw,
        atol=capacity_tolerance_mw,
        rtol=0.0,
    ):
        raise RuntimeError(
            f"Distributed solar capacity mismatch after creating "
            f"pandapower elements: requested "
            f"{total_capacity_mw:.9f} MW, created "
            f"{created_total_mw:.9f} MW."
        )

    if (
        maximum_projects_per_bus
        - minimum_projects_per_bus
        > 1
    ):
        raise RuntimeError(
            "Distributed solar projects were not evenly "
            "distributed among the candidate busbars."
        )

    print(
        f"[generator_builder] Created "
        f"{project_count} non-voltage-controlling distributed "
        f"solar sgen elements on {bus_count} eligible "
        f"22 kV busbars."
    )

    print(
        f"[generator_builder] Distributed solar capacity: "
        f"requested={total_capacity_mw:.6f} MW, "
        f"created={created_total_mw:.6f} MW."
    )

    print(
        f"[generator_builder] Project capacity range: "
        f"{created_minimum_kw:.3f}-"
        f"{created_maximum_kw:.3f} kW."
    )

    print(
        f"[generator_builder] Projects per busbar: "
        f"minimum={minimum_projects_per_bus}, "
        f"maximum={maximum_projects_per_bus}, "
        f"random_seed={random_seed}, "
        f"season={season}, scaling=1.0."
    )

    gen_map = generator_map

    return gen_map


def build_behind_meter_mv_generators(
    net: pp.pandapowerNet,
    bus_map: dict[str, int],
    total_capacity_mw: float = 810.0,
    mean_project_kw: float = 250.0,
    std_project_kw: float = 150.0,
    min_project_kw: float = 0.0,
    max_project_kw: float = 2000.0,
    min_voltage_kv: float = 1.0,
    max_voltage_kv: float = 35.0,
    random_seed: int | None = None,
) -> dict[str, tuple[str, int]]:
    """
    Create behind-the-meter solar projects on MV busbars.

    Project characteristics
    -----------------------
    - Total default installed capacity: 810 MW.
    - Individual project sizes follow a truncated normal distribution.
    - Default distribution center: 250 kW.
    - Default standard deviation: 150 kW.
    - Individual capacity limits: above 0 and at most 2 MW.
    - Multiple projects may be connected to one busbar.
    - All projects are non-voltage-controlling sgen elements.
    - scaling is fixed at 1.0.

    Placement rule
    --------------
    Projects are placed only at in-service MV buses having positive
    in-service consumption.

    Consumption at a bus is calculated as:

        sum(load.p_mw * load.scaling)

    for in-service loads connected directly to that bus.

    Capacity is distributed proportionally to the available consumption
    headroom. Consequently, buses with higher consumption receive more
    behind-the-meter capacity, while the same approximate penetration
    ratio is maintained across all eligible buses.

    Existing in-service sgen elements tagged source="BTM_MV" are deducted
    from the consumption headroom. This also prevents a repeated call from
    assigning more behind-the-meter generation than the load can absorb.

    The following condition is checked for every eligible bus:

        existing BTM generation + new BTM generation < consumption

    Returns
    -------
    dict[str, tuple[str, int]]
        project_name -> ("sgen", sgen_index)
    """

    source_tag = "BTM_MV"
    capacity_tolerance_mw = 1e-9
    consumption_margin_mw = 1e-6
    minimum_positive_project_mw = 1e-9

    total_capacity_mw = float(total_capacity_mw)
    mean_project_mw = float(mean_project_kw) / 1000.0
    std_project_mw = float(std_project_kw) / 1000.0
    min_project_mw = float(min_project_kw) / 1000.0
    max_project_mw = float(max_project_kw) / 1000.0

    # --------------------------------------------------------------
    # Validate parameters
    # --------------------------------------------------------------
    if not np.isfinite(total_capacity_mw):
        raise ValueError(
            "total_capacity_mw must be finite."
        )

    if total_capacity_mw < 0.0:
        raise ValueError(
            "total_capacity_mw cannot be negative."
        )

    if total_capacity_mw == 0.0:
        print(
            "[generator_builder] Behind-the-meter requested "
            "capacity is 0 MW; no projects were created."
        )
        return {}

    if not np.isfinite(mean_project_mw):
        raise ValueError(
            "mean_project_kw must be finite."
        )

    if mean_project_mw <= 0.0:
        raise ValueError(
            "mean_project_kw must be greater than zero."
        )

    if not np.isfinite(std_project_mw):
        raise ValueError(
            "std_project_kw must be finite."
        )

    if std_project_mw <= 0.0:
        raise ValueError(
            "std_project_kw must be greater than zero."
        )

    if not np.isfinite(min_project_mw):
        raise ValueError(
            "min_project_kw must be finite."
        )

    if not np.isfinite(max_project_mw):
        raise ValueError(
            "max_project_kw must be finite."
        )

    if min_project_mw < 0.0:
        raise ValueError(
            "min_project_kw cannot be negative."
        )

    if max_project_mw <= 0.0:
        raise ValueError(
            "max_project_kw must be greater than zero."
        )

    if max_project_mw <= min_project_mw:
        raise ValueError(
            "max_project_kw must be greater than "
            "min_project_kw."
        )

    if not np.isfinite(min_voltage_kv):
        raise ValueError(
            "min_voltage_kv must be finite."
        )

    if not np.isfinite(max_voltage_kv):
        raise ValueError(
            "max_voltage_kv must be finite."
        )

    if max_voltage_kv <= min_voltage_kv:
        raise ValueError(
            "max_voltage_kv must be greater than "
            "min_voltage_kv."
        )

    rng = np.random.default_rng(random_seed)

    # Zero-capacity projects are not useful pandapower elements.
    effective_minimum_project_mw = max(
        min_project_mw,
        minimum_positive_project_mw,
    )

    # --------------------------------------------------------------
    # Build bus-name lookup
    # --------------------------------------------------------------
    bus_name_by_index = {
        int(bus_idx): bus_name
        for bus_name, bus_idx in bus_map.items()
    }

    # --------------------------------------------------------------
    # Calculate scaled in-service consumption at each bus
    # --------------------------------------------------------------
    consumption_by_bus: dict[int, float] = {}

    if net.load.empty:
        raise ValueError(
            "net.load is empty. Behind-the-meter projects require "
            "positive consumption at their connection busbars."
        )

    for load_idx, load in net.load.iterrows():
        try:
            bus_idx = int(load["bus"])
            p_mw = float(load["p_mw"])
        except (KeyError, TypeError, ValueError):
            continue

        if not np.isfinite(p_mw):
            continue

        if "in_service" in net.load.columns:
            in_service_value = load.get(
                "in_service",
                True,
            )

            in_service = (
                True
                if pd.isna(in_service_value)
                else bool(in_service_value)
            )
        else:
            in_service = True

        if not in_service:
            continue

        if "scaling" in net.load.columns:
            scaling_value = load.get(
                "scaling",
                1.0,
            )

            scaling = (
                1.0
                if pd.isna(scaling_value)
                else float(scaling_value)
            )
        else:
            scaling = 1.0

        if not np.isfinite(scaling):
            continue

        scaled_consumption_mw = p_mw * scaling

        if scaled_consumption_mw <= 0.0:
            continue

        consumption_by_bus[bus_idx] = (
            consumption_by_bus.get(bus_idx, 0.0)
            + scaled_consumption_mw
        )

    if not consumption_by_bus:
        raise ValueError(
            "No positive in-service scaled consumption was found "
            "in net.load."
        )

    # --------------------------------------------------------------
    # Calculate existing BTM generation at each bus
    # --------------------------------------------------------------
    existing_btm_by_bus: dict[int, float] = {}

    if (
        not net.sgen.empty
        and "source" in net.sgen.columns
    ):
        existing_btm_mask = (
            net.sgen["source"].eq(source_tag)
        )

        if "in_service" in net.sgen.columns:
            existing_btm_mask &= (
                net.sgen["in_service"]
                .fillna(True)
                .astype(bool)
            )

        existing_btm = net.sgen.loc[
            existing_btm_mask
        ]

        for sgen_idx, sgen in existing_btm.iterrows():
            try:
                bus_idx = int(sgen["bus"])
                p_mw = float(sgen["p_mw"])
            except (KeyError, TypeError, ValueError):
                continue

            if not np.isfinite(p_mw):
                continue

            if "scaling" in net.sgen.columns:
                scaling_value = sgen.get(
                    "scaling",
                    1.0,
                )

                scaling = (
                    1.0
                    if pd.isna(scaling_value)
                    else float(scaling_value)
                )
            else:
                scaling = 1.0

            if not np.isfinite(scaling):
                continue

            effective_generation_mw = p_mw * scaling

            if effective_generation_mw <= 0.0:
                continue

            existing_btm_by_bus[bus_idx] = (
                existing_btm_by_bus.get(
                    bus_idx,
                    0.0,
                )
                + effective_generation_mw
            )

    # --------------------------------------------------------------
    # Find eligible MV buses and their available headroom
    # --------------------------------------------------------------
    eligible_buses: list[
        tuple[int, str, float, float]
    ] = []

    for bus_idx in net.bus.index:
        try:
            integer_bus_idx = int(bus_idx)
            vn_kv = float(
                net.bus.at[bus_idx, "vn_kv"]
            )
        except (KeyError, TypeError, ValueError):
            continue

        if not np.isfinite(vn_kv):
            continue

        if "in_service" in net.bus.columns:
            bus_service_value = net.bus.at[
                bus_idx,
                "in_service",
            ]

            bus_in_service = (
                True
                if pd.isna(bus_service_value)
                else bool(bus_service_value)
            )
        else:
            bus_in_service = True

        if not bus_in_service:
            continue

        # MV default: greater than 1 kV and at most 35 kV.
        if not (
            vn_kv > min_voltage_kv
            and vn_kv <= max_voltage_kv
        ):
            continue

        consumption_mw = consumption_by_bus.get(
            integer_bus_idx,
            0.0,
        )

        if consumption_mw <= 0.0:
            continue

        existing_btm_mw = existing_btm_by_bus.get(
            integer_bus_idx,
            0.0,
        )

        headroom_mw = (
            consumption_mw
            - existing_btm_mw
            - consumption_margin_mw
        )

        if headroom_mw <= 0.0:
            continue

        bus_name = bus_name_by_index.get(
            integer_bus_idx
        )

        if bus_name is None:
            if "name" in net.bus.columns:
                stored_bus_name = net.bus.at[
                    bus_idx,
                    "name",
                ]

                bus_name = (
                    str(stored_bus_name)
                    if pd.notna(stored_bus_name)
                    else str(integer_bus_idx)
                )
            else:
                bus_name = str(integer_bus_idx)

        eligible_buses.append(
            (
                integer_bus_idx,
                bus_name,
                consumption_mw,
                headroom_mw,
            )
        )

    if not eligible_buses:
        raise ValueError(
            "No eligible in-service MV busbar has positive "
            "consumption headroom."
        )

    total_headroom_mw = sum(
        bus[3]
        for bus in eligible_buses
    )

    if total_capacity_mw > (
        total_headroom_mw
        + capacity_tolerance_mw
    ):
        raise ValueError(
            f"The requested {total_capacity_mw:.6f} MW of "
            f"behind-the-meter generation exceeds the total "
            f"available MV consumption headroom of "
            f"{total_headroom_mw:.6f} MW. Reduce the requested "
            f"capacity or increase the modelled consumption."
        )

        # --------------------------------------------------------------
    # Allocate aggregate capacity among usable buses
    # --------------------------------------------------------------
    # A bus can be used only if its available headroom can accommodate
    # at least one minimum-size project.
    usable_buses = [
        bus
        for bus in eligible_buses
        if bus[3] >= (
            effective_minimum_project_mw
            - capacity_tolerance_mw
        )
    ]

    if not usable_buses:
        raise ValueError(
            "No eligible MV bus has enough consumption headroom "
            f"for the minimum project size of "
            f"{min_project_kw:.3f} kW."
        )

    usable_headroom_mw = sum(
        bus[3]
        for bus in usable_buses
    )

    if total_capacity_mw > (
        usable_headroom_mw
        + capacity_tolerance_mw
    ):
        raise ValueError(
            f"The requested {total_capacity_mw:.6f} MW cannot "
            f"be placed while maintaining a minimum project size "
            f"of {min_project_kw:.3f} kW. MV buses capable of "
            f"hosting at least one project provide only "
            f"{usable_headroom_mw:.6f} MW of usable consumption "
            f"headroom."
        )

    # Iteratively remove buses whose proportional allocation would
    # be smaller than the minimum project size. Their capacity is
    # redistributed among the remaining buses.
    active_buses = usable_buses.copy()

    while True:
        active_headroom_mw = sum(
            bus[3]
            for bus in active_buses
        )

        if total_capacity_mw > (
            active_headroom_mw
            + capacity_tolerance_mw
        ):
            raise ValueError(
                f"The requested {total_capacity_mw:.6f} MW cannot "
                f"be distributed among MV buses without exceeding "
                f"their consumption. Remaining usable headroom is "
                f"{active_headroom_mw:.6f} MW."
            )

        penetration_ratio = (
            total_capacity_mw
            / active_headroom_mw
        )

        undersized_bus_positions = [
            position
            for position, bus in enumerate(active_buses)
            if (
                bus[3] * penetration_ratio
                < (
                    effective_minimum_project_mw
                    - capacity_tolerance_mw
                )
            )
        ]

        if not undersized_bus_positions:
            break

        undersized_positions_set = set(
            undersized_bus_positions
        )

        active_buses = [
            bus
            for position, bus in enumerate(active_buses)
            if position not in undersized_positions_set
        ]

        if not active_buses:
            raise ValueError(
                "No MV bus remains after enforcing the minimum "
                f"project size of {min_project_kw:.3f} kW."
            )

    # Each remaining bus gets a share proportional to its available
    # consumption headroom.
    bus_allocations: list[
        tuple[int, str, float, float, float]
    ] = []

    for (
        bus_idx,
        bus_name,
        consumption_mw,
        headroom_mw,
    ) in active_buses:
        allocated_mw = (
            headroom_mw
            * penetration_ratio
        )

        bus_allocations.append(
            (
                bus_idx,
                bus_name,
                consumption_mw,
                headroom_mw,
                allocated_mw,
            )
        )

    # --------------------------------------------------------------
    # Close floating-point allocation residual
    # --------------------------------------------------------------
    allocated_total_mw = sum(
        allocation[4]
        for allocation in bus_allocations
    )

    allocation_residual_mw = (
        total_capacity_mw
        - allocated_total_mw
    )

    if (
        abs(allocation_residual_mw)
        > capacity_tolerance_mw
    ):
        mutable_allocations = [
            list(allocation)
            for allocation in bus_allocations
        ]

        if allocation_residual_mw > 0.0:
            # Add the residual to the bus with the most spare
            # consumption headroom.
            adjustment_position = max(
                range(len(mutable_allocations)),
                key=lambda position: (
                    mutable_allocations[position][3]
                    - mutable_allocations[position][4]
                ),
            )

            available_headroom_mw = (
                mutable_allocations[
                    adjustment_position
                ][3]
                - mutable_allocations[
                    adjustment_position
                ][4]
            )

            if (
                available_headroom_mw
                + capacity_tolerance_mw
                < allocation_residual_mw
            ):
                raise RuntimeError(
                    "Insufficient bus headroom to close the "
                    "capacity-allocation residual."
                )

            mutable_allocations[
                adjustment_position
            ][4] += allocation_residual_mw

        else:
            # Remove the residual from a bus while keeping its
            # allocation large enough for at least one project.
            required_reduction_mw = abs(
                allocation_residual_mw
            )

            adjustable_positions = [
                position
                for position, allocation
                in enumerate(mutable_allocations)
                if (
                    allocation[4]
                    - effective_minimum_project_mw
                    >= (
                        required_reduction_mw
                        - capacity_tolerance_mw
                    )
                )
            ]

            if not adjustable_positions:
                raise RuntimeError(
                    "No bus allocation can absorb the negative "
                    "floating-point residual while preserving the "
                    "minimum project size."
                )

            adjustment_position = max(
                adjustable_positions,
                key=lambda position: (
                    mutable_allocations[position][4]
                ),
            )

            mutable_allocations[
                adjustment_position
            ][4] -= required_reduction_mw

        bus_allocations = [
            tuple(allocation)
            for allocation in mutable_allocations
        ]

    # --------------------------------------------------------------
    # Validate aggregate bus allocations
    # --------------------------------------------------------------
    final_allocated_total_mw = sum(
        allocation[4]
        for allocation in bus_allocations
    )

    if not np.isclose(
        final_allocated_total_mw,
        total_capacity_mw,
        atol=capacity_tolerance_mw,
        rtol=0.0,
    ):
        raise RuntimeError(
            f"Bus allocation mismatch: requested "
            f"{total_capacity_mw:.9f} MW, allocated "
            f"{final_allocated_total_mw:.9f} MW."
        )

    for (
        bus_idx,
        bus_name,
        consumption_mw,
        headroom_mw,
        allocated_mw,
    ) in bus_allocations:
        if allocated_mw < (
            effective_minimum_project_mw
            - capacity_tolerance_mw
        ):
            raise RuntimeError(
                f"Bus {bus_idx} ('{bus_name}') received only "
                f"{allocated_mw * 1000.0:.6f} kW, below the "
                f"minimum project size of "
                f"{min_project_kw:.6f} kW."
            )

        if allocated_mw > (
            headroom_mw
            + capacity_tolerance_mw
        ):
            raise RuntimeError(
                f"Bus {bus_idx} ('{bus_name}') was allocated "
                f"{allocated_mw:.9f} MW but has only "
                f"{headroom_mw:.9f} MW of consumption headroom."
            )

    # --------------------------------------------------------------
    # Generate a bounded normal portfolio for one bus
    # --------------------------------------------------------------
    def _generate_bus_projects(
        allocated_mw: float,
    ) -> np.ndarray:
        """
        Split one bus's allocation into normally distributed projects
        while preserving the exact aggregate allocation.
        """

                # The allocation must be able to accommodate at least one
        # minimum-size project.
        if allocated_mw < (
            effective_minimum_project_mw
            - capacity_tolerance_mw
        ):
            raise ValueError(
                f"Bus allocation of "
                f"{allocated_mw * 1000.0:.6f} kW is smaller "
                f"than the minimum project size of "
                f"{min_project_kw:.6f} kW."
            )

        # At least this many projects are required to keep every
        # individual project at or below max_project_mw.
        minimum_project_count = max(
            1,
            int(
                np.ceil(
                    allocated_mw
                    / max_project_mw
                    - 1e-12
                )
            ),
        )

        # No more than this many projects are possible without
        # placing at least one project below min_project_mw.
        maximum_project_count = int(
            np.floor(
                allocated_mw
                / effective_minimum_project_mw
                + 1e-12
            )
        )

        if (
            maximum_project_count
            < minimum_project_count
        ):
            raise ValueError(
                f"Bus allocation of "
                f"{allocated_mw * 1000.0:.6f} kW cannot be "
                f"divided into projects between "
                f"{min_project_kw:.6f} and "
                f"{max_project_kw:.6f} kW."
            )

        # Select a project count that gives approximately the requested
        # 250 kW mean, bounded by the feasible count interval.
        estimated_project_count = int(
            round(
                allocated_mw
                / mean_project_mw
            )
        )

        project_count = int(
            np.clip(
                estimated_project_count,
                minimum_project_count,
                maximum_project_count,
            )
        )

        if project_count < 1:
            raise RuntimeError(
                f"Internal error: project_count={project_count} "
                f"for bus allocation "
                f"{allocated_mw:.9f} MW."
            )

        # Draw from the normal distribution and reject values outside
        # the requested project-size interval.
        raw_capacities = np.empty(
            project_count,
            dtype=float,
        )

        for project_position in range(
            project_count
        ):
            for _ in range(10000):
                sampled_mw = float(
                    rng.normal(
                        mean_project_mw,
                        std_project_mw,
                    )
                )

                if (
                    sampled_mw
                    > effective_minimum_project_mw
                    and sampled_mw
                    <= max_project_mw
                ):
                    raw_capacities[
                        project_position
                    ] = sampled_mw
                    break
            else:
                raw_capacities[
                    project_position
                ] = min(
                    max(
                        mean_project_mw,
                        effective_minimum_project_mw,
                    ),
                    max_project_mw,
                )

        # Find a common offset that makes the sum exact while preserving
        # the lower and upper project-size limits.
        lower_offset = (
            effective_minimum_project_mw
            - float(raw_capacities.max())
        )

        upper_offset = (
            max_project_mw
            - float(raw_capacities.min())
        )

        capacities = raw_capacities.copy()

        for _ in range(100):
            offset = (
                lower_offset + upper_offset
            ) / 2.0

            capacities = np.clip(
                raw_capacities + offset,
                effective_minimum_project_mw,
                max_project_mw,
            )

            if float(
                capacities.sum()
            ) < allocated_mw:
                lower_offset = offset
            else:
                upper_offset = offset

        capacities = np.clip(
            raw_capacities
            + (
                lower_offset + upper_offset
            )
            / 2.0,
            effective_minimum_project_mw,
            max_project_mw,
        )

        residual_mw = (
            allocated_mw
            - float(capacities.sum())
        )

        if (
            abs(residual_mw)
            > capacity_tolerance_mw
        ):
            for project_position in rng.permutation(
                project_count
            ):
                if (
                    abs(residual_mw)
                    <= capacity_tolerance_mw
                ):
                    break

                if residual_mw > 0.0:
                    available_mw = (
                        max_project_mw
                        - capacities[project_position]
                    )

                    adjustment_mw = min(
                        residual_mw,
                        available_mw,
                    )

                    capacities[
                        project_position
                    ] += adjustment_mw

                    residual_mw -= adjustment_mw

                else:
                    available_mw = (
                        capacities[project_position]
                        - effective_minimum_project_mw
                    )

                    adjustment_mw = min(
                        abs(residual_mw),
                        available_mw,
                    )

                    capacities[
                        project_position
                    ] -= adjustment_mw

                    residual_mw += adjustment_mw

        if not np.isclose(
            float(capacities.sum()),
            allocated_mw,
            atol=capacity_tolerance_mw,
            rtol=0.0,
        ):
            raise RuntimeError(
                f"Could not split {allocated_mw:.9f} MW "
                f"into valid project capacities."
            )

        return capacities

    # --------------------------------------------------------------
    # Create sgen elements
    # --------------------------------------------------------------
    generator_map: dict[
        str,
        tuple[str, int],
    ] = {}

    created_indices: list[int] = []
    created_by_bus: dict[int, float] = {}
    project_counter = 0

    # Randomize creation order without changing aggregate bus allocation.
    allocation_order = rng.permutation(
        len(bus_allocations)
    )

    for allocation_position in allocation_order:
        (
            bus_idx,
            bus_name,
            consumption_mw,
            headroom_mw,
            allocated_mw,
        ) = bus_allocations[
            int(allocation_position)
        ]

        project_capacities = (
            _generate_bus_projects(
                allocated_mw
            )
        )

        rng.shuffle(project_capacities)

        for (
            local_project_number,
            project_capacity_mw,
        ) in enumerate(
            project_capacities,
            start=1,
        ):
            project_counter += 1

            project_name = (
                f"BTM_MV_{project_counter:05d}_"
                f"{bus_idx}_"
                f"{local_project_number:03d}"
            )

            sgen_idx = pp.create_sgen(
                net,
                bus=bus_idx,
                p_mw=float(
                    project_capacity_mw
                ),
                q_mvar=0.0,
                sn_mva=float(
                    project_capacity_mw
                ),
                scaling=1.0,
                name=project_name,
                type=TECHNOLOGY_SOLAR,
                in_service=True,
            )

            net.sgen.at[
                sgen_idx,
                "technology",
            ] = TECHNOLOGY_SOLAR

            net.sgen.at[
                sgen_idx,
                "source",
            ] = source_tag

            net.sgen.at[
                sgen_idx,
                "voltage_level",
            ] = f"{net.bus.at[bus_idx, 'vn_kv']} kV"

            net.sgen.at[
                sgen_idx,
                "project_capacity_kw",
            ] = (
                float(project_capacity_mw)
                * 1000.0
            )

            net.sgen.at[
                sgen_idx,
                "connection_bus_name",
            ] = bus_name

            net.sgen.at[
                sgen_idx,
                "bus_consumption_mw",
            ] = consumption_mw

            net.sgen.at[
                sgen_idx,
                "btm_bus_allocation_mw",
            ] = allocated_mw

            generator_map[
                project_name
            ] = (
                "sgen",
                sgen_idx,
            )

            created_indices.append(
                sgen_idx
            )

            created_by_bus[bus_idx] = (
                created_by_bus.get(
                    bus_idx,
                    0.0,
                )
                + float(project_capacity_mw)
            )

    # --------------------------------------------------------------
    # Final validation
    # --------------------------------------------------------------
    created_sgens = net.sgen.loc[
        created_indices
    ]

    created_total_mw = float(
        created_sgens["p_mw"].sum()
    )

    if not np.isclose(
        created_total_mw,
        total_capacity_mw,
        atol=capacity_tolerance_mw,
        rtol=0.0,
    ):
        raise RuntimeError(
            f"Behind-the-meter capacity mismatch: requested "
            f"{total_capacity_mw:.9f} MW, created "
            f"{created_total_mw:.9f} MW."
        )

    violations: list[str] = []

    for (
        bus_idx,
        new_generation_mw,
    ) in created_by_bus.items():
        consumption_mw = (
            consumption_by_bus[bus_idx]
        )

        existing_btm_mw = (
            existing_btm_by_bus.get(
                bus_idx,
                0.0,
            )
        )

        total_btm_mw = (
            existing_btm_mw
            + new_generation_mw
        )

        if total_btm_mw >= consumption_mw:
            violations.append(
                f"bus={bus_idx}, "
                f"consumption={consumption_mw:.9f} MW, "
                f"BTM={total_btm_mw:.9f} MW"
            )

    if violations:
        raise RuntimeError(
            "Behind-the-meter generation is not below "
            "consumption at the following buses: "
            + "; ".join(violations)
        )

    minimum_project_kw_created = float(
        created_sgens["p_mw"].min()
        * 1000.0
    )

    maximum_project_kw_created = float(
        created_sgens["p_mw"].max()
        * 1000.0
    )

    mean_project_kw_created = float(
        created_sgens["p_mw"].mean()
        * 1000.0
    )

    used_bus_count = int(
        created_sgens["bus"].nunique()
    )

    print(
        f"[generator_builder] Created "
        f"{len(created_indices)} behind-the-meter MV solar "
        f"sgen elements on {used_bus_count} busbars."
    )

    print(
        f"[generator_builder] BTM capacity: "
        f"requested={total_capacity_mw:.6f} MW, "
        f"created={created_total_mw:.6f} MW."
    )

    print(
        f"[generator_builder] BTM project sizes: "
        f"minimum={minimum_project_kw_created:.3f} kW, "
        f"mean={mean_project_kw_created:.3f} kW, "
        f"maximum={maximum_project_kw_created:.3f} kW."
    )

    print(
        f"[generator_builder] MV consumption headroom: "
        f"{total_headroom_mw:.6f} MW; "
        f"portfolio penetration ratio="
        f"{penetration_ratio:.6f}; "
        f"random_seed={random_seed}."
    )

    gen_map = generator_map

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
