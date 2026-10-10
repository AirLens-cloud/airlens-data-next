#!/usr/bin/env python3
"""NOAA GEFS-Aerosols 글로벌 PM 수집기 — mac 무료 파이프라인용 envelope 스키마.

설계 배경(비공개 내부 노트 2026-07-16 — 동작 정의는 코드·테스트·계약):
§"무료 글로벌 소스 판정" — "NOAA GEFS-Chem ... PM 기본값과 CAMS 장애 폴백".

이 파일은 기존 `collect_noaa_aq.py`(Globe 프론트가 소비하는 `current-pm25-grid.json`
레거시 계약, 손대지 않음)와 **같은 원자료**(GEFS-Aerosols PMTF/PMTC GRIB)를 재사용하되,
출력을 `mac_aq_adapter.build_grid_reading()` envelope 계약으로 조립한다. mac 파이프라인의
producer 산출물과 기존 Supabase Globe 파이프라인 산출물은 서로 다른 소비자를 위한 별도
스냅샷이라 스키마를 공유하지 않는다 ([[feedback_shared_name_hides_different_quantity]] 정합
— 같은 소스, 다른 계약).

원자료: gefs.YYYYMMDD/HH/chem/pgrb2ap25/gefs.chem.tHHz.a2d_0p25.f000.grib2 (+.idx)
  - PM2.5 = GRIB record `PMTF:surface:...aerosol_size <2.5e-06`
  - PM10  = GRIB record `PMTC:surface:...aerosol_size <1e-05`
재사용 = `collect_noaa_aq.{parse_idx_range,http_get,find_latest_cycle,grib_decode,
unit_factor,cycle_to_ts_ms}` (byte-range 다운로드 + eccodes 디코드) +
`collect_gfs_wind.build_dense`(row-major dense 배열, la1=북단/lo1=-180 관례 — 이미
그 형제 수집기에서 검증된 함수, 여기서 재테스트하지 않는다).

정직성 (형제 수집기와 동일 원칙):
  - 격자가 하나라도 불완전하면(`build_dense` 가 ValueError) exit 1 — 0 으로 메우지 않는다.
  - 두 오염물질(PM2.5/PM10) 중 하나라도 실패하면 전체 실패 — 절반짜리 snapshot 발행 금지.
  - 성공한 레코드만 임시 파일에 쓰고 `os.replace` 로 교체 — 실패 시 기존 last-good 파일은
    절대 건드리지 않는다.
  - 이 스크립트는 CAMS collector(`collect_cams_global.py`)와 완전히 독립된 프로세스다.
    한쪽이 실패해도 다른 쪽 실행에 영향을 주지 않는다(별도 exit code, 공유 상태 없음).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

# 형제 모듈 재사용(단일 소스) — repo root 에서 실행 가정.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gfs_wind import build_dense  # noqa: E402
from collect_noaa_aq import (  # noqa: E402
    S3_BASE,
    cycle_to_ts_ms,
    find_latest_cycle,
    grib_decode,
    http_get,
    parse_idx_range,
    unit_factor,
)
import mac_aq_adapter as adapter  # noqa: E402

GRID_RES_DEG = float(os.environ.get("MAC_GEFS_CHEM_RES_DEG", "1.0"))
OUTPUT_PATH = os.environ.get("MAC_GEFS_CHEM_OUTPUT_PATH", "gefs-chem-global-snapshot.json")

ATTRIBUTION = (
    "NOAA/NCEP GEFS-Aerosols (U.S. public domain, "
    "https://registry.opendata.aws/noaa-gefs/)"
)

EXPECTED_POLLUTANT_COUNT = 2  # pm25 + pm10 (GEFS-Aerosols 는 가스를 제공하지 않는다)
FRESHNESS_CUTOFF_HOURS = 6  # GEFS chem cycle 간격(design SOT §"스케줄")

VARS = [
    {
        "idx_match": "PMTF:surface:anl:aerosol=Total aerosol:aerosol_size <2.5e-06",
        "pollutant_key": "pm25",
        "source_variable": "PMTF",
    },
    {
        "idx_match": "PMTC:surface:anl:aerosol=Total aerosol:aerosol_size <1e-05",
        "pollutant_key": "pm10",
        "source_variable": "PMTC",
    },
]


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def grid_header_for(res_deg: float) -> dict:
    """la1=북단(90)/lo1=-180 관례(collect_gfs_wind 와 동일) — nx/ny 는 res_deg 로 계산."""
    nx = round(360.0 / res_deg)
    ny = round(180.0 / res_deg) + 1
    return {"nx": nx, "ny": ny, "la1": 90.0, "lo1": -180.0, "dx": res_deg, "dy": res_deg}


def rows_to_ugm3(rows: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """GRIB 값(kg/m³ 또는 이미 µg/m³ 추정)을 µg/m³ 로 정규화. 0 으로 메우지 않는다."""
    if not rows:
        raise ValueError("empty rows — 0 으로 메우지 않는다")
    factor = unit_factor(max(v for _la, _lo, v in rows))
    return [(la, lo, round(v * factor, 2)) for la, lo, v in rows]


def build_pollutant_block(rows_ugm3: list[tuple[float, float, float]], header: dict,
                           source_variable: str) -> dict:
    """dense 배열 + provenance — `mac_aq_adapter.build_grid_reading` 의 pollutants_grid[key] 값."""
    dense = build_dense(
        rows_ugm3, nx=header["nx"], ny=header["ny"],
        la1=header["la1"], lo1=header["lo1"], dx=header["dx"], dy=header["dy"],
    )
    return {
        "unit": "ug/m3",
        "sourceVariable": source_variable,
        "conversion": "GRIB kg/m3 x 1e9 (already ug/m3 if >=1)",
        "data": dense,
    }


def compute_expires_at(generated_at_iso: str, hours: float) -> str:
    """generatedAt(ISO) + hours → expiresAt(ISO, Z suffix)."""
    dt = datetime.fromisoformat(generated_at_iso.replace("Z", "+00:00"))
    return (dt + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def assemble_reading(pollutant_blocks: dict[str, dict], header: dict, generated_at: str,
                      valid_at: str, age_hours: float) -> dict:
    """envelope + grid_reading 조립. 실패 시(불완전) 호출자가 발행하지 않는다."""
    quality = adapter.estimate_quality(
        pollutant_count=len(pollutant_blocks),
        expected_count=EXPECTED_POLLUTANT_COUNT,
        age_hours=age_hours,
    )
    envelope = adapter.build_envelope(
        kind="analysis",
        source="NOAA GEFS-Aerosols",
        source_version=valid_at,
        generated_at=generated_at,
        observed_at=None,
        valid_at=valid_at,
        expires_at=compute_expires_at(generated_at, FRESHNESS_CUTOFF_HOURS),
        resolution_km=round(header["dx"] * 111.0, 1),
        attribution=ATTRIBUTION,
        quality=quality,
    )
    reading = adapter.build_grid_reading(
        envelope,
        {"nx": header["nx"], "ny": header["ny"], "lo1": header["lo1"], "la1": header["la1"],
         "dx": header["dx"], "dy": header["dy"]},
        pollutant_blocks,
    )
    errors = adapter.validate_grid_reading(reading)
    if errors:
        raise ValueError(f"assembled reading failed validation: {errors}")
    return reading


# ────────────────────────────── IO (네트워크/subprocess, 미테스트 — 형제 관례) ──────────────────────────────

def fetch_and_decode(ymd: str, hh: str, key: str, idx_text: str, header: dict) -> dict:
    """두 오염물질 모두 성공해야 반환 — 하나라도 실패하면 raise (부분 발행 금지)."""
    grib_url = f"{S3_BASE}/{key}"
    blocks = {}
    for spec in VARS:
        rng = parse_idx_range(idx_text, spec["idx_match"])
        if not rng:
            raise RuntimeError(f"idx match 없음: {spec['pollutant_key']} — 발행 중단")
        start, end = rng
        range_header = f"bytes={start}-{end}" if end is not None else f"bytes={start}-"
        grib_bytes = http_get(grib_url, range_header=range_header)
        rows = grib_decode(grib_bytes)
        if not rows:
            raise RuntimeError(f"{spec['pollutant_key']}: decode 0 rows — 발행 중단")
        rows_ugm3 = rows_to_ugm3(rows)
        blocks[spec["pollutant_key"]] = build_pollutant_block(
            rows_ugm3, header, spec["source_variable"],
        )
    return blocks


def main() -> int:
    try:
        ymd, hh, key, idx_text = find_latest_cycle()
        valid_ts_ms = cycle_to_ts_ms(ymd, hh)
        valid_at = datetime.fromtimestamp(valid_ts_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        age_hours = (datetime.now(timezone.utc)
                     - datetime.fromtimestamp(valid_ts_ms / 1000, tz=timezone.utc)
                     ).total_seconds() / 3600

        header = grid_header_for(GRID_RES_DEG)
        blocks = fetch_and_decode(ymd, hh, key, idx_text, header)
        reading = assemble_reading(blocks, header, generated_at, valid_at, age_hours)
    except Exception as e:  # noqa: BLE001 — outage 는 fail-loud, last-good 은 안 건드림
        print(f"ERROR: GEFS-chem collection failed — {e}", file=sys.stderr)
        print("  기존 snapshot(있다면) 은 그대로 유지한다.", file=sys.stderr)
        return 1

    tmp_path = f"{OUTPUT_PATH}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(reading, f, separators=(",", ":"))
    os.replace(tmp_path, OUTPUT_PATH)
    print(f"Done: NOAA GEFS-Aerosols {ymd} {hh}z -> {OUTPUT_PATH} "
          f"(pollutants={list(blocks.keys())}, grade={reading['quality']['grade']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
