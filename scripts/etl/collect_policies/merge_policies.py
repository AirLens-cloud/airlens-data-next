#!/usr/bin/env python3
"""
merge_policies.py — Merge policies from all sources into unified registry.

Reads by_source/*.json, deduplicates, and outputs:
  - registry/policy_registry.json (full merged)
  - registry/by_country/{CC}.json (per-country)

Usage:
  .venv/bin/python scripts/collect_policies/merge_policies.py
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

from collect_policies.config import BY_COUNTRY_DIR, BY_SOURCE_DIR, REGISTRY_DIR, REGIONS


def normalize_name(name: str) -> str:
    """Normalize policy name for dedup comparison."""
    import re
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def load_source(path: Path) -> list[dict]:
    """Load a source JSON file."""
    if not path.exists():
        logger.warning(f"Source not found: {path}")
        return []
    with open(path) as f:
        data = json.load(f)
    logger.info(f"Loaded {len(data)} policies from {path.name}")
    return data


def deduplicate(policies: list[dict]) -> list[dict]:
    """Remove duplicate policies (same country + similar name)."""
    seen: dict[str, dict] = {}  # key -> best policy

    for p in policies:
        cc = p.get("countryCode", "")
        name_norm = normalize_name(p.get("name", ""))
        key = f"{cc}:{name_norm[:60]}"

        if key in seen:
            existing = seen[key]
            # Keep the one with more data (longer description, more standards)
            existing_richness = len(existing.get("description", "")) + len(existing.get("standards", []))
            new_richness = len(p.get("description", "")) + len(p.get("standards", []))
            if new_richness > existing_richness:
                seen[key] = p
        else:
            seen[key] = p

    return list(seen.values())


def merge_all() -> list[dict]:
    """Merge all source files."""
    all_policies: list[dict] = []

    source_files = sorted(BY_SOURCE_DIR.glob("*.json"))
    for src in source_files:
        if src.name.startswith("cpr_cache"):
            continue
        policies = load_source(src)
        all_policies.extend(policies)

    logger.info(f"Total before dedup: {len(all_policies)}")
    merged = deduplicate(all_policies)
    logger.info(f"Total after dedup: {len(merged)}")

    # Sort by country, then date
    merged.sort(key=lambda p: (p.get("countryCode", ""), p.get("adoptedDate", "")))

    return merged


def write_by_country(policies: list[dict]) -> None:
    """Split policies into per-country files."""
    BY_COUNTRY_DIR.mkdir(parents=True, exist_ok=True)

    by_cc: dict[str, list[dict]] = defaultdict(list)
    for p in policies:
        cc = p.get("countryCode", "XX")
        by_cc[cc].append(p)

    for cc, country_policies in by_cc.items():
        out_path = BY_COUNTRY_DIR / f"{cc}.json"
        with open(out_path, "w") as f:
            json.dump(country_policies, f, ensure_ascii=False, indent=2)

    logger.info(f"Wrote {len(by_cc)} country files to {BY_COUNTRY_DIR}")


def main() -> None:
    merged = merge_all()

    # Write full registry
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    registry_path = REGISTRY_DIR / "policy_registry.json"
    with open(registry_path, "w") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    logger.info(f"Wrote {len(merged)} policies to {registry_path}")

    # Write per-country files
    write_by_country(merged)

    # Summary stats
    countries = {p["countryCode"] for p in merged}
    by_type: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    for p in merged:
        by_type[p.get("type", "unknown")] += 1
        by_source[p.get("source", "unknown")] += 1

    logger.info(f"=== Merge Summary ===")
    logger.info(f"Total policies: {len(merged)}")
    logger.info(f"Countries: {len(countries)}")
    logger.info(f"By type: {dict(by_type)}")
    logger.info(f"By source: {dict(by_source)}")

    # WHO compliance stats
    who_standards = [p for p in merged if p.get("standards")]
    logger.info(f"Countries with AQ standards: {len(who_standards)}")


if __name__ == "__main__":
    main()
