#!/usr/bin/env python3
"""
collect_major_policies.py — Compile major air quality laws & policies worldwide.

Sources: UNEP Actions on Air Quality (194 countries), official government publications,
CCAC national plans, EU directives, EPA regulations.

This complements WHO standards with actual policy/law records.

Usage:
  python3 scripts/collect_policies/collect_major_policies.py
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

from collect_policies.config import BY_SOURCE_DIR, REGIONS


def build_policy_database() -> list[dict]:
    """Compile major air quality policies from published sources."""

    policies: list[dict] = []

    def add(
        cc: str, name: str, name_local: str, ptype: str, status: str,
        adopted: str, sectors: list[str], pollutants: list[str],
        description: str, source_url: str = "",
    ) -> None:
        policies.append({
            "id": f"MAJ-{cc}-{adopted[:4]}-{len(policies)}",
            "countryCode": cc,
            "region": REGIONS.get(cc, "Unknown"),
            "name": name,
            "nameLocal": name_local,
            "type": ptype,
            "status": status,
            "adoptedDate": adopted,
            "effectiveDate": adopted,
            "sector": sectors,
            "pollutants": pollutants,
            "scope": "national",
            "targets": [],
            "standards": [],
            "source": "Government Official / UNEP / CCAC",
            "sourceUrl": source_url,
            "description": description,
            "sdidTreatmentYear": int(adopted[:4]) if adopted[:4].isdigit() else None,
        })

    # ═══════════════════════════════════════════
    # EAST ASIA
    # ═══════════════════════════════════════════

    # South Korea
    add("KR", "Clean Air Conservation Act", "대기환경보전법",
        "law", "in_force", "1990-08-01",
        ["industry", "transport", "energy"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "CO"],
        "Foundational air quality law establishing emission standards, monitoring, and enforcement.")
    add("KR", "Special Act on Fine Dust Reduction and Management", "미세먼지 저감 및 관리에 관한 특별법",
        "law", "in_force", "2019-02-15",
        ["cross-sector"], ["PM2.5", "PM10"],
        "Special legislation targeting fine dust reduction with seasonal management system and emergency measures.")
    add("KR", "Comprehensive Plan for Fine Dust Management (2020-2024)", "제2차 미세먼지 관리 종합계획",
        "plan", "in_force", "2019-11-01",
        ["industry", "transport", "energy"], ["PM2.5", "PM10"],
        "5-year comprehensive plan targeting 35% reduction in domestic PM2.5 emissions by 2024.")
    add("KR", "Seasonal Fine Dust Management System", "고농도 미세먼지 계절관리제",
        "regulation", "in_force", "2019-12-01",
        ["industry", "transport", "energy"], ["PM2.5"],
        "Dec-Mar intensive emission reduction measures including coal plant curtailment and vehicle restrictions.")
    add("KR", "Metropolitan Air Quality Improvement Special Act", "수도권 대기환경개선에 관한 특별법",
        "law", "in_force", "2003-12-31",
        ["transport", "industry"], ["PM2.5", "PM10", "NO2"],
        "Special measures for Seoul metropolitan area air quality improvement.")
    add("KR", "Vehicle Emission Grade System", "자동차 배출가스 등급제",
        "regulation", "in_force", "2019-02-15",
        ["transport"], ["PM2.5", "NO2"],
        "5-grade vehicle emission classification for LEZ enforcement and old diesel restrictions.")

    # Japan
    add("JP", "Air Pollution Control Act", "大気汚染防止法",
        "law", "in_force", "1968-06-10",
        ["industry", "transport"], ["PM2.5", "PM10", "NO2", "SO2", "O3"],
        "Foundational air pollution control law with emission standards for stationary and mobile sources.")
    add("JP", "Act on Special Measures for Total Emission Reduction of NOx and PM", "NOx・PM法",
        "law", "in_force", "2001-06-27",
        ["transport"], ["PM2.5", "NO2"],
        "Total emission reduction for NOx and PM from vehicles in designated areas.")
    add("JP", "Mercury Air Emission Reduction Plan", "",
        "plan", "in_force", "2016-03-01",
        ["industry"], ["PM2.5"],
        "Mercury and co-pollutant emission reduction from coal plants and waste incineration.")

    # China
    add("CN", "Air Pollution Prevention and Control Action Plan", "大气污染防治行动计划 (国十条)",
        "plan", "amended", "2013-09-10",
        ["industry", "transport", "energy"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Landmark national plan targeting 10% PM10 reduction by 2017, 25% PM2.5 reduction in Beijing-Tianjin-Hebei.")
    add("CN", "Air Pollution Prevention and Control Law (2015 revision)", "大气污染防治法",
        "law", "in_force", "2016-01-01",
        ["industry", "transport", "energy", "agriculture"], ["PM2.5", "PM10", "SO2", "NO2", "O3", "VOC"],
        "Major revision strengthening penalties, expanding monitoring, and adding VOC controls.")
    add("CN", "Three-Year Action Plan for Winning the Blue Sky Defense War", "打赢蓝天保卫战三年行动计划",
        "plan", "in_force", "2018-07-03",
        ["industry", "transport", "energy"], ["PM2.5", "SO2", "NO2"],
        "2018-2020 plan targeting 18% SO2 and NOx reduction from 2015 levels.")
    add("CN", "Action Plan for Continuous Improvement of Air Quality", "空气质量持续改善行动计划",
        "plan", "in_force", "2023-12-07",
        ["industry", "transport", "energy"], ["PM2.5", "O3", "VOC", "NO2"],
        "2024-2025 plan targeting coordinated PM2.5 and O3 control, VOC reduction.")

    # Taiwan
    add("TW", "Air Pollution Control Act (2018 amendment)", "空氣污染防制法",
        "law", "in_force", "2018-08-01",
        ["industry", "transport"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "VOC"],
        "Major amendment strengthening penalties, adding citizen reporting, and mobile source controls.")
    add("TW", "Clean Air Action Plan (2020-2023)", "空氣污染防制行動方案",
        "plan", "in_force", "2020-01-01",
        ["industry", "transport", "energy"], ["PM2.5"],
        "Target: red-alert days reduction by 50%, annual PM2.5 below 15 ug/m3 nationally.")

    # Mongolia
    add("MN", "National Programme to Reduce Air and Environmental Pollution (2017-2025)", "",
        "plan", "in_force", "2017-03-01",
        ["household", "transport", "energy"], ["PM2.5", "PM10", "SO2"],
        "Comprehensive program targeting Ulaanbaatar air pollution from ger district coal burning.")

    # ═══════════════════════════════════════════
    # SOUTHEAST ASIA
    # ═══════════════════════════════════════════

    add("TH", "Enhancement and Conservation of National Environmental Quality Act", "",
        "law", "in_force", "1992-04-01",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2", "O3"],
        "Framework environmental law establishing air quality standards and Pollution Control Department.")
    add("TH", "National Action Plan on Air Quality Management (2024-2027)", "",
        "plan", "in_force", "2024-01-01",
        ["agriculture", "transport", "industry"], ["PM2.5"],
        "4-year plan addressing crop burning, vehicle emissions, and transboundary haze.")

    add("VN", "Law on Environmental Protection (2020 revision)", "Luật Bảo vệ môi trường",
        "law", "in_force", "2022-01-01",
        ["industry", "transport", "construction"], ["PM2.5", "PM10", "NO2", "SO2"],
        "Major revision with stricter emission standards, EIA requirements, and air quality monitoring mandates.")
    add("VN", "National Action Plan on Air Quality Management to 2025", "",
        "plan", "in_force", "2021-01-01",
        ["transport", "industry", "construction"], ["PM2.5", "PM10"],
        "Targets include vehicle emission standards, industrial controls, and monitoring network expansion.")

    add("ID", "Government Regulation No. 22/2021 on Environmental Protection", "",
        "regulation", "in_force", "2021-02-02",
        ["industry", "transport", "energy"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Updated ambient air quality standards and emission limits for industrial sources.")
    add("ID", "Jakarta Clean Air Action Plan", "",
        "plan", "in_force", "2023-01-01",
        ["transport", "industry"], ["PM2.5"],
        "City-level plan for Jakarta addressing vehicle emissions, industrial sources, and monitoring.")

    add("PH", "Clean Air Act (RA 8749)", "",
        "law", "in_force", "1999-06-23",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2", "O3"],
        "Comprehensive clean air law with emission standards, permitting, and airshed management.")

    add("MY", "Environmental Quality (Clean Air) Regulations 2014", "",
        "regulation", "in_force", "2014-06-01",
        ["industry"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Updated emission standards for industrial facilities and power plants.")

    add("SG", "Environmental Protection and Management Act", "",
        "law", "in_force", "1999-04-01",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Framework law for air quality management with strict industrial emission controls.")
    add("SG", "Singapore Green Plan 2030", "",
        "plan", "in_force", "2021-02-10",
        ["transport", "energy", "industry"], ["PM2.5", "CO"],
        "Comprehensive sustainability plan including electric vehicle targets and cleaner energy.")

    # ═══════════════════════════════════════════
    # SOUTH ASIA
    # ═══════════════════════════════════════════

    add("IN", "National Clean Air Programme (NCAP)", "",
        "plan", "in_force", "2019-01-10",
        ["industry", "transport", "household", "agriculture"], ["PM2.5", "PM10"],
        "Target: 40% reduction in PM concentration by 2025-26 in 131 non-attainment cities.")
    add("IN", "Air (Prevention and Control of Pollution) Act", "",
        "law", "in_force", "1981-03-29",
        ["industry"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Foundational air pollution control law establishing Central and State Pollution Control Boards.")
    add("IN", "Graded Response Action Plan (GRAP) for Delhi-NCR", "",
        "regulation", "in_force", "2017-01-12",
        ["transport", "construction", "industry"], ["PM2.5", "PM10"],
        "Emergency response plan with escalating restrictions based on AQI severity in Delhi region.")
    add("IN", "Commission for Air Quality Management in NCR Act", "",
        "law", "in_force", "2021-08-12",
        ["cross-sector"], ["PM2.5", "PM10"],
        "Statutory commission with powers to enforce air quality measures in Delhi-NCR.")

    add("BD", "Bangladesh Environment Conservation Rules", "",
        "regulation", "in_force", "1997-08-27",
        ["industry"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Environmental regulations including ambient air quality standards and industrial emissions.")
    add("BD", "Clean Air Act (proposed 2019)", "",
        "law", "proposed", "2019-04-15",
        ["industry", "transport", "construction"], ["PM2.5", "PM10"],
        "Proposed comprehensive clean air legislation with stricter standards and enforcement.")

    add("PK", "Pakistan Clean Air Act 2024", "",
        "law", "in_force", "2024-03-01",
        ["industry", "transport", "agriculture"], ["PM2.5", "PM10", "SO2", "NO2"],
        "New national clean air law with updated standards and smog commission mandates.")
    add("PK", "Smog Commission (Punjab)", "",
        "regulation", "in_force", "2023-10-01",
        ["agriculture", "industry", "transport"], ["PM2.5"],
        "Provincial commission to combat seasonal smog from crop burning and industrial emissions.")

    add("NP", "Environment Protection Act 2019", "",
        "law", "in_force", "2019-10-18",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Updated environmental protection framework with air quality provisions.")

    add("LK", "National Environmental Act (amended)", "",
        "law", "in_force", "1988-01-01",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Environmental framework law with vehicle emission testing and industrial standards.")

    # ═══════════════════════════════════════════
    # EUROPE
    # ═══════════════════════════════════════════

    # EU-wide
    add("DE", "EU Ambient Air Quality Directive 2008/50/EC", "EU-Luftqualitätsrichtlinie",
        "directive", "in_force", "2008-05-21",
        ["cross-sector"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "CO", "Pb", "benzene"],
        "EU framework setting limit values for major pollutants, transposed into all EU member states.")
    add("DE", "EU Revised AAQD 2024/2881", "",
        "directive", "in_force", "2024-11-20",
        ["cross-sector"], ["PM2.5", "PM10", "NO2", "SO2", "O3"],
        "Revised EU directive: PM2.5 annual limit 10 ug/m3 by 2030, closer to WHO guidelines.")
    add("DE", "National Emission Ceilings Directive (NEC) 2016/2284", "",
        "directive", "in_force", "2016-12-14",
        ["energy", "transport", "agriculture", "industry"], ["SO2", "NO2", "VOC", "NH3", "PM2.5"],
        "EU national emission reduction commitments for 2020 and 2030.")

    add("DE", "Federal Immission Control Act (BImSchG)", "Bundes-Immissionsschutzgesetz",
        "law", "in_force", "1974-03-15",
        ["industry", "transport"], ["PM2.5", "PM10", "NO2", "SO2"],
        "Germany's foundational immission control law with industrial permitting and emission standards.")
    add("DE", "Clean Air Plan for Stuttgart 2019", "",
        "plan", "in_force", "2019-01-01",
        ["transport"], ["NO2", "PM2.5"],
        "City-level plan with diesel driving bans to meet NO2 limit values.")

    add("GB", "Clean Air Strategy 2019", "",
        "strategy", "in_force", "2019-01-14",
        ["transport", "industry", "agriculture", "household"], ["PM2.5", "NO2", "NH3", "VOC"],
        "Comprehensive strategy targeting halving of harm from air pollution by 2030.")
    add("GB", "Environment Act 2021 (air quality provisions)", "",
        "law", "in_force", "2021-11-09",
        ["cross-sector"], ["PM2.5"],
        "Legally binding PM2.5 targets and duty on local authorities for air quality management.")
    add("GB", "Clean Air (Human Rights) Bill (proposed)", "",
        "law", "proposed", "2022-06-01",
        ["cross-sector"], ["PM2.5", "NO2"],
        "Proposed law to establish right to clean air following Ella Adoo-Kissi-Debrah inquest.")

    add("FR", "Plan National de Réduction des Émissions (PREPA)", "",
        "plan", "in_force", "2017-05-10",
        ["transport", "agriculture", "industry", "household"], ["PM2.5", "NO2", "NH3", "VOC", "SO2"],
        "National plan targeting 2020-2030 emission reduction commitments under NEC Directive.")
    add("FR", "Low Emission Zones (ZFE-m)", "Zones à Faibles Émissions mobilité",
        "regulation", "in_force", "2021-01-01",
        ["transport"], ["PM2.5", "NO2"],
        "Mandatory LEZs in 43 cities by 2025 using Crit'Air vehicle classification system.")

    add("IT", "National Air Pollution Control Programme", "Programma Nazionale di Controllo",
        "plan", "in_force", "2021-12-01",
        ["transport", "agriculture", "household", "industry"], ["PM2.5", "NO2", "NH3", "VOC"],
        "National program under NEC Directive with measures for Po Valley air quality improvement.")

    add("NL", "Clean Air Agreement (Schone Lucht Akkoord)", "",
        "plan", "in_force", "2020-01-13",
        ["transport", "industry", "household", "agriculture"], ["PM2.5", "NO2", "NH3"],
        "Voluntary agreement between national/regional/local government targeting 50% health gain by 2030.")

    add("PL", "Clean Air Programme (Czyste Powietrze)", "Program Czyste Powietrze",
        "plan", "in_force", "2018-09-01",
        ["household"], ["PM2.5", "PM10"],
        "Subsidies for replacing coal boilers with clean heating. Target: 3 million homes by 2029.")
    add("PL", "Anti-Smog Resolution (Małopolska)", "",
        "regulation", "in_force", "2017-01-23",
        ["household"], ["PM2.5", "PM10"],
        "Regional ban on coal and wood burning in solid fuel boilers, first such regulation in Poland.")

    add("SE", "Clean Air Programme", "",
        "plan", "in_force", "2019-01-01",
        ["transport", "energy", "household"], ["PM2.5", "NO2"],
        "National programme aligning with NEC Directive reduction commitments.")

    add("NO", "Action Plan for Cleaner Air", "",
        "plan", "in_force", "2016-01-01",
        ["transport", "household"], ["PM2.5", "PM10", "NO2"],
        "Urban air quality improvement with wood-burning and road dust measures.")

    add("CH", "Ordinance on Air Pollution Control (LRV)", "Luftreinhalte-Verordnung",
        "regulation", "in_force", "1985-12-16",
        ["industry", "transport", "household"], ["PM2.5", "PM10", "NO2", "SO2", "O3"],
        "Swiss air pollution control ordinance with some of the strictest standards globally.")

    # ═══════════════════════════════════════════
    # NORTH AMERICA
    # ═══════════════════════════════════════════

    add("US", "Clean Air Act (1970, amended 1990)", "",
        "law", "in_force", "1970-12-31",
        ["industry", "transport", "energy"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "CO", "Pb"],
        "Foundational US federal air quality law establishing NAAQS, SIPs, and mobile source standards.")
    add("US", "NAAQS PM2.5 Revision (2024)", "",
        "regulation", "in_force", "2024-03-06",
        ["cross-sector"], ["PM2.5"],
        "Tightened annual PM2.5 standard from 12 to 9 ug/m3.")
    add("US", "Good Neighbor Plan (2023)", "",
        "regulation", "in_force", "2023-06-05",
        ["energy", "industry"], ["O3", "NO2"],
        "Interstate air pollution rule requiring upwind states to reduce NOx emissions affecting downwind ozone.")
    add("US", "Mercury and Air Toxics Standards (MATS)", "",
        "regulation", "in_force", "2012-02-16",
        ["energy"], ["PM2.5"],
        "Emission standards for hazardous air pollutants from power plants including mercury and PM.")
    add("US", "Tier 3 Motor Vehicle Emission Standards", "",
        "regulation", "in_force", "2017-01-01",
        ["transport"], ["PM2.5", "NO2", "VOC", "SO2"],
        "Tighter vehicle emission and fuel sulfur standards aligned with California LEV III.")

    add("CA", "Canadian Ambient Air Quality Standards (CAAQS)", "",
        "standard", "in_force", "2013-05-25",
        ["cross-sector"], ["PM2.5", "PM10", "NO2", "SO2", "O3"],
        "Progressively tightening ambient standards for 2020 and 2025 achievement deadlines.")
    add("CA", "Air Quality Management System (AQMS)", "",
        "regulation", "in_force", "2013-01-01",
        ["industry"], ["PM2.5", "NO2", "SO2", "VOC"],
        "Comprehensive framework with base-level industrial emission requirements (BLIERs).")

    add("MX", "ProAire Programs", "Programas de Gestión para Mejorar la Calidad del Aire",
        "plan", "in_force", "2014-01-01",
        ["transport", "industry"], ["PM2.5", "PM10", "O3", "NO2"],
        "City-level air quality management programs for major metropolitan areas.")
    add("MX", "NOM-025-SSA1-2021 (PM2.5/PM10 standards)", "",
        "standard", "in_force", "2021-10-01",
        ["cross-sector"], ["PM2.5", "PM10"],
        "Updated Mexican ambient air quality standards for particulate matter.")

    # ═══════════════════════════════════════════
    # SOUTH AMERICA
    # ═══════════════════════════════════════════

    add("BR", "CONAMA Resolution 491/2018", "",
        "regulation", "in_force", "2018-11-19",
        ["cross-sector"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "CO"],
        "Progressive air quality standards with 4 intermediate phases toward WHO guideline values.")
    add("BR", "PROCONVE (Vehicle Emission Control Program)", "",
        "regulation", "in_force", "1986-01-01",
        ["transport"], ["PM2.5", "NO2", "CO", "VOC"],
        "Progressive vehicle emission standards program, currently at Phase L7/P8.")

    add("CL", "Atmospheric Decontamination Plans", "Planes de Descontaminación Atmosférica",
        "plan", "in_force", "2014-01-01",
        ["industry", "household", "transport"], ["PM2.5", "PM10"],
        "Mandatory decontamination plans for zones exceeding air quality standards.")
    add("CL", "Restriction on Wood-Burning Heaters", "",
        "regulation", "in_force", "2015-06-01",
        ["household"], ["PM2.5"],
        "Emission standards and bans on uncertified wood heaters in saturated zones.")

    add("CO", "Resolution 2254/2017 (NAAQS)", "",
        "regulation", "in_force", "2017-11-01",
        ["cross-sector"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "CO"],
        "Updated national ambient air quality standards with tighter PM2.5 limits.")

    add("PE", "Supreme Decree 003-2017-MINAM (ECA for Air)", "",
        "regulation", "in_force", "2017-06-07",
        ["cross-sector"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "CO"],
        "Environmental quality standards for air with progressive tightening.")

    # ═══════════════════════════════════════════
    # MIDDLE EAST
    # ═══════════════════════════════════════════

    add("AE", "Federal Law No. 12/2021 on Climate Change", "",
        "law", "in_force", "2021-10-07",
        ["energy", "industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "UAE's first climate change law with air quality provisions and emission reduction targets.")

    add("SA", "National Environmental Strategy", "",
        "strategy", "in_force", "2017-01-01",
        ["industry", "energy"], ["PM2.5", "PM10", "SO2"],
        "Environmental component of Vision 2030 with industrial emission controls.")

    add("IL", "Clean Air Law (2008)", "חוק אוויר נקי",
        "law", "in_force", "2011-01-01",
        ["industry", "transport"], ["PM2.5", "PM10", "NO2", "SO2", "O3"],
        "Comprehensive clean air law with emission permits, monitoring, and public right to information.")

    add("TR", "Regulation on Assessment and Management of Air Quality", "",
        "regulation", "in_force", "2008-11-06",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2", "O3"],
        "EU-aligned air quality regulation as part of accession process.")
    add("TR", "National Air Quality Action Plan", "",
        "plan", "in_force", "2019-01-01",
        ["industry", "transport", "household"], ["PM2.5", "PM10", "SO2"],
        "Plan for 81 provinces to meet air quality limit values, targeting coal heating transition.")

    add("EG", "Law 4/1994 on Environmental Protection (amended 2009)", "",
        "law", "in_force", "1994-01-27",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Framework environmental law with air quality provisions and industrial emission standards.")

    # ═══════════════════════════════════════════
    # AFRICA
    # ═══════════════════════════════════════════

    add("ZA", "National Environmental Management: Air Quality Act (Act 39 of 2004)", "",
        "law", "in_force", "2005-09-11",
        ["industry", "transport", "household"], ["PM2.5", "PM10", "SO2", "NO2", "O3"],
        "Comprehensive AQ act replacing Atmospheric Pollution Prevention Act, with listed activities and licensing.")
    add("ZA", "National Air Quality Management Plan (2024)", "",
        "plan", "in_force", "2024-01-01",
        ["industry", "energy", "household", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Updated plan addressing Highveld Priority Area and Vaal Triangle air quality challenges.")
    add("ZA", "Minimum Emission Standards (MES) for Listed Activities", "",
        "regulation", "in_force", "2010-03-31",
        ["industry", "energy"], ["PM2.5", "SO2", "NO2"],
        "Point source emission limits for major industrial categories including power generation.")

    add("NG", "National Environmental (Air Quality Control) Regulations 2014", "",
        "regulation", "in_force", "2014-01-01",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Updated air quality regulations with emission limits and monitoring requirements.")

    add("KE", "Environmental Management and Co-ordination (Air Quality) Regulations 2014", "",
        "regulation", "in_force", "2014-07-28",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Air quality regulations under EMCA 1999, with ambient and emission standards.")
    add("KE", "Nairobi City County Clean Air Action Plan", "",
        "plan", "in_force", "2020-01-01",
        ["transport", "industry", "waste"], ["PM2.5"],
        "City-level clean air plan addressing transport, industry, and waste burning emissions.")

    add("GH", "Environmental Assessment Regulations (LI 1652)", "",
        "regulation", "in_force", "1999-06-01",
        ["industry"], ["PM2.5", "PM10", "SO2"],
        "Environmental regulations including air emission standards for industrial activities.")

    add("ET", "Pollution Control Proclamation 300/2002", "",
        "law", "in_force", "2002-12-03",
        ["industry"], ["PM2.5", "PM10", "SO2"],
        "Environmental pollution control law including air quality standards and industrial emissions.")

    # ═══════════════════════════════════════════
    # OCEANIA
    # ═══════════════════════════════════════════

    add("AU", "National Environment Protection (Ambient Air Quality) Measure", "",
        "standard", "in_force", "1998-06-26",
        ["cross-sector"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "CO"],
        "National standard recently updated in 2025 with tighter PM2.5 limits (7 ug/m3 annual).")
    add("AU", "National Clean Air Agreement", "",
        "plan", "in_force", "2015-12-01",
        ["cross-sector"], ["PM2.5", "NO2", "O3"],
        "Intergovernmental agreement to improve air quality management and reporting.")

    add("NZ", "National Environmental Standards for Air Quality (NESAQ)", "",
        "standard", "in_force", "2004-09-01",
        ["cross-sector"], ["PM2.5", "PM10", "NO2", "SO2", "O3", "CO"],
        "National standards with PM10 24-hour limit and ban on new open fires in polluted airsheds.")

    # ═══════════════════════════════════════════
    # ADDITIONAL COUNTRIES (UNEP survey-based)
    # ═══════════════════════════════════════════

    add("RU", "Federal Law on Environmental Protection (2002)", "",
        "law", "in_force", "2002-01-10",
        ["industry", "energy"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Framework environmental law with air quality MPC (maximum permissible concentration) standards.")
    add("RU", "Clean Air Federal Project (2019-2024)", "Чистый воздух",
        "plan", "in_force", "2019-01-01",
        ["industry", "transport"], ["PM2.5"],
        "Federal project targeting 20% emission reduction in 12 most polluted cities.")

    add("UA", "Law on Air Protection (1992, amended 2017)", "",
        "law", "in_force", "1992-10-16",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Air protection law progressively aligned with EU AAQD standards.")

    add("IR", "Clean Air Law (2017)", "",
        "law", "in_force", "2017-07-01",
        ["transport", "industry"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Comprehensive clean air legislation addressing Tehran air quality crisis.")

    # Central Asia
    add("KZ", "Environmental Code of the Republic of Kazakhstan (2021)", "",
        "law", "in_force", "2021-01-02",
        ["industry", "energy"], ["PM2.5", "PM10", "SO2", "NO2"],
        "New comprehensive environmental code with EU-aligned air quality standards.")

    # Caribbean
    add("TT", "Environmental Management Act 2000", "",
        "law", "in_force", "2000-12-15",
        ["industry"], ["PM2.5", "PM10", "SO2"],
        "Environmental management framework for Trinidad and Tobago's petrochemical sector.")

    # North Africa
    add("MA", "Law 13-03 on Air Pollution Control", "",
        "law", "in_force", "2003-05-12",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Morocco's air quality law establishing ambient standards and emission limits.")

    add("TN", "Decree on Air Quality Standards (2018)", "",
        "regulation", "in_force", "2018-01-01",
        ["industry", "transport"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Updated ambient air quality standards for Tunisia.")

    add("DZ", "Law 03-10 on Environmental Protection (2003)", "",
        "law", "in_force", "2003-07-19",
        ["industry"], ["PM2.5", "PM10", "SO2", "NO2"],
        "Framework environmental law with air quality management provisions.")

    return policies


def main() -> None:
    policies = build_policy_database()

    BY_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = BY_SOURCE_DIR / "major_policies.json"
    with open(output_path, "w") as f:
        json.dump(policies, f, ensure_ascii=False, indent=2)

    countries = {p["countryCode"] for p in policies}
    by_type: dict[str, int] = {}
    for p in policies:
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1

    logger.info(f"Saved {len(policies)} major policies from {len(countries)} countries to {output_path}")
    logger.info(f"By type: {by_type}")


if __name__ == "__main__":
    main()
