#!/usr/bin/env python3
"""NOAA GFS 1° 바람 수집기 (surface 10m AGL + 850hPa).

Open-Meteo 10° 지표면 재샘플을 대체. 구 산출물 `current-wind-surface-level-gfs-1.0.json`
은 그 이름을 달고 있었으나 실제로는 Open-Meteo 10° 지표면 데이터였고(파일명 위장),
소비자 전원이 여기 산출물로 옮겨간 뒤 삭제됐다(2026-07-24). 여기서는 실제 NOAA GFS 1°
grib 을 받아 이름과 내용을 일치시킨다.

소스: gfs.YYYYMMDD/HH/atmos/gfs.tHHz.pgrb2.1p00.f000 (+.idx) — AWS Open Data S3 무계정.
  - surface = UGRD/VGRD `10 m above ground`
  - 850hPa  = UGRD/VGRD `850 mb`
부분 다운로드 = `.idx` byte-range (collect_noaa_aq 의 parse_idx_range 재사용).
GRIB decode = eccodes `grib_get_data` (CI apt libeccodes-tools). pip 신규 deps 0.
신규 secret 0 (S3 anonymous).

출력 계약은 기존 소비자(WindField.fromGFSRecords)와 호환 — [u, v] 2 레코드,
header 는 nx/ny/lo1/la1/dx/dy 만 구조분해되므로 신규 필드(level/resolution/generatedAt)는
무시된다.

정직성:
  - 격자가 하나라도 비면 exit 1 (0 으로 조용히 메우지 않는다).
  - 한 레벨이라도 실패하면 전체 exit 1 (850 자리에 surface 가 놓이는 일이 없도록).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

# 형제 모듈 재사용(단일 소스) — 워크플로가 repo root 에서 실행하므로 경로 삽입.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_noaa_aq import (  # noqa: E402
    grib_decode,
    http_get,
    normalize_lon,
    parse_idx_range,
)

S3_GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"

# GFS 1° 전구 격자 — 행0 = 북단(90), 열0 = -180 (우리 JSON 계약)
NX, NY = 360, 181
DX, DY = 1.0, 1.0
LA1, LO1 = 90.0, -180.0

FALLBACK_STRIDE = 2  # git 커밋 폴백은 2° 다운샘플 (히스토리 볼륨 억제)

LEVELS = [
    {"key": "surface", "idx_lev": "10 m above ground", "slug": "wind-surface"},
    {"key": "850hPa", "idx_lev": "850 mb", "slug": "wind-850hpa"},
]

CYCLE_HOURS = ("18", "12", "06", "00")


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def gfs_key(ymd: str, hh: str) -> str:
    """S3 object key (f000 = analysis)."""
    return f"gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.1p00.f000"


def idx_match(var: str, level: str) -> str:
    """`.idx` 라인에서 찾을 substring. f000 세그먼트는 anl."""
    return f"{var}:{level}:anl:"


def cycle_candidates(now: datetime):
    """최신 우선 cycle 후보 (오늘→2일 전, 18/12/06/00). 미래 cycle 은 제외."""
    for day_offset in range(0, 3):
        d = now - timedelta(days=day_offset)
        ymd = d.strftime("%Y%m%d")
        for hh in CYCLE_HOURS:
            if day_offset == 0 and int(hh) > now.hour:
                continue  # 아직 발행되지 않은 미래 cycle
            yield ymd, hh


def build_dense(rows, nx=NX, ny=NY, la1=LA1, lo1=LO1, dx=DX, dy=DY):
    """(lat, lon, val) → row-major dense 배열 (행0 = la1 = 북단, 열0 = lo1).

    GFS native 경도는 0..359 이므로 normalize_lon 으로 -180 원점에 맞춘다.
    이 roll 을 빠뜨리면 지구본이 정확히 180° 어긋난다.
    """
    arr = [None] * (nx * ny)
    for lat, lon, val in rows:
        j = round((la1 - lat) / dy)          # 행0 = 북단
        i = round((normalize_lon(lon) - lo1) / dx)
        if 0 <= j < ny and 0 <= i < nx:
            arr[j * nx + i] = val
    missing = sum(1 for v in arr if v is None)
    if missing:
        raise ValueError(
            f"incomplete grid: {missing}/{nx * ny} cells missing — "
            "0 으로 메우지 않고 실패한다 (정직성)"
        )
    return arr


def downsample(dense, nx: int, ny: int, stride: int):
    """stride 간격 셀만 남긴 저해상도 격자 → (data, nx2, ny2)."""
    out = []
    for j in range(0, ny, stride):
        for i in range(0, nx, stride):
            out.append(dense[j * nx + i])
    return out, len(range(0, nx, stride)), len(range(0, ny, stride))


def build_record(data, header: dict) -> dict:
    return {"header": header, "data": [round(v, 2) for v in data]}


def make_header(level: str, ref_time: str, generated_at: str,
                nx: int, ny: int, dx: float, dy: float) -> dict:
    return {
        "nx": nx, "ny": ny, "lo1": LO1, "la1": LA1, "dx": dx, "dy": dy,
        "refTime": ref_time,          # GFS cycle (f000 valid time)
        "forecastTime": 0,
        "generatedAt": generated_at,  # 파이프라인 건강 신호 (freshness probe 가 잰다)
        "level": level,
        "resolution": dx,
        "centerName": "NOAA/NCEP GFS 1.0",
    }


# ────────────────────────────── IO ──────────────────────────────

def find_latest_gfs_cycle(now: datetime = None):
    """UGRD/VGRD × (10m, 850mb) 4 레코드가 모두 있는 최신 cycle. 없으면 RuntimeError."""
    now = now or datetime.now(timezone.utc)
    required = [idx_match(var, lev["idx_lev"]) for lev in LEVELS for var in ("UGRD", "VGRD")]
    for ymd, hh in cycle_candidates(now):
        key = gfs_key(ymd, hh)
        try:
            idx = http_get(f"{S3_GFS}/{key}.idx", timeout=30).decode("utf-8", "replace")
        except urllib.error.HTTPError:
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  cycle {ymd} {hh}z probe: {e}", file=sys.stderr)
            continue
        if all(m in idx for m in required):
            print(f"  cycle: {ymd} {hh}z")
            return ymd, hh, key, idx
    raise RuntimeError("no GFS 1p00 cycle with UGRD/VGRD (10m + 850mb) found (today..2d ago)")


def fetch_component(grib_url: str, idx_text: str, var: str, level: str):
    """한 GRIB 레코드만 byte-range 로 받아 dense 배열로."""
    rng = parse_idx_range(idx_text, idx_match(var, level))
    if not rng:
        raise RuntimeError(f"idx match 없음: {var} {level}")
    start, end = rng
    range_header = f"bytes={start}-{end}" if end is not None else f"bytes={start}-"
    rows = grib_decode(http_get(grib_url, range_header=range_header))
    if not rows:
        raise RuntimeError(f"grib decode 0 rows: {var} {level}")
    return build_dense(rows)


def main() -> int:
    ymd, hh, key, idx_text = find_latest_gfs_cycle()
    grib_url = f"{S3_GFS}/{key}"
    ref_time = datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]), int(hh),
                        tzinfo=timezone.utc).isoformat()
    generated_at = datetime.now(timezone.utc).isoformat()

    for lev in LEVELS:
        # 한 레벨이라도 실패하면 예외 → exit 1. 부분 성공 금지.
        u = fetch_component(grib_url, idx_text, "UGRD", lev["idx_lev"])
        v = fetch_component(grib_url, idx_text, "VGRD", lev["idx_lev"])

        hdr = make_header(lev["key"], ref_time, generated_at, NX, NY, DX, DY)
        with open(f"{lev['slug']}.json", "w") as f:
            json.dump([build_record(u, hdr), build_record(v, hdr)], f, separators=(",", ":"))

        u2, nx2, ny2 = downsample(u, NX, NY, FALLBACK_STRIDE)
        v2, _, _ = downsample(v, NX, NY, FALLBACK_STRIDE)
        hdr2 = make_header(lev["key"], ref_time, generated_at,
                           nx2, ny2, DX * FALLBACK_STRIDE, DY * FALLBACK_STRIDE)
        with open(f"{lev['slug']}-2deg.json", "w") as f:
            json.dump([build_record(u2, hdr2), build_record(v2, hdr2)], f, separators=(",", ":"))

        speeds = [(a * a + b * b) ** 0.5 for a, b in zip(u, v)]
        print(f"  {lev['key']}: {NX * NY} pts -> {lev['slug']}.json "
              f"(max wind {max(speeds):.1f} m/s) + {lev['slug']}-2deg.json ({nx2 * ny2} pts)")

    print(f"Done: NOAA GFS 1.0 {ymd} {hh}z -> surface + 850hPa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
