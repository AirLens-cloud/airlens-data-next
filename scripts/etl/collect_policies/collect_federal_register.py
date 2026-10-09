#!/usr/bin/env python3
"""
collect_federal_register.py — Collect US air quality RULEs from the Federal Register API.

Free ($0), keyless, machine-readable JSON (federalregister.gov/api/v1). Tier 2 structured
source: EPA-issued final rules matching air-quality terms, filtered a second time by an
air-quality keyword check to drop unrelated EPA actions and term false-positives.

No LLM. Server-Collect: GitHub Actions runner → by_source JSON → upsert REST → DB.

Usage:
  python3 scripts/etl/collect_policies/collect_federal_register.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # repo root (scripts/etl/collect_policies → ../../../..)
sys.path.insert(0, str(ROOT / "scripts" / "etl"))

from collect_policies.config import BY_SOURCE_DIR, REGIONS

API_URL = "https://www.federalregister.gov/api/v1/documents.json"

# Pollutant detection — same patterns as collect_cpr.py (kept local; module stays self-contained).
POLLUTANT_PATTERNS: dict[str, list[str]] = {
    "PM2.5": [r"pm\s*2\.?5", r"fine particulate", r"fine dust"],
    "PM10": [r"pm\s*10", r"coarse particulate"],
    "NO2": [r"no2", r"nitrogen dioxide", r"nox"],
    "SO2": [r"so2", r"sulfur dioxide", r"sulphur dioxide"],
    "O3": [r"ozone", r"o3"],
    "CO": [r"\bco\b", r"carbon monoxide"],
    "VOC": [r"\bvoc\b", r"volatile organic"],
    "BC": [r"black carbon", r"soot"],
    "NH3": [r"ammonia", r"nh3"],
}

# Air-quality relevance keywords (second-pass filter — drops Coast Guard / unrelated EPA rules).
AQ_KEYWORDS: set[str] = {
    "air quality", "air pollution", "clean air", "ambient air", "particulate matter",
    "pm2.5", "pm10", "fine dust", "nitrogen dioxide", "sulfur dioxide", "ozone",
    "emission standard", "emissions standard", "vehicle emission", "industrial emission",
    "smog", "haze", "air pollutant", "national ambient air quality", "air quality management",
    "diesel emission", "black carbon", "ozone standard", "naaqs",
}


def strip_control_chars(text: str | None) -> str:
    """Remove C0 control chars (Federal Register abstracts contain them) and collapse whitespace."""
    if not text:
        return ""
    # Drop non-whitespace C0 controls entirely; keep \t \n \r for whitespace collapse below.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def map_fr_type(fr_type: str) -> str:
    """Map a Federal Register document type to our policy type. FR feed is RULE-only → regulation."""
    # config.POLICY_TYPES guarantees "regulation" exists; FR returns "Rule"/"Proposed Rule".
    return "regulation"


def detect_pollutants(text: str) -> list[str]:
    """Detect pollutants mentioned in text (title + abstract)."""
    text_lower = text.lower()
    found: list[str] = []
    for pollutant, patterns in POLLUTANT_PATTERNS.items():
        if any(re.search(p, text_lower) for p in patterns):
            found.append(pollutant)
    return found


def is_air_quality_related(title: str, abstract: str) -> tuple[bool, float]:
    """Second-pass relevance check. Returns (is_related, score). Each keyword hit = +2.0."""
    text = f"{title} {abstract}".lower()
    score = 0.0
    for kw in AQ_KEYWORDS:
        if kw in text:
            score += 2.0
    return score >= 2.0, score


def extract_year(date_str: str | None) -> int | None:
    """Extract a 4-digit year from a date string (collect_cpr.py parity)."""
    if not date_str:
        return None
    match = re.search(r"(\d{4})", str(date_str))
    return int(match.group(1)) if match else None


def fr_doc_to_policy(doc: dict, idx: int) -> dict | None:
    """Convert one Federal Register API document into a standard policy dict, or None if unrelated."""
    title = doc.get("title", "") or ""
    abstract = doc.get("abstract") or ""

    related, _score = is_air_quality_related(title, abstract)
    if not related:
        return None

    pub_date = doc.get("publication_date", "") or ""
    effective = doc.get("effective_on") or pub_date or ""
    doc_number = doc.get("document_number", str(idx))

    return {
        "id": f"FR-US-{pub_date[:4]}-{doc_number}",
        "countryCode": "US",
        "region": REGIONS.get("US", "Unknown"),
        "name": title,
        "nameLocal": "",
        "type": map_fr_type(doc.get("type", "")),
        "status": "in_force",
        "adoptedDate": pub_date,
        "effectiveDate": effective,
        "sector": [],  # FR API has no structured sector field — no guessing (core-rules §3-2)
        "pollutants": detect_pollutants(f"{title} {abstract}"),
        "scope": "national",
        "targets": [],
        "standards": [],
        "source": "US Federal Register",
        "sourceUrl": doc.get("html_url", ""),
        "description": strip_control_chars(abstract)[:500],
        "sdidTreatmentYear": extract_year(pub_date),
    }


def fetch_federal_register(per_page: int = 100, max_pages: int = 3) -> list[dict]:
    """Fetch EPA air-quality RULE documents (keyless). Resilient: returns [] on failure."""
    params = [
        ("conditions[agencies][]", "environmental-protection-agency"),
        ("conditions[type]", "RULE"),
        ("conditions[term]", "air quality"),
        ("per_page", str(per_page)),
        ("order", "newest"),
        ("fields[]", "document_number"),
        ("fields[]", "title"),
        ("fields[]", "abstract"),
        ("fields[]", "publication_date"),
        ("fields[]", "effective_on"),
        ("fields[]", "html_url"),
        ("fields[]", "type"),
        ("fields[]", "agencies"),
    ]
    url: str | None = f"{API_URL}?{urllib.parse.urlencode(params)}"
    docs: list[dict] = []
    pages = 0

    while url and pages < max_pages:
        pages += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AirLens-policy-collect"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
        except Exception as e:  # noqa: BLE001 — resilience: never throw, skip on failure
            logger.warning(f"Federal Register fetch failed (page {pages}): {e}")
            break
        results = data.get("results") or []
        docs.extend(results)
        url = data.get("next_page_url")

    logger.info(f"Fetched {len(docs)} Federal Register documents across {pages} page(s)")
    return docs


def main() -> None:
    docs = fetch_federal_register()
    policies: list[dict] = []
    for i, doc in enumerate(docs):
        policy = fr_doc_to_policy(doc, i)
        if policy is not None:
            policies.append(policy)

    BY_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = BY_SOURCE_DIR / "federal_register.json"
    with open(output_path, "w") as f:
        json.dump(policies, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved {len(policies)} air quality policies to {output_path}")
    countries = {p["countryCode"] for p in policies}
    logger.info(f"Countries covered: {len(countries)}")
    by_type: dict[str, int] = {}
    for p in policies:
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    logger.info(f"By type: {by_type}")


if __name__ == "__main__":
    main()
