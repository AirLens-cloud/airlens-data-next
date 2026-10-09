"""
Configuration for policy collection pipeline.

All constants, paths, and field mappings in one place.
"""
from pathlib import Path

# ── Paths ──
ROOT = Path(__file__).resolve().parent.parent.parent.parent  # repo root (scripts/etl/collect_policies → ../../../..)
DATA_DIR = ROOT / "Data" / "6-policy-analysis"
REGISTRY_DIR = DATA_DIR / "registry"
BY_SOURCE_DIR = REGISTRY_DIR / "by_source"
BY_COUNTRY_DIR = REGISTRY_DIR / "by_country"
FRONTEND_DIR = ROOT / "apps/web" / "public" / "data"

# ── WHO 2021 Air Quality Guidelines (reference values) ──
WHO_GUIDELINES: dict[str, dict[str, float]] = {
    "PM2.5": {"annual": 5.0, "24h": 15.0},
    "PM10": {"annual": 15.0, "24h": 45.0},
    "NO2": {"annual": 10.0, "1h": 200.0},
    "SO2": {"24h": 40.0, "10min": 500.0},
    "O3": {"8h": 100.0, "peak": 160.0},
    "CO": {"24h": 4.0, "1h": 35.0},
}

# ── Policy types ──
POLICY_TYPES = [
    "law",
    "regulation",
    "standard",
    "plan",
    "strategy",
    "guideline",
    "decree",
    "order",
    "directive",
    "act",
]

# ── Policy status ──
POLICY_STATUSES = [
    "in_force",
    "enacted",
    "amended",
    "repealed",
    "proposed",
    "expired",
]

# ── Sectors ──
SECTORS = [
    "transport",
    "industry",
    "energy",
    "agriculture",
    "waste",
    "household",
    "construction",
    "mining",
    "cross-sector",
]

# ── Pollutants ──
POLLUTANTS = [
    "PM2.5", "PM10", "NO2", "SO2", "O3", "CO",
    "VOC", "NH3", "BC", "Pb", "benzene",
]

# ── UNEP Regions ──
REGIONS: dict[str, str] = {
    # East Asia
    "CN": "East Asia", "JP": "East Asia", "KR": "East Asia",
    "MN": "East Asia", "TW": "East Asia", "HK": "East Asia",
    # Southeast Asia
    "ID": "Southeast Asia", "TH": "Southeast Asia", "VN": "Southeast Asia",
    "PH": "Southeast Asia", "MY": "Southeast Asia", "SG": "Southeast Asia",
    "MM": "Southeast Asia", "KH": "Southeast Asia", "LA": "Southeast Asia",
    "BN": "Southeast Asia",
    # South Asia
    "IN": "South Asia", "BD": "South Asia", "PK": "South Asia",
    "LK": "South Asia", "NP": "South Asia", "AF": "South Asia",
    "BT": "South Asia", "MV": "South Asia",
    # Central Asia
    "KZ": "Central Asia", "UZ": "Central Asia", "TM": "Central Asia",
    "KG": "Central Asia", "TJ": "Central Asia",
    # Europe
    "GB": "Europe", "DE": "Europe", "FR": "Europe", "IT": "Europe",
    "ES": "Europe", "PT": "Europe", "NL": "Europe", "BE": "Europe",
    "AT": "Europe", "CH": "Europe", "SE": "Europe", "NO": "Europe",
    "DK": "Europe", "FI": "Europe", "IE": "Europe", "PL": "Europe",
    "CZ": "Europe", "SK": "Europe", "HU": "Europe", "RO": "Europe",
    "BG": "Europe", "HR": "Europe", "SI": "Europe", "LT": "Europe",
    "LV": "Europe", "EE": "Europe", "LU": "Europe", "MT": "Europe",
    "CY": "Europe", "GR": "Europe", "AL": "Europe", "BA": "Europe",
    "RS": "Europe", "ME": "Europe", "MK": "Europe", "XK": "Europe",
    "MD": "Europe", "UA": "Europe", "BY": "Europe", "IS": "Europe",
    "RU": "Europe",
    # North America
    "US": "North America", "CA": "North America", "MX": "North America",
    # Central America & Caribbean
    "GT": "Central America & Caribbean", "HN": "Central America & Caribbean",
    "SV": "Central America & Caribbean", "NI": "Central America & Caribbean",
    "CR": "Central America & Caribbean", "PA": "Central America & Caribbean",
    "BZ": "Central America & Caribbean", "CU": "Central America & Caribbean",
    "JM": "Central America & Caribbean", "HT": "Central America & Caribbean",
    "DO": "Central America & Caribbean", "TT": "Central America & Caribbean",
    "BB": "Central America & Caribbean", "BS": "Central America & Caribbean",
    "GD": "Central America & Caribbean",
    # South America
    "BR": "South America", "AR": "South America", "CL": "South America",
    "CO": "South America", "PE": "South America", "VE": "South America",
    "EC": "South America", "BO": "South America", "PY": "South America",
    "UY": "South America", "GY": "South America", "SR": "South America",
    # Middle East
    "AE": "Middle East", "SA": "Middle East", "IL": "Middle East",
    "TR": "Middle East", "IQ": "Middle East", "IR": "Middle East",
    "JO": "Middle East", "LB": "Middle East", "KW": "Middle East",
    "BH": "Middle East", "QA": "Middle East", "OM": "Middle East",
    "YE": "Middle East", "SY": "Middle East", "PS": "Middle East",
    # Africa
    "ZA": "Africa", "NG": "Africa", "EG": "Africa", "KE": "Africa",
    "ET": "Africa", "GH": "Africa", "TZ": "Africa", "UG": "Africa",
    "RW": "Africa", "SN": "Africa", "CI": "Africa", "CM": "Africa",
    "MA": "Africa", "TN": "Africa", "DZ": "Africa", "LY": "Africa",
    "SD": "Africa", "AO": "Africa", "MZ": "Africa", "ZW": "Africa",
    "BW": "Africa", "NA": "Africa", "MW": "Africa", "ZM": "Africa",
    "MG": "Africa", "CD": "Africa", "CG": "Africa", "GA": "Africa",
    "BF": "Africa", "ML": "Africa", "NE": "Africa", "TD": "Africa",
    "GN": "Africa", "BJ": "Africa", "TG": "Africa", "SL": "Africa",
    "LR": "Africa", "MR": "Africa", "ER": "Africa", "DJ": "Africa",
    "SO": "Africa", "GM": "Africa", "GW": "Africa", "CV": "Africa",
    "KM": "Africa", "ST": "Africa", "SC": "Africa", "MU": "Africa",
    "SS": "Africa", "CF": "Africa", "GQ": "Africa", "BI": "Africa",
    "LS": "Africa", "SZ": "Africa",
    # Oceania
    "AU": "Oceania", "NZ": "Oceania", "FJ": "Oceania",
    "PG": "Oceania", "WS": "Oceania", "TO": "Oceania",
    "VU": "Oceania", "SB": "Oceania",
}

# ── ISO 3166-1 alpha-3 to alpha-2 mapping (common ones) ──
ISO3_TO_ISO2: dict[str, str] = {
    "AFG": "AF", "ALB": "AL", "DZA": "DZ", "AGO": "AO", "ARG": "AR",
    "ARM": "AM", "AUS": "AU", "AUT": "AT", "AZE": "AZ", "BHS": "BS",
    "BHR": "BH", "BGD": "BD", "BRB": "BB", "BLR": "BY", "BEL": "BE",
    "BLZ": "BZ", "BEN": "BJ", "BTN": "BT", "BOL": "BO", "BIH": "BA",
    "BWA": "BW", "BRA": "BR", "BRN": "BN", "BGR": "BG", "BFA": "BF",
    "BDI": "BI", "KHM": "KH", "CMR": "CM", "CAN": "CA", "CPV": "CV",
    "CAF": "CF", "TCD": "TD", "CHL": "CL", "CHN": "CN", "COL": "CO",
    "COM": "KM", "COG": "CG", "COD": "CD", "CRI": "CR", "CIV": "CI",
    "HRV": "HR", "CUB": "CU", "CYP": "CY", "CZE": "CZ", "DNK": "DK",
    "DJI": "DJ", "DOM": "DO", "ECU": "EC", "EGY": "EG", "SLV": "SV",
    "GNQ": "GQ", "ERI": "ER", "EST": "EE", "SWZ": "SZ", "ETH": "ET",
    "FJI": "FJ", "FIN": "FI", "FRA": "FR", "GAB": "GA", "GMB": "GM",
    "GEO": "GE", "DEU": "DE", "GHA": "GH", "GRC": "GR", "GRD": "GD",
    "GTM": "GT", "GIN": "GN", "GNB": "GW", "GUY": "GY", "HTI": "HT",
    "HND": "HN", "HUN": "HU", "ISL": "IS", "IND": "IN", "IDN": "ID",
    "IRN": "IR", "IRQ": "IQ", "IRL": "IE", "ISR": "IL", "ITA": "IT",
    "JAM": "JM", "JPN": "JP", "JOR": "JO", "KAZ": "KZ", "KEN": "KE",
    "KWT": "KW", "KGZ": "KG", "LAO": "LA", "LVA": "LV", "LBN": "LB",
    "LSO": "LS", "LBR": "LR", "LBY": "LY", "LTU": "LT", "LUX": "LU",
    "MDG": "MG", "MWI": "MW", "MYS": "MY", "MDV": "MV", "MLI": "ML",
    "MLT": "MT", "MRT": "MR", "MUS": "MU", "MEX": "MX", "MDA": "MD",
    "MNG": "MN", "MNE": "ME", "MAR": "MA", "MOZ": "MZ", "MMR": "MM",
    "NAM": "NA", "NPL": "NP", "NLD": "NL", "NZL": "NZ", "NIC": "NI",
    "NER": "NE", "NGA": "NG", "MKD": "MK", "NOR": "NO", "OMN": "OM",
    "PAK": "PK", "PSE": "PS", "PAN": "PA", "PNG": "PG", "PRY": "PY",
    "PER": "PE", "PHL": "PH", "POL": "PL", "PRT": "PT", "QAT": "QA",
    "ROU": "RO", "RUS": "RU", "RWA": "RW", "SAU": "SA", "SEN": "SN",
    "SRB": "RS", "SYC": "SC", "SLE": "SL", "SGP": "SG", "SVK": "SK",
    "SVN": "SI", "SLB": "SB", "SOM": "SO", "ZAF": "ZA", "SSD": "SS",
    "ESP": "ES", "LKA": "LK", "SDN": "SD", "SUR": "SR", "SWE": "SE",
    "CHE": "CH", "SYR": "SY", "TWN": "TW", "TJK": "TJ", "TZA": "TZ",
    "THA": "TH", "TLS": "TL", "TGO": "TG", "TON": "TO", "TTO": "TT",
    "TUN": "TN", "TUR": "TR", "TKM": "TM", "UGA": "UG", "UKR": "UA",
    "ARE": "AE", "GBR": "GB", "USA": "US", "URY": "UY", "UZB": "UZ",
    "VUT": "VU", "VEN": "VE", "VNM": "VN", "YEM": "YE", "ZMB": "ZM",
    "ZWE": "ZW", "WSM": "WS", "STP": "ST", "HKG": "HK", "SGS": "GS",
    "XKX": "XK",
}
