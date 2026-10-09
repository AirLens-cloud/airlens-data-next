#!/usr/bin/env python3
"""
collect_who_standards.py — Compile national ambient air quality standards.

Sources:
  - WHO Air Quality Standards Database (2025, 3rd edition, ~140 countries)
  - Kutlar Joss et al. (2017) "Time to harmonize national ambient air quality standards"
  - WHO Global Air Quality Guidelines (2021)
  - Smart Air Filters global standards compilation
  - Individual country EPA/Ministry of Environment official publications

Output: Data/6-policy-analysis/registry/by_source/who_standards.json

Usage:
  .venv/bin/python scripts/collect_policies/collect_who_standards.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # repo root (scripts/etl/collect_policies → ../../../..)
sys.path.insert(0, str(ROOT / "scripts" / "etl"))

from collect_policies.config import BY_SOURCE_DIR, REGIONS, WHO_GUIDELINES


def build_standards_database() -> list[dict]:
    """Build comprehensive national AQ standards from published sources.

    Values sourced from:
    - WHO AQ Standards DB 2025 (140 countries)
    - Kutlar Joss et al. (2017) Int J Public Health
    - Official government publications
    All values in ug/m3 unless noted.
    """

    # (country_code, country_name, standards_dict, law_name, year)
    # standards_dict: {pollutant: {period: value}}
    raw_data: list[tuple[str, str, dict, str, int]] = [
        # ── East Asia ──
        ("KR", "South Korea", {
            "PM2.5": {"annual": 15, "24h": 35},
            "PM10": {"annual": 50, "24h": 100},
            "NO2": {"annual": 30, "1h": 100},
            "SO2": {"annual": 20, "1h": 150},
            "O3": {"8h": 60},
            "CO": {"8h": 9000, "1h": 25000},
        }, "Framework Act on Environmental Policy / Seasonal Fine Dust Management", 2019),

        ("JP", "Japan", {
            "PM2.5": {"annual": 15, "24h": 35},
            "PM10": {"annual": 100, "24h": 200},  # SPM standard
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 40, "1h": 100},
            "O3": {"1h": 120},
            "CO": {"8h": 20000},
        }, "Air Pollution Control Act", 1968),

        ("CN", "China", {
            "PM2.5": {"annual": 35, "24h": 75},
            "PM10": {"annual": 70, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 60, "24h": 150},
            "O3": {"8h": 160, "1h": 200},
            "CO": {"24h": 4000},
        }, "Ambient Air Quality Standards (GB 3095-2012) / Air Pollution Prevention and Control Action Plan", 2013),

        ("TW", "Taiwan", {
            "PM2.5": {"annual": 15, "24h": 35},
            "PM10": {"annual": 50, "24h": 100},
            "NO2": {"annual": 30, "1h": 100},
            "SO2": {"annual": 30, "24h": 75},
            "O3": {"8h": 60},
            "CO": {"8h": 9000},
        }, "Air Pollution Control Act", 2018),

        ("MN", "Mongolia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 100},
            "NO2": {"annual": 30, "1h": 200},
            "SO2": {"annual": 10, "24h": 50},
            "O3": {"8h": 100},
        }, "Law on Air / National Program on Reducing Air Pollution", 2017),

        # ── Southeast Asia ──
        ("TH", "Thailand", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 120},
            "NO2": {"annual": 40, "1h": 170},
            "SO2": {"annual": 100, "24h": 300},
            "O3": {"1h": 200},
            "CO": {"1h": 30000},
        }, "Enhancement and Conservation of National Environmental Quality Act / NAAQS Notification", 2023),

        ("VN", "Vietnam", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
            "O3": {"1h": 200},
            "CO": {"1h": 30000},
        }, "Law on Environmental Protection / QCVN 05:2023/BTNMT", 2023),

        ("ID", "Indonesia", {
            "PM2.5": {"annual": 15, "24h": 55},
            "PM10": {"annual": 40, "24h": 75},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 45, "24h": 150},
            "O3": {"1h": 150},
            "CO": {"1h": 30000},
        }, "Government Regulation No. 22/2021 on Environmental Protection", 2021),

        ("PH", "Philippines", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 60, "24h": 150},
            "NO2": {"annual": 60, "1h": 150},
            "SO2": {"annual": 80, "24h": 180},
            "O3": {"1h": 140},
            "CO": {"8h": 10000},
        }, "Clean Air Act (RA 8749) / DAO 2021-19", 1999),

        ("MY", "Malaysia", {
            "PM2.5": {"annual": 15, "24h": 35},
            "PM10": {"annual": 40, "24h": 100},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 40, "24h": 105},
            "O3": {"8h": 100},
            "CO": {"8h": 10000},
        }, "Environmental Quality (Clean Air) Regulations 2014 / NAAQS 2020", 2020),

        ("SG", "Singapore", {
            "PM2.5": {"annual": 12, "24h": 37.5},
            "PM10": {"annual": 20, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 15, "24h": 50},
            "O3": {"8h": 100},
            "CO": {"8h": 10000},
        }, "Environmental Protection and Management Act / Singapore Green Plan 2030", 2021),

        ("MM", "Myanmar", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
        }, "National Environmental Quality (Emission) Guidelines", 2015),

        ("KH", "Cambodia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 60, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 300},
        }, "Sub-Decree on Air Pollution Control", 2000),

        # ── South Asia ──
        ("IN", "India", {
            "PM2.5": {"annual": 40, "24h": 60},
            "PM10": {"annual": 60, "24h": 100},
            "NO2": {"annual": 40, "24h": 80},
            "SO2": {"annual": 50, "24h": 80},
            "O3": {"8h": 100},
            "CO": {"8h": 4000},
        }, "National Clean Air Programme (NCAP) / NAAQS 2009", 2019),

        ("BD", "Bangladesh", {
            "PM2.5": {"annual": 15, "24h": 65},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"8h": 160},
            "CO": {"8h": 10000},
        }, "Environment Conservation Rules / Clean Air Act (proposed)", 2019),

        ("PK", "Pakistan", {
            "PM2.5": {"annual": 15, "24h": 35},
            "PM10": {"annual": 120, "24h": 150},
            "NO2": {"annual": 40, "24h": 80},
            "SO2": {"annual": 80, "24h": 120},
            "O3": {"1h": 130},
            "CO": {"8h": 5000},
        }, "National Environmental Quality Standards (NEQS) / Pakistan Clean Air Act 2024", 2024),

        ("NP", "Nepal", {
            "PM2.5": {"annual": 20, "24h": 40},
            "PM10": {"annual": 40, "24h": 100},
            "NO2": {"annual": 40, "24h": 80},
            "SO2": {"annual": 50, "24h": 70},
        }, "National Ambient Air Quality Standards", 2012),

        ("LK", "Sri Lanka", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 100},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 80},
            "O3": {"8h": 100},
        }, "National Environmental Act / Ambient Air Quality Standards", 2019),

        # ── Europe (EU Directive 2008/50/EC + NEC Directive + national) ──
        ("DE", "Germany", {
            "PM2.5": {"annual": 25, "24h": 50},  # EU limit (new EU: 10 by 2030)
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "Federal Immission Control Act (BImSchG) / EU AAQD", 2010),

        ("GB", "United Kingdom", {
            "PM2.5": {"annual": 20, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "Environment Act 2021 / Clean Air Strategy 2019", 2021),

        ("FR", "France", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "Plan National de Réduction des Émissions de Polluants Atmosphériques (PREPA)", 2017),

        ("IT", "Italy", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "D.Lgs 155/2010 (EU AAQD transposition)", 2010),

        ("ES", "Spain", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "Royal Decree 102/2011 (EU AAQD transposition)", 2011),

        ("NL", "Netherlands", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Clean Air Agreement (Schone Lucht Akkoord)", 2020),

        ("SE", "Sweden", {
            "PM2.5": {"annual": 20, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 100, "1h": 200},
            "O3": {"8h": 120},
        }, "Environmental Code (Miljöbalken) / EU AAQD", 2010),

        ("NO", "Norway", {
            "PM2.5": {"annual": 15, "24h": 25},
            "PM10": {"annual": 25, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Pollution Control Act / Regulation on Local Air Quality (Forurensningsforskriften)", 2004),

        ("DK", "Denmark", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Environmental Protection Act / EU AAQD transposition", 2010),

        ("FI", "Finland", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Government Decree on Air Quality (79/2017)", 2017),

        ("PL", "Poland", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Environmental Protection Law / Clean Air Programme (Czyste Powietrze)", 2018),

        ("CZ", "Czech Republic", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Act on Protection of Air (201/2012 Sb.)", 2012),

        ("AT", "Austria", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 30, "1h": 200},
            "SO2": {"24h": 125, "1h": 200},
            "O3": {"8h": 120},
        }, "Immissionsschutzgesetz-Luft (IG-L)", 2010),

        ("CH", "Switzerland", {
            "PM2.5": {"annual": 10, "24h": 25},
            "PM10": {"annual": 20, "24h": 50},
            "NO2": {"annual": 30, "1h": 100},
            "SO2": {"annual": 30, "24h": 100},
            "O3": {"1h": 120},
        }, "Ordinance on Air Pollution Control (LRV)", 2018),

        ("IE", "Ireland", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Air Pollution Act 1987 / EU AAQD transposition", 2011),

        ("PT", "Portugal", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Decreto-Lei 102/2010 (EU AAQD transposition)", 2010),

        ("GR", "Greece", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Environmental Protection Act / EU AAQD transposition", 2010),

        ("HU", "Hungary", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Act LIII of 1995 on Environmental Protection / EU AAQD", 2010),

        ("RO", "Romania", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Law 104/2011 on Ambient Air Quality", 2011),

        ("BG", "Bulgaria", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Clean Ambient Air Act / EU AAQD transposition", 2010),

        ("HR", "Croatia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Air Protection Act / EU AAQD transposition", 2013),

        ("SK", "Slovakia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Act No. 137/2010 Coll. on Air / EU AAQD", 2010),

        # ── North America ──
        ("US", "United States", {
            "PM2.5": {"annual": 9, "24h": 35},
            "PM10": {"24h": 150},
            "NO2": {"annual": 53, "1h": 100},
            "SO2": {"1h": 196},
            "O3": {"8h": 137},  # 0.070 ppm
            "CO": {"8h": 9000, "1h": 35000},
        }, "Clean Air Act / NAAQS (2024 revision)", 2024),

        ("CA", "Canada", {
            "PM2.5": {"annual": 8.8, "24h": 27},
            "PM10": {"24h": 50},
            "NO2": {"annual": 17, "1h": 60},
            "SO2": {"annual": 10, "1h": 70},
            "O3": {"8h": 122},  # 62 ppb
            "CO": {"8h": 13000},
        }, "Canadian Ambient Air Quality Standards (CAAQS) 2025", 2025),

        ("MX", "Mexico", {
            "PM2.5": {"annual": 10, "24h": 45},
            "PM10": {"annual": 36, "24h": 70},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 40, "1h": 200},
            "O3": {"8h": 137},
            "CO": {"8h": 8700},
        }, "NOM-025-SSA1-2021 / Ley General del Equilibrio Ecológico", 2021),

        # ── South America ──
        ("BR", "Brazil", {
            "PM2.5": {"annual": 15, "24h": 50},  # PI-3 target
            "PM10": {"annual": 25, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 20, "24h": 40},
            "O3": {"8h": 100},
            "CO": {"8h": 10000},
        }, "CONAMA Resolution 491/2018", 2018),

        ("CL", "Chile", {
            "PM2.5": {"annual": 20, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 60, "24h": 125},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "Supreme Decree 12/2011 (PM2.5) / Atmospheric Decontamination Plans", 2014),

        ("CO", "Colombia", {
            "PM2.5": {"annual": 15, "24h": 37},
            "PM10": {"annual": 30, "24h": 75},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 50},
            "O3": {"8h": 100},
            "CO": {"8h": 5000},
        }, "Resolution 2254/2017 (NAAQS)", 2017),

        ("AR", "Argentina", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"1h": 120},
            "CO": {"8h": 10000},
        }, "Ley de Presupuestos Mínimos para la Protección Ambiental", 2002),

        ("PE", "Peru", {
            "PM2.5": {"annual": 15, "24h": 50},
            "PM10": {"annual": 50, "24h": 100},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 250},
            "O3": {"8h": 100},
            "CO": {"8h": 10000},
        }, "Supreme Decree 003-2017-MINAM (ECA for Air)", 2017),

        ("EC", "Ecuador", {
            "PM2.5": {"annual": 15, "24h": 50},
            "PM10": {"annual": 50, "24h": 100},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 60, "24h": 125},
            "O3": {"8h": 100},
            "CO": {"8h": 10000},
        }, "TULSMA / Environmental Management Law", 2015),

        ("UY", "Uruguay", {
            "PM2.5": {"annual": 15, "24h": 25},
            "PM10": {"annual": 30, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 30, "24h": 125},
            "O3": {"8h": 100},
        }, "Decreto 135/021 (Air Quality Standards)", 2021),

        # ── Middle East ──
        ("AE", "United Arab Emirates", {
            "PM2.5": {"annual": 15, "24h": 65},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 60, "24h": 125},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "Federal Law No. 12/2021 on Climate Change / NAAQS", 2021),

        ("SA", "Saudi Arabia", {
            "PM2.5": {"annual": 15, "24h": 35},
            "PM10": {"annual": 80, "24h": 340},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"1h": 200},
            "CO": {"8h": 10000},
        }, "General Environmental Regulations / NAAQS", 2012),

        ("IL", "Israel", {
            "PM2.5": {"annual": 25, "24h": 37.5},
            "PM10": {"annual": 50, "24h": 130},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "Clean Air Law (2008)", 2008),

        ("TR", "Turkey", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Air Quality Assessment and Management Regulation / EU alignment", 2013),

        ("IQ", "Iraq", {
            "PM10": {"annual": 80, "24h": 350},
            "NO2": {"annual": 100, "1h": 400},
            "SO2": {"annual": 60, "24h": 150},
        }, "Environmental Protection and Improvement Act No. 27/2009", 2009),

        ("EG", "Egypt", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 80, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 60, "24h": 150},
            "O3": {"1h": 200},
            "CO": {"8h": 10000},
        }, "Environmental Law No. 4/1994 (amended 2009) / Decree 964/2015", 2015),

        # ── Africa ──
        ("ZA", "South Africa", {
            "PM2.5": {"annual": 20, "24h": 40},
            "PM10": {"annual": 40, "24h": 75},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "National Environmental Management: Air Quality Act (Act 39 of 2004)", 2004),

        ("NG", "Nigeria", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 100, "24h": 260},
            "O3": {"1h": 200},
        }, "National Environmental (Air Quality Control) Regulations 2014", 2014),

        ("KE", "Kenya", {
            "PM2.5": {"annual": 25, "24h": 75},
            "PM10": {"annual": 50, "24h": 100},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
        }, "Environmental Management and Co-ordination (Air Quality) Regulations 2014", 2014),

        ("GH", "Ghana", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 70, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 260},
        }, "Environmental Protection Agency Act 1994 / Ambient Air Quality Guidelines", 2000),

        ("ET", "Ethiopia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
        }, "Environmental Pollution Control Proclamation 300/2002 / NAAQS", 2003),

        ("TZ", "Tanzania", {
            "PM10": {"annual": 60, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 100, "24h": 365},
        }, "Environmental Management Act 2004 / Environmental Standards Regulations", 2007),

        # ── Oceania ──
        ("AU", "Australia", {
            "PM2.5": {"annual": 7, "24h": 25},
            "PM10": {"annual": 20, "24h": 50},
            "NO2": {"annual": 15, "1h": 80},
            "SO2": {"annual": 20, "24h": 75},
            "O3": {"8h": 120, "1h": 160},
            "CO": {"8h": 10000},
        }, "National Environment Protection (Ambient Air Quality) Measure 2025", 2025),

        ("NZ", "New Zealand", {
            "PM2.5": {"annual": 10, "24h": 25},
            "PM10": {"annual": 20, "24h": 50},
            "NO2": {"annual": 30, "1h": 200},
            "SO2": {"annual": 30, "24h": 120},
            "O3": {"1h": 150},
            "CO": {"8h": 10000},
        }, "National Environmental Standards for Air Quality (NESAQ) 2004 (amended 2021)", 2021),

        # ── Additional countries ──
        ("RU", "Russia", {
            "PM2.5": {"annual": 25, "24h": 35},
            "PM10": {"annual": 40, "24h": 60},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
            "O3": {"8h": 100},
            "CO": {"8h": 5000},
        }, "Federal Law on Environmental Protection / GN 2.1.6.3492-17", 2017),

        ("UA", "Ukraine", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
            "O3": {"8h": 120},
        }, "Law on Air Protection / EU approximation", 2017),

        ("IR", "Iran", {
            "PM2.5": {"annual": 25, "24h": 75},
            "PM10": {"annual": 60, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"8h": 160},
            "CO": {"8h": 10000},
        }, "Clean Air Law (2017)", 2017),

        # ── EU member states (EU AAQD 2008/50/EC common limits) ──
        ("BE", "Belgium", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "EU AAQD transposition", 2010),

        ("LT", "Lithuania", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Law on Ambient Air Protection / EU AAQD", 2010),

        ("LV", "Latvia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Law on Pollution / EU AAQD", 2010),

        ("EE", "Estonia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Ambient Air Protection Act / EU AAQD", 2010),

        ("SI", "Slovenia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Environment Protection Act / EU AAQD", 2010),

        ("LU", "Luxembourg", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Law on Air Quality / EU AAQD", 2010),

        ("MT", "Malta", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Environment Protection Act / EU AAQD", 2010),

        # ── Middle East / North Africa ──
        ("JO", "Jordan", {
            "PM2.5": {"annual": 25, "24h": 65},
            "PM10": {"annual": 70, "24h": 120},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 60, "24h": 150},
            "O3": {"8h": 120},
        }, "Environment Protection Law No. 6/2017", 2017),

        ("KW", "Kuwait", {
            "PM2.5": {"annual": 15, "24h": 35},
            "PM10": {"annual": 90, "24h": 350},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"1h": 200},
        }, "EPA Environmental Regulations", 2014),

        ("QA", "Qatar", {
            "PM2.5": {"annual": 25, "24h": 75},
            "PM10": {"annual": 100, "24h": 200},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
        }, "Ministry of Environment NAAQS", 2010),

        ("OM", "Oman", {
            "PM10": {"annual": 80, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 60, "24h": 125},
            "O3": {"8h": 120},
        }, "Royal Decree on Environmental Protection", 2001),

        ("MA", "Morocco", {
            "PM2.5": {"annual": 25, "24h": 75},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
            "O3": {"8h": 120},
        }, "Law 13-03 on Air Pollution Control", 2003),

        ("TN", "Tunisia", {
            "PM10": {"annual": 50, "24h": 260},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"8h": 160},
        }, "Environmental Protection Framework", 2018),

        ("DZ", "Algeria", {
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 150},
        }, "Law 03-10 on Environmental Protection", 2003),

        # ── Sub-Saharan Africa ──
        ("UG", "Uganda", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 100, "24h": 260},
        }, "National Environment Act 2019", 2019),

        ("RW", "Rwanda", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
        }, "Law on Environment / Air Quality Standards", 2018),

        ("SN", "Senegal", {
            "PM10": {"annual": 60, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 100, "24h": 365},
        }, "Environment Code / NS 05-062 Air Quality", 2001),

        # ── South America ──
        ("VE", "Venezuela", {
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"1h": 160},
            "CO": {"8h": 10000},
        }, "Normas sobre Calidad del Aire (Gaceta 4899)", 1995),

        ("PY", "Paraguay", {
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
        }, "Environmental Law 294/93", 1993),

        ("BO", "Bolivia", {
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"8h": 120},
        }, "Law 1333 on Environment / Annex 1", 1992),

        ("CR", "Costa Rica", {
            "PM2.5": {"annual": 15, "24h": 50},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
            "O3": {"8h": 120},
            "CO": {"8h": 10000},
        }, "Decreto 39951-S Ambient Air Quality Standards", 2016),

        ("PA", "Panama", {
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 80, "24h": 365},
            "O3": {"8h": 160},
        }, "General Environmental Law (Law 41 of 1998)", 1998),

        # ── Southeast Asia ──
        ("LA", "Laos", {
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 300},
        }, "Environmental Protection Law / NAAQS", 2012),

        ("BN", "Brunei", {
            "PM2.5": {"annual": 15, "24h": 35},
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
            "O3": {"8h": 100},
        }, "Environmental Protection and Management Order", 2016),

        # ── Central Asia ──
        ("UZ", "Uzbekistan", {
            "PM10": {"annual": 50, "24h": 150},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"annual": 50, "24h": 125},
        }, "Law on Air Protection", 1996),

        ("GE", "Georgia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Law on Ambient Air Protection / EU approximation", 2017),

        # ── Other ──
        ("IS", "Iceland", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Regulation on Air Quality / EEA AAQD alignment", 2010),

        ("RS", "Serbia", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Law on Air Protection / EU approximation", 2013),

        ("BA", "Bosnia and Herzegovina", {
            "PM2.5": {"annual": 25, "24h": 50},
            "PM10": {"annual": 40, "24h": 50},
            "NO2": {"annual": 40, "1h": 200},
            "SO2": {"24h": 125, "1h": 350},
            "O3": {"8h": 120},
        }, "Law on Air Protection", 2003),
    ]

    policies: list[dict] = []

    for cc, name, standards, law_name, year in raw_data:
        aq_standards = []
        for pollutant, periods in standards.items():
            for period, value in periods.items():
                who_val = WHO_GUIDELINES.get(pollutant, {}).get(period)
                aq_standards.append({
                    "pollutant": pollutant,
                    "averagingPeriod": period,
                    "value": value,
                    "unit": "ug/m3",
                    "whoGuidelineValue": who_val if who_val else None,
                    "ratioToWho": round(value / who_val, 2) if who_val else None,
                })

        policy = {
            "id": f"WHO-STD-{cc}",
            "countryCode": cc,
            "countryName": name,
            "region": REGIONS.get(cc, "Unknown"),
            "name": law_name,
            "nameLocal": "",
            "type": "standard",
            "status": "in_force",
            "adoptedDate": f"{year}-01-01",
            "effectiveDate": f"{year}-01-01",
            "sector": ["cross-sector"],
            "pollutants": list(standards.keys()),
            "scope": "national",
            "targets": [],
            "standards": aq_standards,
            "source": "WHO AQ Standards DB / National Regulations",
            "sourceUrl": "https://www.who.int/tools/air-quality-standards",
            "description": f"National ambient air quality standards for {name}. "
                          f"Covers {len(standards)} pollutants.",
            "sdidTreatmentYear": year,
        }
        policies.append(policy)

    return policies


def main() -> None:
    policies = build_standards_database()

    BY_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = BY_SOURCE_DIR / "who_standards.json"
    with open(output_path, "w") as f:
        json.dump(policies, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved {len(policies)} country standards to {output_path}")

    # WHO compliance summary
    meets_who = 0
    for p in policies:
        pm25_annual = next(
            (s for s in p["standards"]
             if s["pollutant"] == "PM2.5" and s["averagingPeriod"] == "annual"),
            None,
        )
        if pm25_annual and pm25_annual["ratioToWho"] and pm25_annual["ratioToWho"] <= 1.0:
            meets_who += 1

    logger.info(f"Countries meeting WHO PM2.5 annual guideline (5 ug/m3): {meets_who}/{len(policies)}")


if __name__ == "__main__":
    main()
