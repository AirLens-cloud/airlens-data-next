#!/usr/bin/env python3
"""NOAA GEFS-Aerosols 글로벌 PM2.5/PM10 그리드 수집기.

Open-Meteo Air Quality API 대체. Open-Meteo 무키 free 티어는 약관상 "비상업 전용"
(open-meteo.com/en/terms) — AirLens 는 유료 구독(상업)이므로 위반. NOAA GEFS-Aerosols
는 US public domain + AWS Open Data S3 무계정 + real-time 이라 완전 무료·상업 OK.

출력은 기존 `current-{pm25,pm10}-grid.json` 계약(variable/resolution/timestamp/
nLat·nLon·latMin·lonMin·dLat·dLon/points[])을 그대로 재현 → downstream
(global-grid-snapshot Edge Fn, airQualityGrid.ts) 무변경.

소스: gefs.YYYYMMDD/HH/chem/pgrb2ap25/gefs.chem.tHHz.a2d_0p25.f000.grib2 (+.idx)
  - PM2.5 = GRIB record `PMTF:surface:...Total aerosol:aerosol_size <2.5e-06`
  - PM10  = GRIB record `PMTC:surface:...Total aerosol:aerosol_size <1e-05`
GRIB decode = eccodes `grib_get_data` (CI apt libeccodes-tools). pip 신규 deps 0.
신규 secret 0 (S3 anonymous). 단위: GRIB kg/m³ → ×1e9 µg/m³ (계약 단위).
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

S3_BASE = "https://noaa-gefs-pds.s3.amazonaws.com"
GRID_RES_DEG = float(os.environ.get("AQ_GRID_RES_DEG", "1.0"))  # GEFS native 0.25° → subsample

# 단위 판정 사후 확인용 경계 (µg/m³). 자릿수 사고만 잡는 넉넉한 창 —
# 판정이 틀리면 중앙값이 1e-9 배 또는 1e9 배로 벗어나므로 이 폭으로 충분하다.
# 좁히면 산불철·청정기의 정상 변동을 발행 실패로 만든다.
# 단위 판정 사후 확인의 허용 대역. **넓은 것이 의도다.**
#
# 이 게이트가 잡아야 하는 사고는 자릿수 사고뿐이다 — 변환을 놓치면 1e-9 배,
# 없어야 할 변환을 하면 1e9 배. 반면 이 게이트가 틀리면 정상 발행이 멈추고
# 웹은 옛 격자를 계속 보여준다. 즉 **위양성이 위음성보다 비싸다.**
#
# 그래서 경계를 실측 중앙값 근처에 두지 않고 자릿수째로 떨어뜨렸다.
# 실측(2026-09-04): pm2_5 p50 4.97 / pm10 p50 14.81.
#   - 하한 0.01 → 정상값의 1/500 ~ 1/1481. 미변환 격자(p50 ~1e-8)는 여전히
#     여섯 자릿수 아래라 확실히 걸린다.
#   - 상한 2000 → 정상값의 135~400배. 이중 변환(p50 ~1e10)은 여전히 걸린다.
# 직전 대역 [0.1, 200] 은 pm10 상단 여유가 14배뿐이라 전지구 먼지 대발생 같은
# 실제 사건에서 발행을 죽일 여지가 있었다 (council 리뷰 지적).
SANE_MEDIAN_MIN_UGM3 = 0.01
SANE_MEDIAN_MAX_UGM3 = 2000.0

# US EPA AQI 표(24h)의 **최상단 농도** — AQI 500 에 대응하는 상단 breakpoint.
# pm2_5 = 500.4 µg/m³, pm10 = 604 µg/m³.
#
# 이것은 물리상한이 **아니다.** 대기가 이 위로 못 간다는 주장이 아니라, 널리
# 쓰이는 보고 스케일이 여기서 끝난다는 사실일 뿐이다. 그래서 초과 셀을 지우지
# 않고 세기만 한다 — "불가능한 값" 이 아니라 "우리가 등급을 매길 수 없는 구간".
#
# 변수마다 값이 다른 것이 핵심이다. 하나의 상수를 두 변수에 돌려쓰면 PM10 을
# PM2.5 스케일로 재게 되고, 그건 이 조직이 이미 등재해 둔 실패 패턴이다
# (feedback_shared_name_hides_different_quantity). PM10 은 같은 대기질 등급에서
# PM2.5 의 2~4배 농도라 스케일 끝도 다르다.
EPA_AQI_SCALE_TOP_UGM3 = {"pm2_5": 500.4, "pm10": 604.0}

# 물리 상한 (hard reject) — 위의 EPA 스케일 상단과 역할이 다르다. 스케일 상단
# 초과는 "우리가 등급을 매길 수 없는 구간"이라 세기만 하지만, 이 상한 초과는
# 발표된 QC 관행·기록 극값 봉투 **위**의 값이라 음수와 같은 종류다 — 값이 아니라
# 결측이다 (2026-09-05 사용자 확정, 근거 정본 = 모노레포 CS 설계 보강 팩
# design-reinforcement-2026-09-05/findings.md Q9).
#   pm2_5 = 1000.0 — 발표된 QC 관행 그대로 (Mathieu-Campbell 2024: PM2.5 시간별
#     >1,000 µg/m³ 제외). 회수된 최고 시간별 기록 855.1 µg/m³ 위의 문헌 근거 수치.
#   pm10 = 10000.0 — 근거 승격 (2026-09-06 문헌 조사, 사용자 승인): ① US EPA
#     기준급 계측기 BAM-1020 의 공식 측정범위가 0–10,000 µg/m³ (Met One spec) ②
#     2021-03-15 고비사막 황사 때 중국 관측망 여러 지점이 9,985~9,999 에서
#     saturate (Atmos. Chem. Phys. 24:1041, 2024) — 즉 지상관측망이 신뢰성 있게
#     보고 가능한 상한과 일치한다. 회수된 극값 봉투(일평균 ≈7,414 · 시간평균
#     ≈6,460 µg/m³)도 이 아래. 알려진 반례 = 2009 시드니 "Red Dawn" TEOM 실측
#     15,366 µg/m³ (세계기록급 단일 지점) — NWP 격자에서 그 수준 셀은 실황보다
#     모델 발산일 가능성이 커 의도적으로 미포함. 2026-09-03 지구본 12,585 µg/m³
#     사고가 이 상한으로 차단된다.
# 센티널(9999 등)은 pm10 상한 *아래*라 이 range check 로는 잡히지 않는다 — GRIB
# 결측 마커는 decode 단계(`grib_decode` 의 -m 9999)가 별도 코드 경로로 이미
# 제거한다. 상한과 센티널을 한 검사로 합치지 않는 것이 Q9 의 분리 원칙이다.
PHYSICAL_MAX_UGM3 = {"pm2_5": 1000.0, "pm10": 10000.0}

# GRIB .idx 매칭 substring + 출력 schema variable + 파일 prefix
VARS = [
    {
        "idx_match": "PMTF:surface:anl:aerosol=Total aerosol:aerosol_size <2.5e-06",
        "variable": "pm2_5",
        "file": "current-pm25-grid",
    },
    {
        "idx_match": "PMTC:surface:anl:aerosol=Total aerosol:aerosol_size <1e-05",
        "variable": "pm10",
        "file": "current-pm10-grid",
    },
]


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def parse_idx_range(idx_text: str, match: str):
    """`.idx` 텍스트에서 match 레코드의 byte-range (start, end) 반환. 마지막이면 end=None.

    .idx line 포맷: `record:byte_offset:d=date:VAR:level:...`
    """
    lines = [ln for ln in idx_text.strip().split("\n") if ln.strip()]
    for i, line in enumerate(lines):
        if match in line:
            start = int(line.split(":")[1])
            end = None
            if i + 1 < len(lines):
                end = int(lines[i + 1].split(":")[1]) - 1
            return start, end
    return None


def normalize_lon(lon: float) -> float:
    """경도를 [-180, 180) 로 정규화 (GEFS GRIB 는 0..359.75 convention)."""
    lon = ((lon + 180.0) % 360.0) - 180.0
    # -180.0 유지, +180.0 → -180.0
    if lon == 180.0:
        lon = -180.0
    return lon


def unit_factor(sample_val: float) -> float:
    """GRIB 값 단위 추정. PM 질량농도 kg/m³(<1) → ×1e9 µg/m³. 이미 µg/m³(>=1)면 1.0.

    PM 은 청정대기도 µg/m³ 단위로 1 이상. kg/m³ 는 항상 <1e-6. 임계 1.0 이 안전 분리.

    `sample_val` 로는 중앙값을 넘긴다 (`representative_value`). 최댓값을 쓰면
    셀 하나가 전지구 발행을 결정한다 — decode 글리치로 어느 한 셀이 1.0 이상
    으로 나오는 순간 factor 가 1.0 으로 떨어져 **격자 전체가 변환되지 않은 채**
    (참값의 1e-9) 나간다. 그러면 모든 값이 0 에 수렴하므로 상한 검사도, 눈으로
    보는 min/max 로그도 통과한다. 중앙값은 셀 하나로 움직이지 않는다.
    """
    return 1e9 if (sample_val is not None and sample_val < 1.0) else 1.0


def representative_value(values) -> float | None:
    """단위 판정용 대표값 = 중앙값. 빈 입력이면 None."""
    finite = sorted(v for v in values if v is not None and math.isfinite(v))
    if not finite:
        return None
    return finite[len(finite) // 2]


def converted_median_is_sane(median_ugm3: float) -> bool:
    """변환 후 중앙값이 지표 PM 배경농도대에 떨어지는지 — 단위 판정의 사후 확인.

    판정이 어느 방향으로 틀려도 중앙값은 자릿수째로 벗어난다: 변환을 놓치면
    1e-9 배로 0 에 붙고, 없어야 할 변환을 하면 1e9 배로 치솟는다. 경계는
    그래서 넉넉하다 — 정상값을 떨어뜨리는 것이 목적이 아니라 자릿수 사고를
    잡는 것이 목적이다. 실측 기준선: 2026-09-04 발행 pm2_5 격자의 중앙값
    4.97 µg/m³ (p25 1.69 / p75 9.89).
    """
    return SANE_MEDIAN_MIN_UGM3 <= median_ugm3 <= SANE_MEDIAN_MAX_UGM3


def subsample(rows, res_deg: float):
    """(lat, lon, val) rows 를 res_deg 격자에 정렬된 점만 남기고 정규화·정렬.

    GEFS native 0.25° 에서 res_deg 배수에 해당하는 셀만 유지. lon 정규화 후
    (lat asc, lon asc) row-major 정렬 (dense grid 보장). 반환: (points, meta).
    """
    tol = 1e-6
    kept = {}
    for lat, lon, val in rows:
        if abs((lat / res_deg) - round(lat / res_deg)) > tol:
            continue
        nlon = normalize_lon(lon)
        if abs((nlon / res_deg) - round(nlon / res_deg)) > tol:
            continue
        kept[(round(lat, 4), round(nlon, 4))] = val  # dedupe (0/360 겹침 방지)
    pts = sorted(kept.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    points = [{"lat": la, "lon": lo, "value": v} for (la, lo), v in pts]
    lats = sorted({la for (la, _lo) in kept})
    lons = sorted({lo for (_la, lo) in kept})
    meta = {
        "nLat": len(lats),
        "nLon": len(lons),
        "latMin": lats[0] if lats else 0,
        "lonMin": lons[0] if lons else 0,
    }
    return points, meta


def summarize(values) -> dict:
    """발행물에 실을 분포 요약. 계약(`contracts/current-aq-grid.v1`)의 보초.

    주의 — 이 p50 은 `converted_median_is_sane()` 이 보는 중앙값과 **모집단이
    다르다**: 저쪽은 subsample 이전의 GEFS native 0.25° 전체 rows 를, 이쪽은
    subsample 이후 1° 격자(points)를 본다. 정규 격자 decimation 이라 중앙값을
    크게 옮길 메커니즘은 약하지만, 두 검사가 같은 값을 본다고 가정하면 안 된다.
    그래도 무해한 이유는 대역이 자릿수 단위로 넓기 때문이다(SANE_MEDIAN_* 주석)
    — decimation 으로 중앙값이 몇 배 움직여도 판정은 바뀌지 않는다.
    """
    finite = sorted(v for v in values if v is not None and math.isfinite(v))
    if not finite:
        return {}
    n = len(finite)
    return {
        "n": n,
        "min": finite[0],
        "p50": finite[n // 2],
        # nearest-rank. `int(n * 0.99)` 은 0.99n 이 정확히 정수일 때 한 칸
        # 밀려서 p99 가 조용히 max 와 같아진다 (n=100 에서 재현). 발행 격자는
        # n=65,160 이라 프로덕션에선 안 드러나지만, 이 함수는 범용이라
        # 소규모 슬라이스에 재사용되면 "p99" 라는 이름으로 max 를 싣게 된다.
        "p99": finite[max(0, min(n - 1, math.ceil(n * 0.99) - 1))],
        "max": finite[-1],
    }


def nullify_impossible_negatives(points) -> int:
    """음수 질량농도 셀을 `None` 으로 떨구고 그 수를 돌려준다 (points 제자리 수정).

    **상한과 하한은 같은 종류의 규칙이 아니다.** 상한 초과는 세기만 한다 —
    대기가 EPA 스케일 위로 못 간다는 근거가 없으므로 그건 "불가능한 값" 이
    아니라 "우리가 등급을 매길 수 없는 구간" 이다 (`EPA_AQI_SCALE_TOP_UGM3`
    주석). 반면 **음수 질량농도는 물리적으로 성립하지 않는다** — 어떤 대기
    상태도 그 값을 내지 않으므로 이건 값이 아니라 결측이다.

    지상 경로엔 이 필터가 이미 있었다 (모노레포
    `models/pipeline/openaq_feed_reader.py:190-204` 의 `pm25 < 0` 거부).
    격자 경로에만 없었다.

    "세기만 하면 안 되나" 에 대한 답: 계약
    (`contracts/current-aq-grid.v1.schema.json`) 이 `points[].value.minimum: 0`
    이라 **음수 셀 하나가 격자 전체의 발행을 막는다**. 발행이 막히면 upload
    스텝이 건너뛰어지고 verify 는 직전 성공분에 200 을 받아 run 이 green 으로
    끝난다 — 아무 신호 없이 옛 격자가 계속 나가는 그 구조(§unit check 주석과
    동일). 한 셀을 정직하게 결측 처리하고 건수를 공시하는 편이, 전지구 격자를
    통째로 세우고 옛 데이터를 내보내는 것보다 낫다.

    0 이나 평균으로 대체하지 않는다 — 지상 경로와 같은 정신이다.
    """
    nulled = 0
    for p in points:
        v = p["value"]
        if v is not None and math.isfinite(v) and v < 0:
            p["value"] = None
            nulled += 1
    return nulled


def nullify_above_physical_max(points, variable: str) -> int:
    """물리 상한(`PHYSICAL_MAX_UGM3`) 초과 셀을 `None` 으로 떨구고 수를 돌려준다.

    음수와 같은 처리(결측)이고 같은 이유다 — 발표된 QC 관행·기록 극값 봉투 위의
    값은 대기 상태가 아니라 소스/디코드 결함이다. EPA 스케일 상단(세기만 함)과
    달리 여기는 문헌 근거가 있어 제거가 정당하다 (`PHYSICAL_MAX_UGM3` 주석).
    클립하지 않는 이유도 음수와 같다: 클립은 관측되지 않은 수치를 지어내는 것이고,
    None 셀은 소비자 쪽에서 no-data 로 남는다 (c486b388 모노레포 선례와 동일 정책 —
    그 수정은 레포 분리로 휴면 사본에 남아 이 라이브 경로에 없었다).
    """
    max_ugm3 = PHYSICAL_MAX_UGM3[variable]
    nulled = 0
    for p in points:
        v = p["value"]
        if v is not None and math.isfinite(v) and v > max_ugm3:
            p["value"] = None
            nulled += 1
    return nulled


def build_grid_json(variable: str, points, meta, res_deg: float, ts_ms: int) -> dict:
    """기존 current-*-grid.json 계약과 동일한 dict 생성 (+ v1 계약 필드).

    추가 필드는 전부 *공시* 다 — 상한 초과 셀은 지우거나 null 로 만들지 않고
    **세기만 한다**: 셀을 결측 처리하면 최근접 조회가 말없이 먼 셀을 집고,
    모델이 그 값을 냈다는 사실 자체가 사라진다. 물리적으로 불가능한 음수만
    예외로 결측 처리한다 (`nullify_impossible_negatives` 참조).
    """
    n_negative_nulled = nullify_impossible_negatives(points)
    n_above_max_nulled = nullify_above_physical_max(points, variable)
    # values 는 물리 상한 처리 *후* — nAboveEpaAqiScaleTop 은 실제로 서빙되는 셀
    # 중 "등급 불가 구간"(스케일 상단~물리 상한 사이)의 수가 된다. 물리 상한
    # 초과분은 별도 카운터(nAbovePhysicalMaxNulled)가 담당 — 역할이 겹치지 않는다.
    values = [p["value"] for p in points]
    scale_top = EPA_AQI_SCALE_TOP_UGM3[variable]
    return {
        "schemaVersion": "1.0",
        "variable": variable,
        "resolution": res_deg,
        "timestamp": ts_ms,
        # timestamp = 데이터가 유효한 사이클 시각 / generatedAt = 이 파일을 만든
        # 시각. 둘을 구분해야 "수집이 멈췄는데 옛 파일이 그대로 있다" 를
        # 발행물만 보고 알 수 있다 (wind 에는 있고 AQ 에는 없던 필드).
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "nLat": meta["nLat"],
        "nLon": meta["nLon"],
        "latMin": meta["latMin"],
        "lonMin": meta["lonMin"],
        "dLat": res_deg,
        "dLon": res_deg,
        "points": points,
        "source": "NOAA GEFS-Aerosols",  # provenance (Glass-box)
        "distribution": summarize(values),
        "epaAqiScaleTopUgm3": scale_top,
        "nAboveEpaAqiScaleTop": sum(
            1 for v in values if v is not None and math.isfinite(v) and v > scale_top
        ),
        # 기본값을 두지 않는다 — `nAboveEpaAqiScaleTop` 와 같은 이유로, 기본 0 을
        # 허용하면 "필터가 안 돌았다" 가 "0건 발견" 으로 둔갑한다.
        "nNegativeCellsNulled": n_negative_nulled,
        "physicalMaxUgm3": PHYSICAL_MAX_UGM3[variable],
        "nAbovePhysicalMaxNulled": n_above_max_nulled,
    }


def cycle_to_ts_ms(ymd: str, hh: str) -> int:
    """cycle (YYYYMMDD, HH) → analysis valid time(UTC) ms. f000 valid = cycle time."""
    dt = datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]), int(hh), tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ────────────────────────────── IO (네트워크/subprocess) ──────────────────────────────

def http_get(url: str, range_header: str = None, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url)
    if range_header:
        req.add_header("Range", range_header)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_latest_cycle(now: datetime = None):
    """가장 최근 가용 cycle(.idx 존재 + PMTF:surface 포함) 탐색. 오늘→어제, 18→00."""
    now = now or datetime.now(timezone.utc)
    for day_offset in range(0, 3):
        d = now - timedelta(days=day_offset)
        ymd = d.strftime("%Y%m%d")
        for hh in ("18", "12", "06", "00"):
            if day_offset == 0 and int(hh) > now.hour:
                continue  # 아직 안 나온 미래 cycle
            key = f"gefs.{ymd}/{hh}/chem/pgrb2ap25/gefs.chem.t{hh}z.a2d_0p25.f000.grib2"
            try:
                idx = http_get(f"{S3_BASE}/{key}.idx", timeout=30).decode("utf-8", "replace")
                if "PMTF:surface" in idx:
                    print(f"  cycle: {ymd} {hh}z")
                    return ymd, hh, key, idx
            except urllib.error.HTTPError:
                continue
            except Exception as e:  # noqa: BLE001
                print(f"  cycle {ymd} {hh}z probe: {e}", file=sys.stderr)
                continue
    raise RuntimeError("no available GEFS-Aerosols cycle found (today..2d ago)")


def grib_decode(grib_bytes: bytes):
    """grib_get_data 로 (lat, lon, value) 리스트 반환 (missing=9999 제외)."""
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as f:
            f.write(grib_bytes)
            path = f.name
        out = subprocess.run(
            ["grib_get_data", "-m", "9999", path],
            capture_output=True, text=True, timeout=180, check=True,
        ).stdout
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
    rows = []
    for line in out.split("\n"):
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            lat, lon, val = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            continue  # header "Latitude Longitude Value"
        if val == 9999.0:
            continue
        rows.append((lat, lon, val))
    return rows


def main() -> int:
    ymd, hh, key, idx_text = find_latest_cycle()
    ts_ms = cycle_to_ts_ms(ymd, hh)
    grib_url = f"{S3_BASE}/{key}"
    written = []
    for spec in VARS:
        rng = parse_idx_range(idx_text, spec["idx_match"])
        if not rng:
            print(f"  WARN {spec['variable']}: idx match 없음 — skip", file=sys.stderr)
            continue
        start, end = rng
        range_header = f"bytes={start}-{end}" if end is not None else f"bytes={start}-"
        grib_bytes = http_get(grib_url, range_header=range_header)
        rows = grib_decode(grib_bytes)
        if not rows:
            print(f"  WARN {spec['variable']}: decode 0 rows — skip", file=sys.stderr)
            continue
        factor = unit_factor(representative_value(v for _la, _lo, v in rows))
        rows = [(la, lo, round(v * factor, 2)) for la, lo, v in rows]
        # 변환이 자릿수째로 틀렸다면 여기서 멈춘다. 미변환 격자는 모든 값이
        # 0 에 붙어 있어 downstream 어디서도 "이상하다"고 보이지 않는다 —
        # 조용히 틀린 전지구 격자를 내보내는 것보다 이 변수를 건너뛰는 편이
        # 낫다. 다른 변수는 계속 진행한다 (한 변수의 사고로 전체를 죽이지 않음).
        median_ugm3 = representative_value(v for _la, _lo, v in rows)
        if median_ugm3 is None or not converted_median_is_sane(median_ugm3):
            # skip 은 조용하면 안 된다. 파일이 안 만들어지면 upload step 은 그
            # 파일을 건너뛰고, verify step 은 **직전 성공분**에 200 을 받아
            # 정상처럼 보이며, run 은 green 으로 끝난다 (`main()` 은 written 이
            # 하나라도 있으면 0). 즉 아무 신호 없이 옛 격자가 계속 발행된다.
            # 그래서 Actions annotation 으로 올린다 — raw 로그를 열어야만
            # 보이는 stderr 한 줄로는 이 상태가 며칠 갈 수 있다.
            print(
                f"::error title=AQ grid unit check failed::{spec['variable']} — 변환 후 중앙값 "
                f"{median_ugm3} µg/m³ 가 [{SANE_MEDIAN_MIN_UGM3}, {SANE_MEDIAN_MAX_UGM3}] 밖. "
                f"이 파일은 갱신되지 않고 옛 버전이 그대로 발행된다",
                file=sys.stderr,
            )
            continue
        points, meta = subsample(rows, GRID_RES_DEG)
        grid = build_grid_json(spec["variable"], points, meta, GRID_RES_DEG, ts_ms)
        out_path = f"{spec['file']}.json"
        with open(out_path, "w") as f:
            json.dump(grid, f, separators=(",", ":"))
        # build_grid_json 이 음수 셀을 None 으로 바꿔 놓았을 수 있다 — min/max 는
        # 남은 유한값만 본다 (None 이 섞이면 TypeError 로 수집 전체가 죽는다).
        vals = [p["value"] for p in points if p["value"] is not None]
        nulled = grid["nNegativeCellsNulled"]
        above_max = grid["nAbovePhysicalMaxNulled"]
        span = f"min={min(vals):.1f} max={max(vals):.1f} µg/m³" if vals else "유한값 0개"
        print(f"  {spec['variable']}: {len(points)} pts -> {out_path} "
              f"({span}, factor={factor:g}, 음수 결측처리={nulled}, "
              f"물리상한 초과 결측처리={above_max})")
        written.append(spec["variable"])
    if not written:
        print("ERROR: no AQ grids written", file=sys.stderr)
        return 1
    print(f"Done: NOAA GEFS-Aerosols {ymd} {hh}z -> {', '.join(written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
