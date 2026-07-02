"""
config.py — central constants for the pandapower builder package.
All magic numbers live here.
"""

# ── Seasonal rating column suffixes in the Excel branch sheet ─────────────────
RATING_SEASON_SUMMER = "summer"   # used as dict key and column name suffix
RATING_SEASON_WINTER = "winter"

# ── Default pandapower load/sgen/gen scaling ──────────────────────────────────
DEFAULT_LOAD_SCALING = 1.0
DEFAULT_GEN_SCALING  = 1.0

# ── Technology labels (must match Generators sheet "Technology" column) ───────
TECHNOLOGY_WIND    = "WINDONSHORE"
TECHNOLOGY_SOLAR   = ("SOLARPHOTOVO", "ROOFTOPPV", "OTHERRES")
TECHNOLOGY_BATTERY = "BATTERYSTRG"
TECHNOLOGY_HYDRO   = ("HYDRORANPOND", "HYDRORESERV")
TECHNOLOGY_GAS     = ("GASBIO", "GASCCGTNEW", "GASCCGTOLD2", "GASCCGTPRES1", "GASCCGTPRES2", "GASCONVOLD1", "GASOCGTNEW", "OTHERNONRES")
TECHNOLOGY_COAL    = ("HARDCOALBF", "HARDCOALOLD1", "LIGHTOIL")
TECHNOLOGY_NUCLEAR = "NUCLEAR"

# ── Technology labels for voltage control
VOLTAGE_CONTROL_TECHS = {
    "BATTERYSTRG",
    "GASOCGTNEW", "GASCCGTPRES1", "GASCCGTPRES2",
    "GASCCGTNEW", "GASCCGTOLD2", "GASCONVOLD1", "GASBIO",
    "HARDCOALBF", "HARDCOALOLD1",
    "LIGHTOIL",
    "NUCLEAR",
    "OTHERNONRES"
}

# Default per-technology scaling factors: {technology: {season: scale}}
TECH_SCALING_DEFAULTS: dict[str, dict[str, float]] = {
    TECHNOLOGY_WIND:    {RATING_SEASON_SUMMER: 0.85, RATING_SEASON_WINTER: 0.90},
    TECHNOLOGY_SOLAR:   {RATING_SEASON_SUMMER: 0.4, RATING_SEASON_WINTER: 0.30},
    TECHNOLOGY_BATTERY: {RATING_SEASON_SUMMER: 1.00, RATING_SEASON_WINTER: 1.00},
    TECHNOLOGY_HYDRO:   {RATING_SEASON_SUMMER: 0.80, RATING_SEASON_WINTER: 0.95},
    TECHNOLOGY_GAS:     {RATING_SEASON_SUMMER: 1.00, RATING_SEASON_WINTER: 1.00},
}

# ── Excel sheet names ─────────────────────────────────────────────────────────
SHEET_BUSBARS      = "Busz"
SHEET_BRANCHES     = "Vezeték"
SHEET_2W_TRANSFORMERS = "2 Transzformátor"
SHEET_3W_TRANSFORMERS = "3 Transzformátor"
SHEET_LOADS        = "Fogyasztás"
SHEET_GENERATORS   = "Termelés"

# ── Required columns per sheet ────────────────────────────────────────────────
REQUIRED_COLUMNS = {
    SHEET_BUSBARS: [
        "Azonosító", "Zóna", "Típus", "U"
    ],
    SHEET_BRANCHES: [
         "Végpont1", "Végpont2", "Ág", "Engedélyesi Azonosító", "Bent",
        "R", "X", "C",
        "Tulajdonos",
        "Inyár", "Itél",
        "Hossz"
    ],
    SHEET_2W_TRANSFORMERS: [
    "Primer", "Szekunder",
    "Hely", "Engedélyesi azonosító",
    "Bent", "Kapcsolási", 
    "Upn", "Usn", 
    "Sn", "Eps", "Pröv", "Pürj", "Qürj",
    "N", "Szabályzott oldal"
    ],
    SHEET_3W_TRANSFORMERS: [
    "Primer", "Szekunder", "Tercier",
    "Hely", "Engedélyesi azonosító",
    "Bent", "Kapcsolási", 
    "Upn", "Usn", "Utn",
    "Sps", "Spt", "Sst", 
    "Eps", "Ept", "Est",
    "Prps", "Prpt", "Prst",
    "Pürj", "Qürj",
    ],
    SHEET_LOADS: [
    "Végpont", "Hely", "Engedélyesi azonosító", "Bent", "Zóna", "Sr", "Sx", 
    ],
    SHEET_GENERATORS: [
    "Végpont", "Hely", "Engedélyesi azonosító", "Bent", "MVA", "Pmax", "Pmin", "Qmax", "Qmin", "P", "Q", "Szabpont", "Uszab", "Technológia"
    ]   
}
