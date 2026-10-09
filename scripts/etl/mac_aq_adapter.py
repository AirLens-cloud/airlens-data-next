#!/usr/bin/env python3
"""macOS 무료 글로벌 근실시간 대기질 파이프라인 — 소스 어댑터 공통 인터페이스.

설계 SOT: `Obsidian-airlens/wiki/architecture/free-global-near-real-time-aq-macos-2026-07-16.md`
§"P0 파일 구조" §"필수 레코드". 이 모듈은 그 레코드 스키마를 코드로 고정한 **어댑터 계약**이다.

이 계약을 구현하는 두 부류:

1. **Global gridded producer** (이 PR — `collect_cams_global.py` / `collect_gefs_chem_global.py`):
   전 세계 격자 하나를 통째로 받아온다. 개별 셀마다 JSON object 를 만들면 수십만 개가
   생기므로, dense 배열 하나(+헤더)로 표현한다 → `build_grid_reading()`.
2. **Regional station adapter** (W5-b — AirKorea/EEA 등 근실시간 관측소):
   각 관측소 좌표 하나가 지오셀 하나에 대응하므로 포인트 레코드 리스트로 표현한다
   → `build_point_reading()`. **주의**: `collect_airkorea.py` 는 ML 학습용 *과거 bulk*
   수집기(월/연 단위 CSV·parquet, `Data/3-raw-sources/`)다. 이 계약이 요구하는 건 완전히
   다른 모드 — "지금 이 관측소의 최신 값 1개"를 받는 근실시간 조회다.
   AirKorea 는 동일 `AIRKOREA_API_KEY` 를 실시간 엔드포인트(`ArpltnInforInqireSvc`
   getCtprvnRltmMesureDnsty 등)에 재사용할 수 있으나 코드 경로는 새로 작성해야 한다.
   EEA 근실시간은 `collect_mac_eea_utd.py`(UTD/E2a) 단일 진입점이다.

두 producer 모두 아래 3단계로 레코드를 만든다:

    envelope = build_envelope(kind=..., source=..., ...)
    quality  = estimate_quality(...)                 # envelope 안에 포함
    reading  = build_point_reading(envelope, lat, lon, pollutants)   # 관측소
             = build_grid_reading(envelope, grid_header, pollutants) # 전역 격자

`validate_point_reading()` / `validate_grid_reading()` 로 산출물 스키마를 커밋 전에 검증한다.

정직성 원칙 (기존 `collect_noaa_aq.py`/`collect_gfs_wind.py` 와 동일):
- 값이 없으면 0 으로 메우지 않는다 — 호출자가 해당 소스를 skip/exit 1 한다.
- `quality` 는 플랫폼 DQSS(베이지안 신뢰도 엔진, `models/dqss/`)가 아니다. 이 파이프라인은
  DB/서버가 없는 정적 스냅샷이므로 신선도+완전성 기반의 단순 휴리스틱만 제공한다
  (`estimate_quality()` docstring 참조). "다른 quantity" 혼동 방지
  ([[feedback_shared_name_hides_different_quantity]] 정합).
"""
from __future__ import annotations

SCHEMA_VERSION = 1

# 설계 SOT §"필수 레코드" 의 pollutants 키 7종.
POLLUTANT_KEYS = ("pm1", "pm25", "pm10", "o3", "no2", "so2", "co")

# `estimate_quality` 의 freshness 가 감점 없이 1.0 으로 평평한 나이 상한(시간).
# 상수로 뽑아 둔 이유: `merge_point_readings.py` 가 "이 구간 안에서는 등급이
# 나이에 무관하므로 이어붙인 레코드의 quality 를 재계산하지 않아도 된다"는
# 불변식에 의존한다. 아래 밴드를 좁히면 그 전제가 조용히 깨지므로 리터럴을
# 두 파일에 각각 두지 않는다.
FRESH_QUALITY_BAND_HOURS = 6

_R_SPECIFIC_DRY_AIR = 287.05  # J/(kg·K) — 표준 건조공기 비기체상수


# ────────────────────────── 단위 변환 (pure) ──────────────────────────

def air_density_kg_m3(pressure_pa: float, temperature_k: float) -> float:
    """이상기체 법칙 ρ = p / (R_specific · T). 표면기압·2m기온으로 근사.

    CAMS 가스 변수(오존/이산화질소/일산화탄소/이산화황)는 질량혼합비(kg/kg)로
    제공되므로, 농도(µg/m³)로 바꾸려면 그 지점의 공기 밀도가 필요하다
    (설계 SOT: "원자료의 kg/m3 또는 kg/kg를 표면 공기 밀도와 메타데이터에 따라 변환").
    """
    if pressure_pa <= 0 or temperature_k <= 0:
        raise ValueError(f"invalid pressure/temperature: {pressure_pa} Pa, {temperature_k} K")
    return pressure_pa / (_R_SPECIFIC_DRY_AIR * temperature_k)


def mixing_ratio_to_ugm3(mixing_ratio_kgkg: float, density_kgm3: float) -> float:
    """질량혼합비(kg/kg) → 질량농도(µg/m³). conc = ratio · ρ_air · 1e9."""
    return mixing_ratio_kgkg * density_kgm3 * 1e9


def mass_conc_kgm3_to_ugm3(value_kgm3: float) -> float:
    """이미 질량농도(kg/m³)인 값(PM1/PM2.5/PM10) → µg/m³. 밀도 불필요."""
    return value_kgm3 * 1e9


def pollutant_value(value_ugm3: float, source_variable: str, conversion: str) -> dict:
    """pollutants[<key>] 하나의 값 — 원본 변수명 + 변환식을 함께 기록(Glass-box provenance)."""
    return {
        "value": round(value_ugm3, 2),
        "unit": "ug/m3",
        "sourceVariable": source_variable,
        "conversion": conversion,
    }


# ────────────────────────── quality 휴리스틱 (pure) ──────────────────────────

def estimate_quality(pollutant_count: int, expected_count: int, age_hours: float) -> dict:
    """신선도 + 완전성 기반 단순 등급. 플랫폼 DQSS(베이지안 신뢰도 엔진)가 아니다.

    이 파이프라인은 Supabase/서버가 없는 정적 스냅샷이라 `models/dqss/` 의 5-컴포넌트
    신뢰도 엔진을 못 쓴다. 대신 두 신호만 본다:
      - completeness = pollutant_count / expected_count (기대한 오염물질이 다 왔는가)
      - freshness     = age_hours (원자료가 스케줄대로 나왔는가)
    A/B/C/D/F 5등급, score 는 두 신호의 최소값 기반(가장 나쁜 쪽이 등급을 결정).
    """
    if expected_count <= 0:
        raise ValueError("expected_count must be > 0")
    completeness = pollutant_count / expected_count
    if age_hours < 0:
        raise ValueError("age_hours must be >= 0")

    # freshness: CAMS 12시간·GEFS 6시간 주기 감안, 24h 이내면 정상 범위로 본다.
    if age_hours <= FRESH_QUALITY_BAND_HOURS:
        freshness = 1.0
    elif age_hours <= 24:
        freshness = 0.8
    elif age_hours <= 48:
        freshness = 0.5
    else:
        freshness = 0.2

    score = round(min(completeness, freshness) * 100)
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"

    return {
        "grade": grade,
        "score": score,
        "method": "freshness+completeness heuristic (not platform DQSS)",
    }


# ────────────────────────── 레코드 조립 (pure) ──────────────────────────

def build_envelope(
    *,
    kind: str,
    source: str,
    source_version: str,
    generated_at: str,
    observed_at: str | None,
    valid_at: str,
    expires_at: str,
    resolution_km: float,
    attribution: str,
    quality: dict,
) -> dict:
    """설계 SOT §"필수 레코드" 의 공통 필드(lat/lon·pollutants·grid 제외). schemaVersion 고정."""
    if kind not in ("observation", "analysis", "forecast"):
        raise ValueError(f"kind must be observation|analysis|forecast, got {kind!r}")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind,
        "source": source,
        "sourceVersion": source_version,
        "generatedAt": generated_at,
        "observedAt": observed_at,
        "validAt": valid_at,
        "expiresAt": expires_at,
        "resolutionKm": resolution_km,
        "quality": quality,
        "attribution": attribution,
    }


def build_point_reading(envelope: dict, lat: float, lon: float, pollutants: dict) -> dict:
    """관측소 1곳 = 지오셀 1개. W5-b 지역 어댑터(AirKorea/EEA)의 산출 계약."""
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"lat out of range: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"lon out of range: {lon}")
    if not pollutants:
        raise ValueError("pollutants must not be empty — 0 으로 메우지 않는다")
    return {**envelope, "lat": lat, "lon": lon, "pollutants": pollutants}


def build_grid_reading(envelope: dict, grid_header: dict, pollutants_grid: dict) -> dict:
    """전 세계 격자 하나. CAMS/NOAA 글로벌 producer 의 산출 계약.

    `grid_header` = {"nx","ny","lo1","la1","dx","dy"} (기존 wind/AQ 그리드 헤더와 동일 관례,
    `collect_gfs_wind.make_header` / `collect_noaa_aq.build_grid_json` 참조).
    `pollutants_grid[<key>]` = {"unit","sourceVariable","conversion","data":[...]} —
    `data` 는 row-major dense 배열(행0=북단, 열0=lo1), `pollutant_value()` 로 만든 단건과
    같은 provenance 필드를 격자 레벨로 승격한 것.
    """
    required_header = ("nx", "ny", "lo1", "la1", "dx", "dy")
    missing = [k for k in required_header if k not in grid_header]
    if missing:
        raise ValueError(f"grid_header missing keys: {missing}")
    if not pollutants_grid:
        raise ValueError("pollutants_grid must not be empty — 0 으로 메우지 않는다")
    expected_len = grid_header["nx"] * grid_header["ny"]
    for key, block in pollutants_grid.items():
        data = block.get("data")
        if data is None or len(data) != expected_len:
            raise ValueError(
                f"pollutant {key!r}: data length {len(data) if data is not None else None} "
                f"!= nx*ny {expected_len} — incomplete grid, 0 으로 메우지 않는다"
            )
    return {**envelope, "grid": grid_header, "pollutants": pollutants_grid}


# ────────────────────────── 검증 (pure — 커밋 전 self-check) ──────────────────────────

_ENVELOPE_REQUIRED = (
    "schemaVersion", "kind", "source", "sourceVersion", "generatedAt",
    "observedAt", "validAt", "expiresAt", "resolutionKm", "quality", "attribution",
)


def _validate_envelope_fields(record: dict) -> list[str]:
    errors = [f"missing field: {k}" for k in _ENVELOPE_REQUIRED if k not in record]
    if record.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"schemaVersion mismatch: {record.get('schemaVersion')!r}")
    if record.get("kind") not in ("observation", "analysis", "forecast"):
        errors.append(f"invalid kind: {record.get('kind')!r}")
    return errors


def validate_point_reading(record: dict) -> list[str]:
    """빈 리스트 = 유효. W5-b 어댑터는 발행 전 이 함수로 자체 검증해야 한다."""
    errors = _validate_envelope_fields(record)
    if "lat" not in record or "lon" not in record:
        errors.append("missing lat/lon")
    if not record.get("pollutants"):
        errors.append("empty pollutants")
    return errors


def validate_grid_reading(record: dict) -> list[str]:
    """빈 리스트 = 유효."""
    errors = _validate_envelope_fields(record)
    grid = record.get("grid")
    if not grid or any(k not in grid for k in ("nx", "ny", "lo1", "la1", "dx", "dy")):
        errors.append("missing/incomplete grid header")
    pollutants = record.get("pollutants")
    if not pollutants:
        errors.append("empty pollutants")
    elif grid:
        expected_len = grid.get("nx", 0) * grid.get("ny", 0)
        for key, block in pollutants.items():
            data = block.get("data") if isinstance(block, dict) else None
            if data is None or len(data) != expected_len:
                errors.append(f"pollutant {key}: data length mismatch")
    return errors
