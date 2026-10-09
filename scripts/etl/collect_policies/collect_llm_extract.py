#!/usr/bin/env python3
"""
collect_llm_extract.py — LLM extraction of free-text air quality policies (opt-in).

Covers sources with no machine-readable feed (China MEE / India CPCB free-text /
PDF policy pages). Free-text → gpt-4o-mini → standard policy dict. Gated behind
RUN_LLM_EXTRACT so the default $0 / LLM-0 cron is unaffected (SOURCES.md contract).

Operation:
  - Add targets to llm_sources.json (url / country / kind: pdf|html).
  - Set OPENAI_API_KEY (GitHub Actions secret) and RUN_LLM_EXTRACT=1.
  - Deterministic fields (countryCode, region) come from the source entry, never
    the LLM, to bound hallucination. type/pollutants are clamped to config values.
    source/sourceUrl mark provenance so output is verifiable against the original.

Usage:
  RUN_LLM_EXTRACT=1 python3 scripts/etl/collect_policies/collect_llm_extract.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # repo root
sys.path.insert(0, str(ROOT / "scripts" / "etl"))

from collect_policies.config import BY_SOURCE_DIR, POLICY_TYPES, POLLUTANTS, REGIONS
from collect_policies import llm_extract, pdf_extract

SOURCES_FILE = Path(__file__).resolve().parent / "llm_sources.json"
MAX_TEXT_CHARS = 12_000

SYSTEM_PROMPT = (
    "You extract air quality policy metadata from government policy text. "
    "Output ONLY valid JSON with keys: name (English), name_local, "
    "type (one of: law|regulation|standard|plan|strategy|guideline|decree|order|act), "
    "status (in_force|enacted|amended|proposed), adopted_date (YYYY-MM-DD or null), "
    "effective_date (YYYY-MM-DD or null), sectors (array of strings), "
    "pollutants (array; subset of PM2.5/PM10/NO2/SO2/O3/CO/VOC/NH3/BC), "
    "description (<=400 chars, English). If a field is not stated, use null or []. "
    "Do not invent values."
)


def build_extraction_prompt(text: str, country_hint: str) -> tuple[str, str]:
    """Return (system, user) prompt for one policy document."""
    user = f"Country: {country_hint}\n\nPolicy document text:\n{text[:MAX_TEXT_CHARS]}"
    return SYSTEM_PROMPT, user


def extract_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    match = re.search(r"(\d{4})", str(date_str))
    return int(match.group(1)) if match else None


def _stable_id(country: str, source_url: str) -> str:
    """URL-derived stable id, position-independent.

    Earlier the id embedded the list index, so removing/reordering an entry in
    llm_sources.json churned ids and orphaned old rows (upsert never deletes).
    Hashing the source URL keeps the same target → same id across edits, so
    merge-duplicates updates the row in place.
    """
    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:8]
    return f"LLM-{country}-{digest}"


def llm_json_to_policy(obj: dict, source_url: str, country: str) -> dict:
    """Map an LLM JSON object to a standard policy dict with deterministic guards."""
    adopted = obj.get("adopted_date") or ""
    ptype = obj.get("type", "regulation")
    if ptype not in POLICY_TYPES:
        ptype = "regulation"  # clamp to config whitelist (no hallucinated types)
    pollutants = [p for p in (obj.get("pollutants") or []) if p in POLLUTANTS]
    sectors = obj.get("sectors") or []
    if not isinstance(sectors, list):
        sectors = []
    return {
        "id": _stable_id(country, source_url),
        "countryCode": country,  # deterministic from source, not the LLM
        "region": REGIONS.get(country, "Unknown"),
        "name": obj.get("name", "") or "",
        "nameLocal": obj.get("name_local", "") or "",
        "type": ptype,
        "status": obj.get("status", "in_force") or "in_force",
        "adoptedDate": adopted,
        "effectiveDate": obj.get("effective_date") or adopted or "",
        "sector": sectors,
        "pollutants": pollutants,
        "scope": "national",
        "targets": [],
        "standards": [],
        "source": "LLM-extracted (gpt-4o-mini)",  # Glass-box provenance
        "sourceUrl": source_url,                   # verifiable against original
        "description": (obj.get("description") or "")[:500],
        "sdidTreatmentYear": extract_year(adopted),
    }


def _html_to_text(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AirLens-policy-collect"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001 — resilience
        logger.warning(f"HTML fetch failed ({url}): {e}")
        return ""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return pdf_extract.normalize_whitespace(text)


def load_text(source: dict) -> str:
    """Fetch source text (pdf via pdf_extract, else html→text)."""
    url = source.get("url", "")
    if source.get("kind", "html") == "pdf":
        return pdf_extract.extract_pdf(url)
    return _html_to_text(url)


def extract_one(source: dict) -> dict | None:
    """Run one source through fetch → LLM → standard dict. None on any failure."""
    text = load_text(source)
    if not text or len(text) < 100:
        logger.warning(f"No usable text for {source.get('url')}")
        return None
    country = source.get("country", "")
    system, user = build_extraction_prompt(text, country)
    try:
        obj = llm_extract.chat_complete_json(system, user)
    except llm_extract.LLMExtractError as e:
        logger.warning(f"LLM extract failed for {source.get('url')}: {e}")
        return None
    return llm_json_to_policy(obj, source.get("url", ""), country)


def load_sources() -> list[dict]:
    if not SOURCES_FILE.exists():
        return []
    data = json.loads(SOURCES_FILE.read_text())
    return data.get("sources", []) if isinstance(data, dict) else data


def main() -> None:
    sources = load_sources()
    BY_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = BY_SOURCE_DIR / "llm_extract.json"

    if not sources:
        logger.info("No LLM sources configured (llm_sources.json sources empty). Skipping — writing [].")
        output_path.write_text("[]")
        return

    policies: list[dict] = []
    for src in sources:
        policy = extract_one(src)
        if policy is not None:
            policies.append(policy)

    output_path.write_text(json.dumps(policies, ensure_ascii=False, indent=2))
    logger.info(f"Saved {len(policies)} LLM-extracted policies to {output_path}")


if __name__ == "__main__":
    main()
