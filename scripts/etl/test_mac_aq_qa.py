"""mac_aq_qa.py 단위 테스트 (AAA). 네트워크 없음 — 순수 함수만 대상."""
from datetime import datetime, timezone

import mac_aq_adapter as adapter
import mac_aq_qa as qa


def _envelope(**overrides):
    base = dict(
        kind="analysis",
        source="CAMS",
        source_version="cycle-20260716T1200Z",
        generated_at="2026-07-16T15:00:00Z",
        observed_at=None,
        valid_at="2026-07-16T15:00:00Z",
        expires_at="2026-07-17T03:00:00Z",
        resolution_km=44,
        attribution="x",
        quality={"grade": "B", "score": 84, "method": "x"},
    )
    base.update(overrides)
    return adapter.build_envelope(**base)


# ────────────────────────── value bounds ──────────────────────────

def test_check_value_bounds_accepts_normal_pm25():
    # Arrange / Act
    reason = qa.check_value_bounds("pm25", 13.2)
    # Assert
    assert reason is None


def test_check_value_bounds_rejects_negative():
    # Arrange / Act
    reason = qa.check_value_bounds("pm25", -1.0)
    # Assert
    assert reason == "below min 0.0"


def test_check_value_bounds_rejects_absurdly_high():
    # Arrange / Act
    reason = qa.check_value_bounds("pm25", 5000.0)
    # Assert
    assert "above max" in reason


def test_check_value_bounds_rejects_nan():
    # Arrange / Act
    reason = qa.check_value_bounds("o3", float("nan"))
    # Assert
    assert reason == "NaN/Inf value"


def test_check_value_bounds_unknown_key_passes():
    # Arrange / Act — 알 수 없는 키는 판단 보류(스키마 검증이 별도로 잡음)
    reason = qa.check_value_bounds("pm999", 1.0)
    # Assert
    assert reason is None


# ────────────────────────── grid QA — no silent drop ──────────────────────────

def test_qa_grid_pollutant_block_counts_anomalies_without_mutating_data():
    # Arrange — 4 cells, 1 negative anomaly
    block = {"unit": "ug/m3", "sourceVariable": "x", "conversion": "y",
             "data": [1.0, -5.0, 3.0, 4.0]}
    # Act
    report = qa.qa_grid_pollutant_block("pm25", block)
    # Assert — 원본 데이터 불변, anomaly 만 집계
    assert block["data"] == [1.0, -5.0, 3.0, 4.0]
    assert report["checkedCount"] == 4
    assert report["anomalyCount"] == 1
    assert report["anomalyRatio"] == 0.25


def test_qa_grid_reading_degrades_quality_but_keeps_pollutants_intact():
    # Arrange — 절반이 이상치(anomaly_ratio=0.5 -> heavy penalty)
    env = _envelope()
    header = {"nx": 2, "ny": 2, "lo1": -180.0, "la1": 90.0, "dx": 0.4, "dy": 0.4}
    pollutants = {"pm25": {"unit": "ug/m3", "sourceVariable": "x", "conversion": "y",
                           "data": [1.0, -5.0, 3.0, 4.0]}}
    reading = adapter.build_grid_reading(env, header, pollutants)
    now = datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc)  # 1h 후 — 아직 fresh
    # Act
    updated, report = qa.qa_grid_reading(reading, now)
    # Assert — pollutants(원본 값)는 그대로, quality 만 강등
    assert updated["pollutants"] == reading["pollutants"]
    assert updated["quality"]["grade"] != "A"
    assert report["overallAnomalyRatio"] == 0.25
    assert report["perPollutant"]["pm25"]["anomalyCount"] == 1


def test_qa_grid_reading_marks_expired_as_f_grade():
    # Arrange — now 가 expiresAt 훨씬 지난 시점
    env = _envelope(expires_at="2026-07-17T03:00:00Z")
    header = {"nx": 1, "ny": 1, "lo1": -180.0, "la1": 90.0, "dx": 0.4, "dy": 0.4}
    pollutants = {"pm25": {"unit": "ug/m3", "sourceVariable": "x", "conversion": "y", "data": [1.0]}}
    reading = adapter.build_grid_reading(env, header, pollutants)
    now = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)  # 훨씬 지남
    # Act
    updated, report = qa.qa_grid_reading(reading, now)
    # Assert
    assert updated["quality"]["grade"] == "F"
    assert report["freshness"]["isExpired"] is True


# ────────────────────────── point reading QA — flag not drop ──────────────────────────

def _point(lat, lon, pm25_value, **env_overrides):
    env = _envelope(kind="observation", source="AirKorea", **env_overrides)
    pollutants = {"pm25": adapter.pollutant_value(pm25_value, "PM25Value", "direct")}
    return adapter.build_point_reading(env, lat=lat, lon=lon, pollutants=pollutants)


def test_qa_point_reading_list_flags_anomaly_without_dropping():
    # Arrange — 2 readings, 1 has an out-of-range value
    readings = [_point(37.5, 127.0, 13.2), _point(35.1, 129.0, -50.0)]
    now = datetime(2026, 7, 16, 15, 30, tzinfo=timezone.utc)
    # Act
    updated, report = qa.qa_point_reading_list(readings, now)
    # Assert — 둘 다 리스트에 남아있음(drop 없음), 이상치만 flag+등급 강등
    assert len(updated) == 2
    assert updated[0]["qaFlags"]["valueAnomalies"] == []
    assert "pm25" in updated[1]["qaFlags"]["valueAnomalies"][0]
    assert updated[1]["quality"]["grade"] != updated[0]["quality"]["grade"] or updated[1]["quality"]["score"] < updated[0]["quality"]["score"]
    assert report["anomalousReadingCount"] == 1
    assert report["readingCount"] == 2


def _multi_pollutant_point(lat, lon, values: dict, **env_overrides):
    """여러 pollutant 를 가진 point reading — 비율 기반 강등 테스트용."""
    env = _envelope(kind="observation", source="AirKorea", **env_overrides)
    pollutants = {k: adapter.pollutant_value(v, k, "direct") for k, v in values.items()}
    return adapter.build_point_reading(env, lat=lat, lon=lon, pollutants=pollutants)


def test_qa_point_reading_list_scales_penalty_by_anomaly_ratio_not_binary():
    # Arrange — 어댑터 POLLUTANT_KEYS 7종 중 1개(pm25)만 이상치.
    # ratio = 1/7 ≈ 0.143 → ">=0.1" 밴드(-40) — 이전 버전(항상 ratio=1.0 → -80, A→F 강제)
    # 과 달리 grid 경로(qa_grid_reading)와 동일한 비율 기반 로직으로 대칭.
    values = {"pm1": 1.0, "pm25": -5.0, "pm10": 2.0, "o3": 3.0, "no2": 4.0, "so2": 5.0, "co": 6.0}
    reading = _multi_pollutant_point(37.5, 127.0, values, quality={"grade": "A", "score": 95})
    now = datetime(2026, 7, 16, 15, 30, tzinfo=timezone.utc)
    # Act
    updated, _report = qa.qa_point_reading_list([reading], now)
    # Assert — 1/7 이탈은 "-40 밴드"(0.1<=ratio<0.5) — 최고 강도(-80, A→F 즉시 강등)가 아님
    assert updated[0]["quality"]["qaPenaltyApplied"] == 40
    assert updated[0]["quality"]["score"] == 55
    assert updated[0]["quality"]["grade"] == "D"


def test_qa_point_reading_list_minor_ratio_lands_in_light_penalty_band():
    # Arrange — 15개 pollutant 항목 중 1개만 이상치. ratio = 1/15 ≈ 0.067
    # → "0.01<=ratio<0.1" 밴드(-10) — 대다수가 정상인 리딩은 경미하게만 강등된다.
    values = {"pm25": -5.0, **{f"aux{i}": 1.0 for i in range(14)}}
    reading = _multi_pollutant_point(37.5, 127.0, values, quality={"grade": "A", "score": 95})
    now = datetime(2026, 7, 16, 15, 30, tzinfo=timezone.utc)
    # Act
    updated, _report = qa.qa_point_reading_list([reading], now)
    # Assert
    assert updated[0]["quality"]["qaPenaltyApplied"] == 10
    assert updated[0]["quality"]["score"] == 85
    assert updated[0]["quality"]["grade"] == "B"


def test_check_duplicate_coordinates_detects_without_removing():
    # Arrange — 같은 좌표 station 2건
    readings = [_point(37.5, 127.0, 10.0), _point(37.5, 127.0, 11.0), _point(1.0, 1.0, 5.0)]
    # Act
    report = qa.check_duplicate_coordinates(readings)
    # Assert
    assert report["duplicateCoordCount"] == 1
    assert report["duplicateReadingCount"] == 2
    assert report["uniqueCoordCount"] == 2


def test_qa_point_reading_list_flags_duplicate_coordinate():
    # Arrange
    readings = [_point(37.5, 127.0, 10.0), _point(37.5, 127.0, 11.0)]
    now = datetime(2026, 7, 16, 15, 30, tzinfo=timezone.utc)
    # Act
    updated, _report = qa.qa_point_reading_list(readings, now)
    # Assert
    assert updated[0]["qaFlags"]["duplicateCoordinate"] is True
    assert updated[1]["qaFlags"]["duplicateCoordinate"] is True
    # drop 없음
    assert len(updated) == 2


def test_check_spatial_coverage_flags_low_count():
    # Arrange / Act
    report = qa.check_spatial_coverage([{"lat": 0, "lon": 0}], min_expected=5)
    # Assert
    assert report["coverageOk"] is False
    assert report["count"] == 1


# ────────────────────────── timestamp freshness ──────────────────────────

def test_check_timestamp_freshness_not_expired_when_within_window():
    # Arrange
    env = _envelope(valid_at="2026-07-16T15:00:00Z", expires_at="2026-07-17T03:00:00Z")
    now = datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc)
    # Act
    result = qa.check_timestamp_freshness(env, now)
    # Assert
    assert result["isExpired"] is False
    assert result["ageHours"] == 1.0


def test_check_timestamp_freshness_expired_past_expiry():
    # Arrange
    env = _envelope(valid_at="2026-07-16T15:00:00Z", expires_at="2026-07-17T03:00:00Z")
    now = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)
    # Act
    result = qa.check_timestamp_freshness(env, now)
    # Assert
    assert result["isExpired"] is True


# ────────────────────────── quality degrade band ──────────────────────────

def test_degrade_quality_for_anomalies_no_change_when_zero_ratio():
    # Arrange / Act
    result = qa.degrade_quality_for_anomalies({"grade": "A", "score": 95}, 0.0)
    # Assert
    assert result == {"grade": "A", "score": 95}


def test_degrade_quality_for_anomalies_heavy_penalty_at_50_percent():
    # Arrange / Act
    result = qa.degrade_quality_for_anomalies({"grade": "A", "score": 95}, 0.5)
    # Assert
    assert result["score"] == 15
    assert result["grade"] == "F"
    assert result["qaPenaltyApplied"] == 80


def test_degrade_quality_for_anomalies_never_negative_score():
    # Arrange / Act
    result = qa.degrade_quality_for_anomalies({"grade": "D", "score": 40}, 0.5)
    # Assert
    assert result["score"] == 0
    assert result["grade"] == "F"


def test_check_value_bounds_pm10_keeps_recorded_dust_storm_extreme():
    # Arrange/Act — 6,460 µg/m3 는 회수된 PM10 시간평균 기록 극값이다. 구 상한
    # 3,000 은 이 실제 사건을 오탐했다 — "실제 극단치를 false-positive 로 잡지
    # 않는다" 는 이 모듈의 자기 목적 위반 (2026-09-05 findings.md Q9 봉투로 교정).
    reason = qa.check_value_bounds("pm10", 6460.0)
    # Assert
    assert reason is None


def test_check_value_bounds_pm10_rejects_above_envelope():
    # Arrange/Act — 12,585 µg/m3 (2026-09-03 지구본 실사고 값) 는 기록 극값
    # 봉투(≈7,414) 위 내부 상한(10,000)을 넘는다.
    reason = qa.check_value_bounds("pm10", 12585.0)
    # Assert
    assert reason == "above max 10000.0"
