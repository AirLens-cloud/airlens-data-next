#!/usr/bin/env python3
"""mac GEFS-chem 스냅샷(mac_aq_adapter 계약) → 웹 Globe AQGridResponse 계약 변환기 — R-W1.

배경: Supabase Storage/DB/Edge 가 전부 quota 402 인 동안 유일하게 살아있는 파이프라인은
`mac-data-publish.yml` 이 매시간 GitHub Pages 로 발행하는 mac 무료 글로벌 스냅샷
(`scripts/etl/collect_gefs_chem_global.py` → `mac_aq_adapter.build_grid_reading()` 계약)이다.
그러나 웹 프론트(`apps/web/src/api/airQualityGrid.ts`)는 전혀 다른 계약(`AQGridResponse`
— sparse points 리스트, latMin 이 **남단** 기준)을 기대한다. 이 스크립트는 그 간극을 코드로
잇는다 — 어떤 사람 개입도 없이, 매시간 자동으로.

두 계약의 핵심 차이 3가지:

1. **격자 방향** — mac 격자는 GRIB 관례대로 행0=북단(`grid.la1`=90 이 origin, 위도가
   행 인덱스와 함께 *감소*). `AQGridResponse.latMin`은 반대로 **남단**이 origin이다
   (`parseGridResponse`가 `latIdx = round((lat - latMin) / dLat)`로 위도가 커질수록
   인덱스도 커진다고 가정). `latMin`을 실수로 `la1`(북단) 그대로 쓰면 지구본이 남북으로
   뒤집힌다 — 이게 이 파일이 막아야 할 1순위 회귀([[feedback_null_estimate_falls_into_assertion]]
   과 같은 부류의 "부호 반대" 실수).
2. **표현 형식** — mac 은 dense 배열(행마다 nx 개 값, 좌표는 header 에서 계산),
   웹은 각 점마다 `{lat, lon, value}`를 **명시**하는 sparse 리스트. 점마다 좌표를 반복하므로
   원본 해상도(1°, nx·ny 수만 개) 그대로 내보내면 300KB 게이트를 수십 배 초과한다
   (`Obsidian-airlens` 실측 없이도 산수로 확인 가능 — 65,160점 × ~40B ≈ 2.6MB).
   그래서 기존 Open-Meteo aq-grid 피드와 같은 5° 해상도로 다운샘플한다
   (`apps/web/src/lib/config/globeOntology.ts` `aqPipeline` 주석 `resolution: '5°'` 정합).
   다운샘플은 보간·평균이 아니라 **기존에 실재하는 격자점만 골라낸다** — 없는 값을
   지어내지 않는다는 이 저장소의 정직성 원칙(`mac_aq_adapter.py` 상단 docstring)과
   같은 이유로, 실제로 GRIB 이 낸 값 그대로 재사용한다.
3. **timestamp** — `generatedAt`(원본 수집 시각)을 epoch ms 로 **보존**한다. 변환을
   실행하는 시점의 현재 시각을 쓰면 안 된다 — last-good baseline 이 재발행될 때마다
   신선도가 거짓으로 갱신되는 사고([[feedback_null_estimate_falls_into_assertion]]류의
   정직성 회귀)가 난다.

CLI:
    python3 build_web_aq_grid.py --input <gefs-chem 스냅샷.json> --out-dir <dir> \\
        [--index <mac/v1/index.json>] [--web-resolution-deg 5.0]

산출: `<out-dir>/current-pm25-grid.json`, `current-pm10-grid.json`
      (+ `--index` 지정 시 `<out-dir>/health.json`)

정직성 원칙(`mac_aq_adapter.py`/형제 수집기와 동일):
  - 입력에 없는 오염물질은 만들지 않는다 — skip + stderr 경고.
  - 비유한값(NaN/None/inf)은 points 에서 제외한다 — 0 으로 메우지 않는다.
  - 변환/크기게이트 실패는 exit 1 (last-good 웹 산출물은 이 스크립트가 건드리지 않는다 —
    호출자가 임시파일→os.replace 로만 교체하므로 실패 시 기존 파일은 그대로 남는다).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gfs_wind import downsample  # noqa: E402 — 다운샘플 로직 재사용(단일 소스)

# 웹 aq-grid 피드 기존 해상도(Open-Meteo) 와 정합 — globeOntology.ts aqPipeline 주석.
DEFAULT_WEB_RES_DEG = float(os.environ.get("MAC_WEB_GRID_RES_DEG", "5.0"))

# 300KB 게이트 — CAMS(11.1MB 원자료)는 이번 범위 제외, GEFS pm25/pm10 만 대상.
MAX_OUTPUT_BYTES = 300 * 1024

# mac_aq_adapter.POLLUTANT_KEYS 의 부분집합 — 이 스크립트가 다루는 건 GEFS 가 실제로
# 주는 pm25/pm10 뿐(GEFS-Aerosols 는 가스를 제공하지 않는다, collect_gefs_chem_global.py
# EXPECTED_POLLUTANT_COUNT=2 참조). Open-Meteo aq-grid varKey 관례(`aqPipeline`)와 맞춘
# 이름 — 실제 파싱에는 안 쓰이지만(parseGridResponse 가 `variable` 필드를 버림) 발행물의
# provenance 필드로서 기존 fixture(`apps/web/public/data/current-pm25-grid.json`)와
# 형식을 맞춘다.
POLLUTANT_VAR_KEYS: dict[str, str] = {"pm25": "pm2_5", "pm10": "pm10"}

# 물리 상한 (hard reject) — 정의·근거·출처 구분은 NOAA 직행 경로
# `collect_noaa_aq.PHYSICAL_MAX_UGM3` 주석이 정본이다 (pm2_5 1,000 = 발표된 QC
# 관행 / pm10 10,000 = 기록 극값 봉투 위 내부 결정, 2026-09-05 사용자 확정).
# 두 생산자가 **같은 파일명을 다른 정책으로 발행하면 안 된다**는 음수 필터와
# 같은 이유로 여기에도 같은 상한을 둔다 — 한쪽만 고치면 폴백 티어에서 결함이
# 되살아난다 (모노레포 c486b388 이 실증한 패턴).
PHYSICAL_MAX_UGM3: dict[str, float] = {"pm2_5": 1000.0, "pm10": 10000.0}

HEALTH_FIELDS: tuple[str, ...] = ("generatedAt", "expiresAt", "servedFrom", "available")


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def parse_generated_at_ms(generated_at_iso: str) -> int:
    """`generatedAt`(ISO, Z suffix) → epoch ms. 절대 "지금" 을 쓰지 않는다 — 원본 보존."""
    dt = datetime.fromisoformat(generated_at_iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def resolve_stride(target_res_deg: float, native_res_deg: float) -> int:
    """웹 목표 해상도에 맞는 다운샘플 stride. 목표가 원본보다 촘촘하면 stride=1(그대로)."""
    if target_res_deg <= 0 or native_res_deg <= 0:
        raise ValueError("resolution must be > 0")
    return max(1, round(target_res_deg / native_res_deg))


def south_anchored_lat_min(la1: float, dy: float, ny2: int, stride: int) -> float:
    """북단-origin(la1) 격자를 남단-origin(`AQGridResponse.latMin`) 좌표계로 변환.

    mac 격자는 행0=la1(북단)이 origin — 위도가 행 인덱스와 함께 **감소**한다. 웹 계약은
    반대로 latMin(남단)이 origin — 위도가 인덱스와 함께 **증가**한다고 가정한다
    (`parseGridResponse`: `latIdx = round((lat - latMin) / dLat)`). `la1` 을 그대로
    `latMin` 에 쓰면 남/북이 뒤집힌 지구본이 나온다 — 이 함수가 그 뒤집기를 명시적으로
    계산한다: 다운샘플된 마지막 행(남단)의 실제 위도.
    """
    return round(la1 - (ny2 - 1) * stride * dy, 6)


def build_points(
    dense_north_first: list[float],
    nx2: int,
    ny2: int,
    la1: float,
    lo1: float,
    stride: int,
    dy: float,
    dx: float,
    physical_max_ugm3: float | None = None,
) -> tuple[list[dict], int, int]:
    """다운샘플된 북단-first dense 배열 → `{lat, lon, value}` sparse 리스트.

    각 점의 좌표는 원본 header(la1/lo1)에서 직접 계산 — 배열 자체를 물리적으로 뒤집지
    않는다(points 는 좌표를 명시하므로 배열 순서는 무관, `south_anchored_lat_min` 이
    origin 만 올바르게 잡으면 충분). 비유한값은 제외한다 — 0 으로 메우지 않는다.

    음수 질량농도도 같은 이유로 제외한다. 질량농도는 음수가 될 수 없으므로 음수 셀은
    *값* 이 아니라 *결측* 이다 — 0 이나 이웃 평균으로 바꾸면 "측정되지 않음" 이
    "가장 깨끗함" 으로 둔갑한다.

    **왜 여기서도 해야 하나.** `mac_aq_qa.py` 는 음수를 잡지만 세기만 한다
    (`qa_grid_pollutant_block` — "셀 값을 바꾸지 않고 이상치 개수만 집계",
    Glass-box 원칙상 원본 불변). 그래서 음수는 이 변환기까지 그대로 흘러온다.
    NOAA 직행 경로는 `collect_noaa_aq.py` 가 이미 같은 정책을 적용한다 — 두 경로가
    **같은 파일명**(`current-pm25-grid.json`)을 서로 다른 정책으로 발행하면,
    웹이 어느 쪽에서 받았느냐에 따라 음수 셀이 보이기도 안 보이기도 한다.

    물리 상한(`physical_max_ugm3`) 초과도 같은 정책으로 제외한다 — 발표된 QC
    관행·기록 극값 봉투 위의 값은 결측이다 (`PHYSICAL_MAX_UGM3` 주석, 2026-09-05).

    반환은 `(points, n_negative_dropped, n_above_max_dropped)`. 떨어뜨린 수를
    돌려주는 이유: 조용히 버리면 "없었다" 와 "지웠다" 가 로그에서 구분되지 않는다.
    """
    points: list[dict] = []
    n_negative = 0
    n_above_max = 0
    for j2 in range(ny2):
        lat = round(la1 - j2 * stride * dy, 6)
        for i2 in range(nx2):
            v = dense_north_first[j2 * nx2 + i2]
            if v is None or not math.isfinite(v):
                continue
            if v < 0:
                n_negative += 1
                continue
            if physical_max_ugm3 is not None and v > physical_max_ugm3:
                # 물리 상한 초과 — 음수와 같은 종류(값이 아니라 결측)다. 근거·값의
                # 출처 구분은 `PHYSICAL_MAX_UGM3` 주석 참조.
                n_above_max += 1
                continue
            lon = round(lo1 + i2 * stride * dx, 6)
            points.append({"lat": lat, "lon": lon, "value": v})
    return points, n_negative, n_above_max


def convert_pollutant(reading: dict, pollutant_key: str, target_res_deg: float) -> dict:
    """mac grid_reading 하나의 오염물질 → `AQGridResponse` 문서. 실패 시 raise(부분 발행 금지)."""
    grid = reading.get("grid")
    if not grid or any(k not in grid for k in ("nx", "ny", "la1", "lo1", "dx", "dy")):
        raise ValueError("input missing/incomplete grid header")
    block = (reading.get("pollutants") or {}).get(pollutant_key)
    if not block or not block.get("data"):
        raise ValueError(f"pollutant {pollutant_key!r} missing in input — 0 으로 메우지 않는다")

    nx, ny = grid["nx"], grid["ny"]
    la1, lo1, dy, dx = grid["la1"], grid["lo1"], grid["dy"], grid["dx"]
    if len(block["data"]) != nx * ny:
        raise ValueError(
            f"pollutant {pollutant_key!r}: data length {len(block['data'])} != nx*ny {nx * ny}"
        )

    stride = resolve_stride(target_res_deg, dy)
    dense2, nx2, ny2 = downsample(block["data"], nx, ny, stride)

    lat_min = south_anchored_lat_min(la1, dy, ny2, stride)
    lon_min = round(lo1, 6)
    variable = POLLUTANT_VAR_KEYS.get(pollutant_key, pollutant_key)
    physical_max = PHYSICAL_MAX_UGM3.get(variable)
    points, n_negative, n_above_max = build_points(
        dense2, nx2, ny2, la1, lo1, stride, dy, dx, physical_max_ugm3=physical_max
    )
    if not points:
        raise ValueError(f"pollutant {pollutant_key!r}: 다운샘플 후 points 0개")

    generated_at = reading.get("generatedAt")
    if not generated_at:
        raise ValueError("input missing generatedAt — timestamp 를 지어낼 수 없다")

    return {
        "variable": variable,
        "resolution": round(stride * dy, 6),
        "timestamp": parse_generated_at_ms(generated_at),
        "nLat": ny2,
        "nLon": nx2,
        "latMin": lat_min,
        "lonMin": lon_min,
        "dLat": round(stride * dy, 6),
        "dLon": round(stride * dx, 6),
        "points": points,
        # 공시 — 기본값 없이 항상 싣는다. 없으면 "음수가 0건이었다" 와 "음수를
        # 세지 않았다" 가 구분되지 않는다. NOAA 직행 경로의 `nNegativeCellsNulled`
        # 와 짝이되 동사가 다르다: 그쪽은 조밀 격자라 셀을 남기고 값만 null 로
        # 만들고(nulled), 이쪽은 희소 출력이라 셀 자체를 뺀다(dropped).
        "nNegativeCellsDropped": n_negative,
        # NOAA 직행 경로의 `nAbovePhysicalMaxNulled` 와 짝 — 동사가 다른 이유도
        # 음수 카운터와 동일(그쪽 null / 이쪽 drop). physicalMaxUgm3 는 어느
        # 상한으로 걸렀는지의 공시 — 없으면 "0건" 과 "안 걸렀다" 가 안 구분된다.
        "physicalMaxUgm3": physical_max,
        "nAbovePhysicalMaxDropped": n_above_max,
        "source": reading.get("source", "NOAA GEFS-Aerosols"),
    }


def build_health(index: dict) -> dict:
    """mac `index.json`(build_mac_index.py 산출, 4소스 상태) → 웹 소비용 `health.json`.

    소스별로 {generatedAt, expiresAt, servedFrom, available} 4필드만 남긴다 — index.json
    의 나머지 필드(quality/coverage/unavailableReason 등)는 mac 클라이언트/운영자 진단용이라
    웹 소비자에게 그대로 노출하지 않는다(축약, 재가공).
    """
    sources = index.get("sources") or {}
    return {
        "generatedAt": index.get("generatedAt"),
        "sources": {
            name: {field: entry.get(field) for field in HEALTH_FIELDS}
            for name, entry in sources.items()
        },
    }


def dump_compact(doc: dict) -> bytes:
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def write_atomic(path: str, payload: bytes) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(payload)
    os.replace(tmp_path, path)


# ────────────────────────────── CLI ──────────────────────────────

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, help="mac GEFS-chem grid_reading JSON 경로")
    p.add_argument("--out-dir", required=True, help="웹 grid JSON 출력 디렉터리")
    p.add_argument("--index", default=None, help="mac/v1/index.json 경로 (health.json 생성용, 선택)")
    p.add_argument("--web-resolution-deg", type=float, default=DEFAULT_WEB_RES_DEG)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    try:
        with open(args.input, encoding="utf-8") as f:
            reading = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read input {args.input!r} — {e}", file=sys.stderr)
        return 1

    os.makedirs(args.out_dir, exist_ok=True)

    written: list[tuple[str, int]] = []
    try:
        for key in POLLUTANT_VAR_KEYS:
            if key not in (reading.get("pollutants") or {}):
                print(f"  {key}: input 에 없음 — skip (0 으로 메우지 않는다)", file=sys.stderr)
                continue
            doc = convert_pollutant(reading, key, args.web_resolution_deg)
            payload = dump_compact(doc)
            size = len(payload)
            if size > MAX_OUTPUT_BYTES:
                raise ValueError(f"{key}: 출력 {size}B 가 게이트 {MAX_OUTPUT_BYTES}B 초과")
            out_path = os.path.join(args.out_dir, f"current-{key}-grid.json")
            write_atomic(out_path, payload)
            written.append((key, size))
    except Exception as e:  # noqa: BLE001 — fail-loud, 기존 산출물은 os.replace 전이라 안 건드림
        print(f"ERROR: web AQ grid 변환 실패 — {e}", file=sys.stderr)
        return 1

    if not written:
        print("ERROR: 변환된 오염물질 0개 — 0 으로 메우지 않는다", file=sys.stderr)
        return 1

    for key, size in written:
        print(f"  {key}: {size}B -> current-{key}-grid.json")

    if args.index:
        try:
            with open(args.index, encoding="utf-8") as f:
                index = json.load(f)
            health = build_health(index)
            write_atomic(os.path.join(args.out_dir, "health.json"), dump_compact(health))
            print("  health.json 갱신됨")
        except (OSError, json.JSONDecodeError) as e:
            # health.json 은 부가 산출물 — 실패해도 그리드 발행 자체는 성공으로 유지.
            print(f"WARN: health.json 생성 실패(그리드는 발행됨) — {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
