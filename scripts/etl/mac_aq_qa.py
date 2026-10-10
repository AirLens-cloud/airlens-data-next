#!/usr/bin/env python3
"""macOS 무료 글로벌 근실시간 대기질 파이프라인 — QA(품질 검증) 계층.

설계 배경(비공개 내부 노트 2026-07-16 — 동작 정의는 코드·테스트·계약):
§"검증 게이트" — "source별 null ratio, 값 범위, 중복 좌표, timestamp gap, 공간 커버리지를
검사한다." 이 모듈은 그 4항목을 `mac_aq_adapter` 의 envelope/point/grid 계약 위에서 구현한다.

**이 모듈은 `mac_aq_adapter.py`/`collect_cams_global.py`/`collect_gefs_chem_global.py` 를
수정하지 않는다** — W5-a 계약 동결 + 병렬 작업 W5-b와의 파일 충돌 회피.
전부 신규 함수.

정책 (팀 지시 + Glass-box 정합):
  - QA 실패 reading 은 **drop 하지 않는다** — quality 등급을 강등하고 카운트를 리포트에
    남긴다(silent drop 금지). 값 자체는 원본 그대로 보존한다(변조 금지 — 사용자가 원본을
    보고 판단할 권리, Glass-box 정신과 동일).
  - 물리 타당 범위(`POLLUTANT_BOUNDS`)는 설계 SOT 에 **명시되어 있지 않다** — 이 모듈이
    도입하는 보수적 기본값이다(WHO/EPA AQI breakpoint 표 + 기록된 극단 사례 참고, 하단 주석).
    실측 데이터로 조정 필요 시 후속 변경 대상.
  - 이 QA 단독으로는 발행을 막지 않는다(그레이드 강등 + 리포트만). 발행 차단은 파이프라인
    수준 실패(스키마 위반·입력 파일 없음)에서만 일어난다 — `build_mac_aq_snapshot.py` 참조.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

# ────────────────────────── 물리 타당 범위 (µg/m3) ──────────────────────────
# 근거: PM 두 항목은 2026-09-05 문헌 봉투로 앵커됨 (사용자 확정, 근거는 비공개 내부
# 설계 노트 Q9). 나머지 항목은
# 여전히 이 모듈의 보수적 기본값(설계 SOT 미지정). "명백히 깨진 값"(음수·decode
# 오류·단위 혼동)만 잡도록 상한을 넉넉히 잡는다 — 실제 대기오염 극단치(산불
# 연무·황사)를 false-positive 로 잡지 않는 것이 목적.
POLLUTANT_BOUNDS: dict[str, tuple[float, float]] = {
    "pm1": (0.0, 1000.0),
    # 발표된 QC 관행 그대로 (Mathieu-Campbell 2024: 시간별 >1,000 제외).
    # 회수된 최고 시간별 기록 855.1 µg/m3 위 — 구 주석("800~999 대")과 정합.
    "pm25": (0.0, 1000.0),
    # 구값 3,000 은 회수된 기록 극값(일평균 ≈7,414 · 시간평균 ≈6,460 µg/m3)보다
    # **낮아** 실제 황사 극단치를 오탐하는 값이었다 — 이 모듈의 자기 목적
    # ("실제 극단치를 false-positive 로 잡지 않는다") 위반. 10,000 은 문헌 값이
    # 아니라 그 봉투 위에 여유를 얹은 내부 결정이다 (findings.md Q9-1 표기 원칙).
    "pm10": (0.0, 10000.0),
    "o3": (0.0, 1000.0),
    "no2": (0.0, 1000.0),
    "so2": (0.0, 2000.0),    # 화산/공업 국지 이벤트 고려
    "co": (0.0, 50000.0),    # CO 는 분자량이 커 µg/m3 스케일이 다른 가스보다 큼
}


def check_value_bounds(pollutant_key: str, value: float) -> str | None:
    """단일 값의 물리 타당성. 문제 없으면 None, 있으면 이유 문자열."""
    if value is None:
        return "null value"
    if isinstance(value, bool):  # bool 은 int 서브클래스라 별도 차단
        return f"non-numeric value: {value!r}"
    if not isinstance(value, (int, float)):
        return f"non-numeric value: {value!r}"
    if math.isnan(value) or math.isinf(value):
        return "NaN/Inf value"
    bounds = POLLUTANT_BOUNDS.get(pollutant_key)
    if bounds is None:
        return None  # 알 수 없는 키는 판단 보류(어댑터 스키마 검증이 별도로 잡음)
    lo, hi = bounds
    if value < lo:
        return f"below min {lo}"
    if value > hi:
        return f"above max {hi}"
    return None


# ────────────────────────── grid reading QA ──────────────────────────

def qa_grid_pollutant_block(pollutant_key: str, block: dict) -> dict:
    """dense `data` 배열 스캔 — 셀 값을 바꾸지 않고 이상치 개수만 집계.

    반환 = {"checkedCount", "anomalyCount", "anomalyRatio", "reasons": {reason: count}}.
    """
    data = block.get("data") or []
    checked = len(data)
    anomaly_count = 0
    reasons: dict[str, int] = {}
    for value in data:
        reason = check_value_bounds(pollutant_key, value)
        if reason is not None:
            anomaly_count += 1
            reasons[reason] = reasons.get(reason, 0) + 1
    ratio = (anomaly_count / checked) if checked else 0.0
    return {
        "checkedCount": checked,
        "anomalyCount": anomaly_count,
        "anomalyRatio": round(ratio, 6),
        "reasons": reasons,
    }


def qa_grid_reading(reading: dict, now: datetime | None = None) -> tuple[dict, dict]:
    """grid_reading 전체 QA. 원본 값은 불변 — quality 등급만 조정한 사본을 반환.

    반환 = (updated_reading, qa_report). `updated_reading` 은 얕은 복사 + quality 필드만 교체
    (pollutants/grid 는 원본 참조 그대로 — 값 변조 없음, Glass-box 원칙).
    """
    now = now or datetime.now(timezone.utc)
    pollutants = reading.get("pollutants") or {}
    per_pollutant = {
        key: qa_grid_pollutant_block(key, block) for key, block in pollutants.items()
    }
    total_checked = sum(r["checkedCount"] for r in per_pollutant.values())
    total_anomaly = sum(r["anomalyCount"] for r in per_pollutant.values())
    overall_ratio = (total_anomaly / total_checked) if total_checked else 0.0

    freshness = check_timestamp_freshness(reading, now)
    original_quality = reading.get("quality") or {}
    degraded_quality = degrade_quality_for_anomalies(original_quality, overall_ratio)
    if freshness["isExpired"]:
        degraded_quality = {**degraded_quality, "grade": "F", "score": min(degraded_quality.get("score", 0), 20)}

    qa_report = {
        "kind": "grid",
        "perPollutant": per_pollutant,
        "overallAnomalyRatio": round(overall_ratio, 6),
        "freshness": freshness,
        "originalQuality": original_quality,
        "adjustedQuality": degraded_quality,
    }
    updated_reading = {**reading, "quality": degraded_quality}
    return updated_reading, qa_report


# ────────────────────────── point reading QA (station list) ──────────────────────────

def qa_point_reading(reading: dict) -> list[str]:
    """관측소 1건의 pollutants 값 범위 체크. 이상 사유 리스트(빈 리스트 = 이상 없음)."""
    reasons = []
    for key, block in (reading.get("pollutants") or {}).items():
        value = block.get("value") if isinstance(block, dict) else None
        reason = check_value_bounds(key, value)
        if reason is not None:
            reasons.append(f"{key}: {reason}")
    return reasons


def check_duplicate_coordinates(readings: list[dict], precision: int = 4) -> dict:
    """(lat,lon) 반올림 기준 중복 좌표 탐지. drop 하지 않고 카운트만 리포트.

    `precision=4` 는 `mac_aq_adapter`/`collect_cams_global.convert_mixing_rows` 가 이미
    쓰는 좌표 반올림 자릿수와 통일(cross-file 관례 재사용, 새 상수 발명 아님).
    """
    seen: dict[tuple[float, float], int] = {}
    for r in readings:
        key = (round(r.get("lat", 0.0), precision), round(r.get("lon", 0.0), precision))
        seen[key] = seen.get(key, 0) + 1
    duplicates = {k: v for k, v in seen.items() if v > 1}
    duplicate_reading_count = sum(duplicates.values())
    return {
        "uniqueCoordCount": len(seen),
        "duplicateCoordCount": len(duplicates),
        "duplicateReadingCount": duplicate_reading_count,
    }


def check_spatial_coverage(readings: list[dict], min_expected: int = 1) -> dict:
    """station 개수가 설정된 최소치 이상인지. 지역망별 정확한 기대치는 SOT 미지정이라
    호출자가 소스별로 `min_expected` 를 넘겨야 한다(기본값 1 = "0건 아님"만 보증)."""
    count = len(readings)
    return {"count": count, "minExpected": min_expected, "coverageOk": count >= min_expected}


def qa_point_reading_list(
    readings: list[dict], now: datetime | None = None, min_expected_stations: int = 1,
) -> tuple[list[dict], dict]:
    """point reading 리스트 전체 QA. 각 reading 은 유지하고 `qaFlags` 필드만 추가(additive
    — `mac_aq_adapter.validate_point_reading` 은 필수 필드만 체크하므로 스키마 위반 아님).
    """
    now = now or datetime.now(timezone.utc)
    dup_report = check_duplicate_coordinates(readings)
    coverage_report = check_spatial_coverage(readings, min_expected_stations)

    dup_keys = set()
    seen: dict[tuple[float, float], int] = {}
    for r in readings:
        key = (round(r.get("lat", 0.0), 4), round(r.get("lon", 0.0), 4))
        seen[key] = seen.get(key, 0) + 1
    dup_keys = {k for k, v in seen.items() if v > 1}

    updated = []
    per_reading_anomaly_count = 0
    expired_count = 0
    for r in readings:
        value_reasons = qa_point_reading(r)
        freshness = check_timestamp_freshness(r, now)
        key = (round(r.get("lat", 0.0), 4), round(r.get("lon", 0.0), 4))
        flags = {
            "valueAnomalies": value_reasons,
            "duplicateCoordinate": key in dup_keys,
            "expired": freshness["isExpired"],
            "ageHours": freshness["ageHours"],
        }
        if value_reasons:
            per_reading_anomaly_count += 1
        if freshness["isExpired"]:
            expired_count += 1

        original_quality = r.get("quality") or {}
        reading_pollutants = r.get("pollutants") or {}
        anomaly_ratio = (len(value_reasons) / len(reading_pollutants)) if reading_pollutants else 0.0
        adjusted_quality = degrade_quality_for_anomalies(original_quality, anomaly_ratio)
        if freshness["isExpired"]:
            adjusted_quality = {**adjusted_quality, "grade": "F", "score": min(adjusted_quality.get("score", 0), 20)}

        updated.append({**r, "quality": adjusted_quality, "qaFlags": flags})

    qa_report = {
        "kind": "point",
        "readingCount": len(readings),
        "anomalousReadingCount": per_reading_anomaly_count,
        "expiredReadingCount": expired_count,
        "duplicateCoordinates": dup_report,
        "spatialCoverage": coverage_report,
    }
    return updated, qa_report


# ────────────────────────── timestamp / quality 공통 ──────────────────────────

def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def check_timestamp_freshness(envelope: dict, now: datetime | None = None) -> dict:
    """`validAt`/`expiresAt` 기준 신선도. `expiresAt` 은 설계 SOT §5.6 신선도의 SOT
    (producer 가 이미 채워 넣은 필드 — 여기서 재계산하지 않고 검증만 한다)."""
    now = now or datetime.now(timezone.utc)
    valid_at = envelope.get("validAt")
    expires_at = envelope.get("expiresAt")
    age_hours = None
    is_expired = False
    if valid_at:
        age_hours = round((now - _parse_iso(valid_at)).total_seconds() / 3600, 2)
    if expires_at:
        is_expired = now > _parse_iso(expires_at)
    return {"ageHours": age_hours, "isExpired": is_expired}


_GRADE_BANDS = (  # mac_aq_adapter.estimate_quality 와 동일한 score→grade 밴드(정합 유지)
    (90, "A"), (75, "B"), (60, "C"), (40, "D"),
)


def _grade_from_score(score: float) -> str:
    for threshold, grade in _GRADE_BANDS:
        if score >= threshold:
            return grade
    return "F"


def degrade_quality_for_anomalies(original_quality: dict, anomaly_ratio: float) -> dict:
    """이상치 비율에 따라 quality score 를 깎고 등급을 재계산. 원본보다 등급이 올라가진
    않는다(anomaly_ratio=0 이면 원본 그대로 반환).

    penalty 밴드: 0% 이상 무시(부동소수 잡음) / ≥1% -10점 / ≥10% -40점 / ≥50% -80점.
    설계 SOT 미지정 — `mac_aq_adapter.estimate_quality` 의 grade 밴드와 정합되는 보수적 기본값.
    """
    if anomaly_ratio <= 0:
        return dict(original_quality)
    original_score = original_quality.get("score", 100)
    if anomaly_ratio >= 0.5:
        penalty = 80
    elif anomaly_ratio >= 0.1:
        penalty = 40
    elif anomaly_ratio >= 0.01:
        penalty = 10
    else:
        penalty = 0
    new_score = max(0, original_score - penalty)
    new_grade = _grade_from_score(new_score)
    return {
        **original_quality,
        "score": new_score,
        "grade": new_grade,
        "qaPenaltyApplied": penalty,
    }
