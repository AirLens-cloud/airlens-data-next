#!/usr/bin/env python3
"""
export_frontend.py — Generate frontend JSON files from policy registry.

Outputs:
  - apps/web/public/data/policy-index.json (updated country index)
  - apps/web/public/data/policy-registry/{CC}.json (per-country detail)
  - apps/web/public/data/policy-registry/global-summary.json (stats)

Usage:
  .venv/bin/python scripts/collect_policies/export_frontend.py
"""
from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # repo root (scripts/etl/collect_policies → ../../../..)
sys.path.insert(0, str(ROOT / "scripts" / "etl"))

from collect_policies.config import FRONTEND_DIR, REGIONS, REGISTRY_DIR, WHO_GUIDELINES

# Country names and flags
COUNTRY_INFO: dict[str, tuple[str, str]] = {
    "AD": ("Andorra", "\U0001f1e6\U0001f1e9"), "AE": ("United Arab Emirates", "\U0001f1e6\U0001f1ea"),
    "AF": ("Afghanistan", "\U0001f1e6\U0001f1eb"), "AL": ("Albania", "\U0001f1e6\U0001f1f1"),
    "AM": ("Armenia", "\U0001f1e6\U0001f1f2"), "AO": ("Angola", "\U0001f1e6\U0001f1f4"),
    "AR": ("Argentina", "\U0001f1e6\U0001f1f7"), "AT": ("Austria", "\U0001f1e6\U0001f1f9"),
    "AU": ("Australia", "\U0001f1e6\U0001f1fa"), "AZ": ("Azerbaijan", "\U0001f1e6\U0001f1ff"),
    "BA": ("Bosnia and Herzegovina", "\U0001f1e7\U0001f1e6"), "BB": ("Barbados", "\U0001f1e7\U0001f1e7"),
    "BD": ("Bangladesh", "\U0001f1e7\U0001f1e9"), "BE": ("Belgium", "\U0001f1e7\U0001f1ea"),
    "BF": ("Burkina Faso", "\U0001f1e7\U0001f1eb"), "BG": ("Bulgaria", "\U0001f1e7\U0001f1ec"),
    "BH": ("Bahrain", "\U0001f1e7\U0001f1ed"), "BI": ("Burundi", "\U0001f1e7\U0001f1ee"),
    "BJ": ("Benin", "\U0001f1e7\U0001f1ef"), "BN": ("Brunei", "\U0001f1e7\U0001f1f3"),
    "BO": ("Bolivia", "\U0001f1e7\U0001f1f4"), "BR": ("Brazil", "\U0001f1e7\U0001f1f7"),
    "BS": ("Bahamas", "\U0001f1e7\U0001f1f8"), "BT": ("Bhutan", "\U0001f1e7\U0001f1f9"),
    "BW": ("Botswana", "\U0001f1e7\U0001f1fc"), "BY": ("Belarus", "\U0001f1e7\U0001f1fe"),
    "CA": ("Canada", "\U0001f1e8\U0001f1e6"), "CD": ("DR Congo", "\U0001f1e8\U0001f1e9"),
    "CF": ("Central African Republic", "\U0001f1e8\U0001f1eb"), "CG": ("Congo", "\U0001f1e8\U0001f1ec"),
    "CH": ("Switzerland", "\U0001f1e8\U0001f1ed"), "CI": ("Ivory Coast", "\U0001f1e8\U0001f1ee"),
    "CL": ("Chile", "\U0001f1e8\U0001f1f1"), "CM": ("Cameroon", "\U0001f1e8\U0001f1f2"),
    "CN": ("China", "\U0001f1e8\U0001f1f3"), "CO": ("Colombia", "\U0001f1e8\U0001f1f4"),
    "CR": ("Costa Rica", "\U0001f1e8\U0001f1f7"), "CU": ("Cuba", "\U0001f1e8\U0001f1fa"),
    "CV": ("Cape Verde", "\U0001f1e8\U0001f1fb"), "CY": ("Cyprus", "\U0001f1e8\U0001f1fe"),
    "CZ": ("Czech Republic", "\U0001f1e8\U0001f1ff"), "DE": ("Germany", "\U0001f1e9\U0001f1ea"),
    "DJ": ("Djibouti", "\U0001f1e9\U0001f1ef"), "DK": ("Denmark", "\U0001f1e9\U0001f1f0"),
    "DO": ("Dominican Republic", "\U0001f1e9\U0001f1f4"), "DZ": ("Algeria", "\U0001f1e9\U0001f1ff"),
    "EC": ("Ecuador", "\U0001f1ea\U0001f1e8"), "EE": ("Estonia", "\U0001f1ea\U0001f1ea"),
    "EG": ("Egypt", "\U0001f1ea\U0001f1ec"), "ER": ("Eritrea", "\U0001f1ea\U0001f1f7"),
    "ES": ("Spain", "\U0001f1ea\U0001f1f8"), "ET": ("Ethiopia", "\U0001f1ea\U0001f1f9"),
    "FI": ("Finland", "\U0001f1eb\U0001f1ee"), "FJ": ("Fiji", "\U0001f1eb\U0001f1ef"),
    "FR": ("France", "\U0001f1eb\U0001f1f7"), "GA": ("Gabon", "\U0001f1ec\U0001f1e6"),
    "GB": ("United Kingdom", "\U0001f1ec\U0001f1e7"), "GD": ("Grenada", "\U0001f1ec\U0001f1e9"),
    "GE": ("Georgia", "\U0001f1ec\U0001f1ea"), "GH": ("Ghana", "\U0001f1ec\U0001f1ed"),
    "GM": ("Gambia", "\U0001f1ec\U0001f1f2"), "GN": ("Guinea", "\U0001f1ec\U0001f1f3"),
    "GQ": ("Equatorial Guinea", "\U0001f1ec\U0001f1f6"), "GR": ("Greece", "\U0001f1ec\U0001f1f7"),
    "GT": ("Guatemala", "\U0001f1ec\U0001f1f9"), "GW": ("Guinea-Bissau", "\U0001f1ec\U0001f1fc"),
    "GY": ("Guyana", "\U0001f1ec\U0001f1fe"), "HK": ("Hong Kong", "\U0001f1ed\U0001f1f0"),
    "HN": ("Honduras", "\U0001f1ed\U0001f1f3"), "HR": ("Croatia", "\U0001f1ed\U0001f1f7"),
    "HT": ("Haiti", "\U0001f1ed\U0001f1f9"), "HU": ("Hungary", "\U0001f1ed\U0001f1fa"),
    "ID": ("Indonesia", "\U0001f1ee\U0001f1e9"), "IE": ("Ireland", "\U0001f1ee\U0001f1ea"),
    "IL": ("Israel", "\U0001f1ee\U0001f1f1"), "IN": ("India", "\U0001f1ee\U0001f1f3"),
    "IQ": ("Iraq", "\U0001f1ee\U0001f1f6"), "IR": ("Iran", "\U0001f1ee\U0001f1f7"),
    "IS": ("Iceland", "\U0001f1ee\U0001f1f8"), "IT": ("Italy", "\U0001f1ee\U0001f1f9"),
    "JM": ("Jamaica", "\U0001f1ef\U0001f1f2"), "JO": ("Jordan", "\U0001f1ef\U0001f1f4"),
    "JP": ("Japan", "\U0001f1ef\U0001f1f5"), "KE": ("Kenya", "\U0001f1f0\U0001f1ea"),
    "KG": ("Kyrgyzstan", "\U0001f1f0\U0001f1ec"), "KH": ("Cambodia", "\U0001f1f0\U0001f1ed"),
    "KR": ("South Korea", "\U0001f1f0\U0001f1f7"), "KW": ("Kuwait", "\U0001f1f0\U0001f1fc"),
    "KZ": ("Kazakhstan", "\U0001f1f0\U0001f1ff"), "LA": ("Laos", "\U0001f1f1\U0001f1e6"),
    "LB": ("Lebanon", "\U0001f1f1\U0001f1e7"), "LK": ("Sri Lanka", "\U0001f1f1\U0001f1f0"),
    "LR": ("Liberia", "\U0001f1f1\U0001f1f7"), "LS": ("Lesotho", "\U0001f1f1\U0001f1f8"),
    "LT": ("Lithuania", "\U0001f1f1\U0001f1f9"), "LU": ("Luxembourg", "\U0001f1f1\U0001f1fa"),
    "LV": ("Latvia", "\U0001f1f1\U0001f1fb"), "LY": ("Libya", "\U0001f1f1\U0001f1fe"),
    "MA": ("Morocco", "\U0001f1f2\U0001f1e6"), "MD": ("Moldova", "\U0001f1f2\U0001f1e9"),
    "ME": ("Montenegro", "\U0001f1f2\U0001f1ea"), "MG": ("Madagascar", "\U0001f1f2\U0001f1ec"),
    "MK": ("North Macedonia", "\U0001f1f2\U0001f1f0"), "ML": ("Mali", "\U0001f1f2\U0001f1f1"),
    "MM": ("Myanmar", "\U0001f1f2\U0001f1f2"), "MN": ("Mongolia", "\U0001f1f2\U0001f1f3"),
    "MR": ("Mauritania", "\U0001f1f2\U0001f1f7"), "MT": ("Malta", "\U0001f1f2\U0001f1f9"),
    "MU": ("Mauritius", "\U0001f1f2\U0001f1fa"), "MV": ("Maldives", "\U0001f1f2\U0001f1fb"),
    "MW": ("Malawi", "\U0001f1f2\U0001f1fc"), "MX": ("Mexico", "\U0001f1f2\U0001f1fd"),
    "MY": ("Malaysia", "\U0001f1f2\U0001f1fe"), "MZ": ("Mozambique", "\U0001f1f2\U0001f1ff"),
    "NA": ("Namibia", "\U0001f1f3\U0001f1e6"), "NE": ("Niger", "\U0001f1f3\U0001f1ea"),
    "NG": ("Nigeria", "\U0001f1f3\U0001f1ec"), "NI": ("Nicaragua", "\U0001f1f3\U0001f1ee"),
    "NL": ("Netherlands", "\U0001f1f3\U0001f1f1"), "NO": ("Norway", "\U0001f1f3\U0001f1f4"),
    "NP": ("Nepal", "\U0001f1f3\U0001f1f5"), "NZ": ("New Zealand", "\U0001f1f3\U0001f1ff"),
    "OM": ("Oman", "\U0001f1f4\U0001f1f2"), "PA": ("Panama", "\U0001f1f5\U0001f1e6"),
    "PE": ("Peru", "\U0001f1f5\U0001f1ea"), "PG": ("Papua New Guinea", "\U0001f1f5\U0001f1ec"),
    "PH": ("Philippines", "\U0001f1f5\U0001f1ed"), "PK": ("Pakistan", "\U0001f1f5\U0001f1f0"),
    "PL": ("Poland", "\U0001f1f5\U0001f1f1"), "PS": ("Palestine", "\U0001f1f5\U0001f1f8"),
    "PT": ("Portugal", "\U0001f1f5\U0001f1f9"), "PY": ("Paraguay", "\U0001f1f5\U0001f1fe"),
    "QA": ("Qatar", "\U0001f1f6\U0001f1e6"), "RO": ("Romania", "\U0001f1f7\U0001f1f4"),
    "RS": ("Serbia", "\U0001f1f7\U0001f1f8"), "RU": ("Russia", "\U0001f1f7\U0001f1fa"),
    "RW": ("Rwanda", "\U0001f1f7\U0001f1fc"), "SA": ("Saudi Arabia", "\U0001f1f8\U0001f1e6"),
    "SD": ("Sudan", "\U0001f1f8\U0001f1e9"), "SE": ("Sweden", "\U0001f1f8\U0001f1ea"),
    "SG": ("Singapore", "\U0001f1f8\U0001f1ec"), "SI": ("Slovenia", "\U0001f1f8\U0001f1ee"),
    "SK": ("Slovakia", "\U0001f1f8\U0001f1f0"), "SL": ("Sierra Leone", "\U0001f1f8\U0001f1f1"),
    "SN": ("Senegal", "\U0001f1f8\U0001f1f3"), "SO": ("Somalia", "\U0001f1f8\U0001f1f4"),
    "SR": ("Suriname", "\U0001f1f8\U0001f1f7"), "SS": ("South Sudan", "\U0001f1f8\U0001f1f8"),
    "SV": ("El Salvador", "\U0001f1f8\U0001f1fb"), "SY": ("Syria", "\U0001f1f8\U0001f1fe"),
    "SZ": ("Eswatini", "\U0001f1f8\U0001f1ff"), "TD": ("Chad", "\U0001f1f9\U0001f1e9"),
    "TG": ("Togo", "\U0001f1f9\U0001f1ec"), "TH": ("Thailand", "\U0001f1f9\U0001f1ed"),
    "TJ": ("Tajikistan", "\U0001f1f9\U0001f1ef"), "TN": ("Tunisia", "\U0001f1f9\U0001f1f3"),
    "TO": ("Tonga", "\U0001f1f9\U0001f1f4"), "TR": ("Turkey", "\U0001f1f9\U0001f1f7"),
    "TT": ("Trinidad and Tobago", "\U0001f1f9\U0001f1f9"), "TW": ("Taiwan", "\U0001f1f9\U0001f1fc"),
    "TZ": ("Tanzania", "\U0001f1f9\U0001f1ff"), "UA": ("Ukraine", "\U0001f1fa\U0001f1e6"),
    "UG": ("Uganda", "\U0001f1fa\U0001f1ec"), "US": ("United States", "\U0001f1fa\U0001f1f8"),
    "UY": ("Uruguay", "\U0001f1fa\U0001f1fe"), "UZ": ("Uzbekistan", "\U0001f1fa\U0001f1ff"),
    "VE": ("Venezuela", "\U0001f1fb\U0001f1ea"), "VN": ("Vietnam", "\U0001f1fb\U0001f1f3"),
    "YE": ("Yemen", "\U0001f1fe\U0001f1ea"), "ZA": ("South Africa", "\U0001f1ff\U0001f1e6"),
    "ZM": ("Zambia", "\U0001f1ff\U0001f1f2"), "ZW": ("Zimbabwe", "\U0001f1ff\U0001f1fc"),
}


def load_registry() -> list[dict]:
    """Load the merged policy registry."""
    path = REGISTRY_DIR / "policy_registry.json"
    if not path.exists():
        logger.error(f"Registry not found: {path}. Run merge_policies.py first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def build_policy_index(policies: list[dict]) -> list[dict]:
    """Build the country-level policy index for the frontend."""
    by_cc: dict[str, list[dict]] = defaultdict(list)
    for p in policies:
        cc = p.get("countryCode", "")
        if cc:
            by_cc[cc].append(p)

    index = []
    for cc, country_policies in sorted(by_cc.items()):
        info = COUNTRY_INFO.get(cc, (cc, ""))
        name, flag = info

        # Find latest update date
        dates = [p.get("adoptedDate", "") for p in country_policies if p.get("adoptedDate")]
        last_updated = max(dates) if dates else ""

        # Check WHO compliance
        has_standards = any(p.get("standards") for p in country_policies)
        pm25_annual = None
        if has_standards:
            for p in country_policies:
                for s in p.get("standards", []):
                    if s.get("pollutant") == "PM2.5" and s.get("averagingPeriod") == "annual":
                        pm25_annual = s.get("value")
                        break
                if pm25_annual is not None:
                    break

        entry = {
            "country": name,
            "countryCode": cc,
            "region": REGIONS.get(cc, "Unknown"),
            "flag": flag,
            "policyCount": len(country_policies),
            "lastUpdated": last_updated,
            "hasStandards": has_standards,
            "pm25AnnualStandard": pm25_annual,
            "whoCompliance": round(pm25_annual / WHO_GUIDELINES["PM2.5"]["annual"], 2) if pm25_annual else None,
        }
        index.append(entry)

    # Sort by country name
    index.sort(key=lambda x: x["country"])
    return index


def build_country_detail(cc: str, policies: list[dict]) -> dict:
    """Build detailed country policy file for frontend."""
    info = COUNTRY_INFO.get(cc, (cc, ""))
    name, flag = info

    # Separate standards from other policies
    standards = [p for p in policies if p.get("standards")]
    other_policies = [p for p in policies if not p.get("standards")]

    return {
        "countryCode": cc,
        "countryName": name,
        "region": REGIONS.get(cc, "Unknown"),
        "flag": flag,
        "totalPolicies": len(policies),
        "standards": standards,
        "policies": other_policies,
    }


def build_global_summary(policies: list[dict], index: list[dict]) -> dict:
    """Build global summary statistics."""
    by_type: dict[str, int] = defaultdict(int)
    by_region: dict[str, int] = defaultdict(int)
    by_decade: dict[str, int] = defaultdict(int)
    pollutant_counts: dict[str, int] = defaultdict(int)

    for p in policies:
        by_type[p.get("type", "unknown")] += 1
        by_region[p.get("region", "Unknown")] += 1

        year = p.get("sdidTreatmentYear")
        if year:
            decade = f"{(year // 10) * 10}s"
            by_decade[decade] += 1

        for poll in p.get("pollutants", []):
            pollutant_counts[poll] += 1

    countries_with_standards = sum(1 for e in index if e.get("hasStandards"))
    countries_meeting_who = sum(
        1 for e in index
        if e.get("whoCompliance") is not None and e["whoCompliance"] <= 1.0
    )

    return {
        "totalPolicies": len(policies),
        "totalCountries": len(index),
        "countriesWithStandards": countries_with_standards,
        "countriesMeetingWho": countries_meeting_who,
        "whoGuidelines": WHO_GUIDELINES,
        "byType": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "byRegion": dict(sorted(by_region.items(), key=lambda x: -x[1])),
        "byDecade": dict(sorted(by_decade.items())),
        "pollutantCoverage": dict(sorted(pollutant_counts.items(), key=lambda x: -x[1])),
    }


def build_recent(policies: list[dict], top_n: int = 20, per_country_cap: int = 3) -> list[dict]:
    """Top-N most recently effective policies, effectiveDate desc, entries with
    no effectiveDate last — same base ordering as the retired Supabase query
    `.order('effective_date', {ascending: false, nullsFirst: false})`.
    (Falling back to adoptedDate for *display* when effectiveDate is absent is
    the frontend mapper's job — apps/web/src/api/policyRegistry.ts `mapRegistryRow`
    — not the sort key here.)

    per_country_cap (2026-08-26, user decision): at most N entries per country
    in the first pass, so a single high-volume source (US Federal Register,
    240 docs) cannot occupy the whole top-N. Ordering within the result stays
    effectiveDate desc; if fewer than top_n survive the cap, remaining slots
    are backfilled with the newest skipped entries (still date-desc) so the
    list length is stable. Set per_country_cap=0 to disable.

    Items are returned as-is from the merged registry (same field shape as
    the `policies[]`/`standards[]` entries inside `{CC}.json`) — D3: this
    feeds the Dispatch policy section via `fetchRecentPolicies`
    (`/data/policy-registry/recent.json`).
    """

    def has_date(p: dict) -> bool:
        return bool(p.get("effectiveDate"))

    with_date = sorted((p for p in policies if has_date(p)), key=lambda p: p["effectiveDate"], reverse=True)
    without_date = [p for p in policies if not has_date(p)]
    ordered = with_date + without_date
    if not per_country_cap:
        return ordered[:top_n]

    per_cc: dict[str, int] = {}
    capped: list[dict] = []
    skipped: list[dict] = []
    for p in ordered:
        cc = p.get("countryCode", "") or "??"
        if per_cc.get(cc, 0) < per_country_cap:
            per_cc[cc] = per_cc.get(cc, 0) + 1
            capped.append(p)
        else:
            skipped.append(p)
        if len(capped) == top_n:
            break
    if len(capped) < top_n:
        capped.extend(skipped[: top_n - len(capped)])
        capped.sort(key=lambda p: p.get("effectiveDate") or "", reverse=True)
    return capped


def main() -> None:
    policies = load_registry()
    logger.info(f"Loaded {len(policies)} policies from registry")

    # 1. Build and write policy-index.json
    index = build_policy_index(policies)
    index_path = FRONTEND_DIR / "policy-index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    logger.info(f"Wrote policy-index.json: {len(index)} countries")

    # 2. Build and write per-country detail files
    registry_fe_dir = FRONTEND_DIR / "policy-registry"
    registry_fe_dir.mkdir(parents=True, exist_ok=True)

    by_cc: dict[str, list[dict]] = defaultdict(list)
    for p in policies:
        cc = p.get("countryCode", "")
        if cc:
            by_cc[cc].append(p)

    for cc, country_policies in by_cc.items():
        detail = build_country_detail(cc, country_policies)
        detail_path = registry_fe_dir / f"{cc}.json"
        with open(detail_path, "w") as f:
            json.dump(detail, f, ensure_ascii=False, indent=2)

    logger.info(f"Wrote {len(by_cc)} country detail files")

    # 3. Build and write global summary
    summary = build_global_summary(policies, index)
    summary_path = registry_fe_dir / "global-summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"Wrote global-summary.json")

    # 4. Build and write recent.json (top-20 by effectiveDate desc, D3)
    recent = build_recent(policies)
    recent_path = registry_fe_dir / "recent.json"
    with open(recent_path, "w") as f:
        json.dump(recent, f, ensure_ascii=False, indent=2)
    logger.info(f"Wrote recent.json: {len(recent)} policies")

    # Print summary
    logger.info("=== Export Summary ===")
    logger.info(f"policy-index.json: {len(index)} countries")
    logger.info(f"policy-registry/: {len(by_cc)} country files")
    logger.info(f"recent.json: {len(recent)} policies")
    logger.info(f"Total policies: {summary['totalPolicies']}")
    logger.info(f"Countries with standards: {summary['countriesWithStandards']}")
    logger.info(f"Countries meeting WHO PM2.5: {summary['countriesMeetingWho']}")


if __name__ == "__main__":
    main()
