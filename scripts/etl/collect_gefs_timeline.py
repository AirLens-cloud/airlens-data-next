#!/usr/bin/env python3
"""NOAA GEFS-Aerosols PM2.5 타임라인 프레임 수집기 (Globe P8).

`collect_noaa_aq.py`(current 단일 프레임)의 시계열 확장. ±24h(3h 스텝, 17프레임)의
전역 PM2.5 격자를 GEFS 분석/예보 스텝에서 생성 → Supabase Storage `aq-data/timeline/`.

소스 동일: `gefs.YYYYMMDD/HH/chem/pgrb2ap25` a2d_0p25
  - PM2.5 = GRIB `PMTF:surface:{anl|N hour fcst}:aerosol=Total aerosol:aerosol_size <2.5e-06`
  - live-probe(2026-07-06) 실측: f000..f120 3h 스텝, 과거 cycle ≥2일 보존.
프레임 플래너: target validTime(now−24…+24, 3h)마다 최신 available cycle(≤validTime) 선택
  → lead 최소(freshest). lead=0=분석 / lead>0=예보(정직 라벨용).
Glass-box: GEFS 단일 결정론 멤버 → p10-p90 없음. 없는 불확실성 만들지 않음(정직 caveat 는 FE legend).
Storage-only(git 커밋 회피 — 볼륨). 신규 secret 0 (S3 anonymous + 기존 SUPABASE_SERVICE_KEY 업로드는 워크플로).
GRIB decode = eccodes `grib_get_data`(CI apt libeccodes-tools). pip 신규 deps 0.
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
    S3_BASE,
    build_grid_json,
    grib_decode,
    http_get,
    parse_idx_range,
    subsample,
    unit_factor,
)

STEP_H = 3          # GEFS a2d_0p25 예보 스텝(실측)
WINDOW_H = 24       # 슬라이더 ±24h
MAX_LEAD_H = 120    # GEFS a2d_0p25 최대 예보시각(실측)
CYCLE_HOURS = (0, 6, 12, 18)
RES_DEG = float(os.environ.get("AQ_TIMELINE_RES_DEG", "2.0"))  # 경량 프레임(scrub용)

# PM2.5 GRIB 레코드 substring(collect_noaa_aq VARS 와 동일 species, 시간 세그먼트만 파라미터화)
PM25_SPECIES = "aerosol=Total aerosol:aerosol_size <2.5e-06"
OUT_DIR = os.environ.get("AQ_TIMELINE_OUT", "timeline")


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def floor_to_step(dt: datetime, step_h: int = STEP_H) -> datetime:
    """dt 를 00Z 기준 step_h 격자로 내림(minute/second 0)."""
    h = (dt.hour // step_h) * step_h
    return dt.replace(hour=h, minute=0, second=0, microsecond=0)


def candidate_cycles(now: datetime, span_h: int = WINDOW_H + STEP_H, back_h: int = 48):
    """now 기준 [now-back_h, now] 의 GEFS cycle(6h) 후보 리스트(desc, 최신 우선).

    back_h 는 과거 프레임(now-24h) 을 lead 최소로 커버할 만큼 넉넉히(기본 48h, 보존 확인).
    """
    base = now.replace(minute=0, second=0, microsecond=0)
    while base.hour not in CYCLE_HOURS:
        base -= timedelta(hours=1)
    out = []
    steps = back_h // 6 + 1
    for i in range(steps + 1):
        out.append(base - timedelta(hours=6 * i))
    return out  # desc (최신 → 과거)


def forecast_idx_match(species: str, lead_h: int) -> str:
    """lead_h 에 맞는 GRIB .idx substring. lead 0=anl, >0='N hour fcst'."""
    seg = "anl" if lead_h == 0 else f"{lead_h} hour fcst"
    return f"PMTF:surface:{seg}:{species}"


def plan_frames(now: datetime, available_cycles, step_h: int = STEP_H,
                window_h: int = WINDOW_H, max_lead_h: int = MAX_LEAD_H):
    """target validTime(anchor±window, step) 마다 최신 available cycle(≤T) 선택.

    available_cycles = 확인된(존재) cycle datetime 리스트(정렬 무관).
    반환: [{'valid': dt, 'cycle': dt, 'lead_h': int}] — cycle 매칭 실패 target 은 제외(정직).
    """
    anchor = floor_to_step(now, step_h)
    targets = [anchor + timedelta(hours=h)
               for h in range(-window_h, window_h + 1, step_h)]
    cyc_desc = sorted(set(available_cycles), reverse=True)  # 최신 우선
    frames = []
    for T in targets:
        best = None
        for tc in cyc_desc:
            if tc > T:
                continue
            lead = int((T - tc).total_seconds() // 3600)
            if lead % step_h != 0 or lead > max_lead_h:
                continue
            # cyc_desc 최신 우선 → 첫 매칭이 lead 최소(freshest). 조기 종료.
            best = {"valid": T, "cycle": tc, "lead_h": lead}
            break
        if best:
            frames.append(best)
    return frames


def cycle_key(cycle: datetime, lead_h: int) -> str:
    """GEFS a2d_0p25 GRIB object key(.idx 는 +'.idx')."""
    ymd = cycle.strftime("%Y%m%d")
    hh = cycle.strftime("%H")
    return (f"gefs.{ymd}/{hh}/chem/pgrb2ap25/"
            f"gefs.chem.t{hh}z.a2d_0p25.f{lead_h:03d}.grib2")


def frame_filename(valid: datetime) -> str:
    """프레임 파일명(URL/파일 안전, 정렬 가능). validTime UTC 기준."""
    return f"pm25-{valid.strftime('%Y%m%d%H')}.json"


def build_frame_json(variable, points, meta, res_deg, valid_ms, lead_h, cycle_iso):
    """current-*-grid.json 계약 + 타임라인 provenance(leadHours/cycle) 확장.

    base 필드는 parseGridResponse 무변경 재사용, 추가 필드는 legend 정직 라벨용.
    """
    d = build_grid_json(variable, points, meta, res_deg, valid_ms)
    d["leadHours"] = lead_h        # 0=분석 / >0=예보 (FORECAST 라벨)
    d["cycle"] = cycle_iso         # GEFS cycle refTime
    return d


def build_manifest(frames_meta, generated_ms: int, ref_cycle_iso: str):
    """타임라인 manifest — FE 슬라이더 스냅 + 신선도 게이트 SOT.

    frames_meta = [{'validTime': iso, 'leadHours': int, 'file': str, 'cycle': iso}]
    refTime = 가장 최신 cycle(신선도 게이트가 나이 측정).
    """
    return {
        "variable": "pm2_5",
        "source": "NOAA GEFS-Aerosols",
        "refTime": ref_cycle_iso,
        "generatedAt": datetime.fromtimestamp(generated_ms / 1000, timezone.utc)
                               .isoformat().replace("+00:00", "Z"),
        "stepHours": STEP_H,
        "windowHours": WINDOW_H,
        "resolution": RES_DEG,
        "frames": frames_meta,
    }


def iso_z(dt: datetime) -> str:
    """UTC ISO with trailing Z."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def finite_values(points):
    """None 결측을 뺀 값만. `build_grid_json` 이 음수·물리 상한 초과 셀을 `None`
    으로 떨구므로(collect_noaa_aq `nullify_impossible_negatives` /
    `nullify_above_physical_max`), 격자 값을 집계하는 자리는 전부 이걸 통과해야
    한다 — None 이 섞인 채 min/max 를 부르면 TypeError 로 수집 전체가 죽는다
    (2026-09-05 실측: 상한 배선 38분 뒤 첫 실패, 타임라인 18h 정지).
    """
    return [p["value"] for p in points if p["value"] is not None]


def frame_span_label(points) -> str:
    """로그 한 줄용 정직 라벨. 전 셀 결측이면 최대값을 지어내지 않는다."""
    vals = finite_values(points)
    return f"max={max(vals):.1f} µg/m³" if vals else "유한값 0개"


# ────────────────────────────── IO (네트워크/subprocess) ──────────────────────────────

def probe_available_cycles(cands):
    """후보 cycle 중 a2d_0p25 f000 .idx 존재(+PMTF:surface) 확인된 것만 반환(desc)."""
    ok = []
    for tc in cands:
        key = cycle_key(tc, 0) + ".idx"
        try:
            idx = http_get(f"{S3_BASE}/{key}", timeout=25).decode("utf-8", "replace")
            if "PMTF:surface" in idx:
                ok.append(tc)
        except urllib.error.HTTPError:
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  cycle probe {tc:%Y%m%d %H}z: {e}", file=sys.stderr)
    return ok


def fetch_frame_grid(frame):
    """한 프레임의 PM2.5 격자 dict 생성. 실패 시 None(정직 skip)."""
    cycle, lead = frame["cycle"], frame["lead_h"]
    key = cycle_key(cycle, lead)
    idx_url = f"{S3_BASE}/{key}.idx"
    try:
        idx_text = http_get(idx_url, timeout=30).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"  f{lead:03d} {cycle:%m%d %H}z: idx fetch 실패 {e} — skip", file=sys.stderr)
        return None
    rng = parse_idx_range(idx_text, forecast_idx_match(PM25_SPECIES, lead))
    if not rng:
        print(f"  f{lead:03d} {cycle:%m%d %H}z: PM2.5 idx match 없음 — skip", file=sys.stderr)
        return None
    start, end = rng
    range_header = f"bytes={start}-{end}" if end is not None else f"bytes={start}-"
    try:
        grib_bytes = http_get(f"{S3_BASE}/{key}", range_header=range_header)
        rows = grib_decode(grib_bytes)
    except Exception as e:  # noqa: BLE001
        print(f"  f{lead:03d} {cycle:%m%d %H}z: grib 실패 {e} — skip", file=sys.stderr)
        return None
    if not rows:
        print(f"  f{lead:03d} {cycle:%m%d %H}z: decode 0 rows — skip", file=sys.stderr)
        return None
    factor = unit_factor(max(v for _la, _lo, v in rows))
    rows = [(la, lo, round(v * factor, 2)) for la, lo, v in rows]
    points, meta = subsample(rows, RES_DEG)
    valid_ms = int(frame["valid"].timestamp() * 1000)
    return build_frame_json("pm2_5", points, meta, RES_DEG, valid_ms,
                            lead, iso_z(cycle))


def main() -> int:
    now = datetime.now(timezone.utc)
    cands = candidate_cycles(now)
    available = probe_available_cycles(cands)
    if not available:
        print("ERROR: no available GEFS-Aerosols cycle (48h)", file=sys.stderr)
        return 1
    print(f"available cycles: {len(available)} (latest {available[0]:%Y%m%d %H}z)")

    frames = plan_frames(now, available)
    if not frames:
        print("ERROR: frame planner produced 0 frames", file=sys.stderr)
        return 1

    out_dir = OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    frames_meta = []
    ref_cycle = max(f["cycle"] for f in frames)  # 최신 cycle = 신선도 refTime
    for fr in frames:
        grid = fetch_frame_grid(fr)
        if grid is None:
            continue
        # 전 셀이 결측이면 그건 격자가 아니다 — 쓰지 않으면 직전 발행분이
        # 살아남는다(빈 격자가 멀쩡한 격자를 덮은 2026-09-04 사고와 같은 정신).
        if not finite_values(grid["points"]):
            print(f"  f{fr['lead_h']:03d} {iso_z(fr['valid'])}: 유한값 0개 — skip",
                  file=sys.stderr)
            continue
        fname = frame_filename(fr["valid"])
        with open(os.path.join(out_dir, fname), "w") as fh:
            json.dump(grid, fh, separators=(",", ":"))
        frames_meta.append({
            "validTime": iso_z(fr["valid"]),
            "leadHours": fr["lead_h"],
            "cycle": iso_z(fr["cycle"]),
            "file": fname,
        })
        # 결측 카운터를 같이 찍는다 — 필터가 돌았는지 로그로 보여야 한다.
        print(f"  {iso_z(fr['valid'])} lead={fr['lead_h']:>3}h "
              f"pts={len(grid['points'])} {frame_span_label(grid['points'])} "
              f"(음수 결측처리={grid['nNegativeCellsNulled']}, "
              f"물리상한 초과 결측처리={grid['nAbovePhysicalMaxNulled']}) -> {fname}")

    if not frames_meta:
        print("ERROR: 0 frames built (all skipped)", file=sys.stderr)
        return 1

    manifest = build_manifest(frames_meta, int(now.timestamp() * 1000), iso_z(ref_cycle))
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, separators=(",", ":"))
    print(f"Done: {len(frames_meta)}/{len(frames)} frames -> {out_dir}/ "
          f"(refTime {manifest['refTime']}, res {RES_DEG}°)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
