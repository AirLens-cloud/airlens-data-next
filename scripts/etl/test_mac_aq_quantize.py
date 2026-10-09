"""mac_aq_quantize.py 단위 테스트 (AAA) — 정밀도 절감 + 왕복(idempotency/오차 상한) + 보호 필드."""
import mac_aq_adapter as adapter
import mac_aq_quantize as quantize


# ────────────────────────── quantize_value ──────────────────────────

def test_quantize_value_rounds_to_default_decimals():
    # Arrange / Act
    result = quantize.quantize_value(13.2345)
    # Assert
    assert result == 13.2


def test_quantize_value_passes_through_none():
    # Arrange / Act / Assert
    assert quantize.quantize_value(None) is None


def test_quantize_value_passes_through_non_numeric():
    # Arrange / Act / Assert — 방어적: 문자열은 그대로(스키마 검증이 별도로 잡음)
    assert quantize.quantize_value("not-a-number") == "not-a-number"


# ────────────────────────── round-trip: idempotency + bounded error ──────────────────────────

def test_quantize_value_is_idempotent():
    # Arrange
    once = quantize.quantize_value(13.2345, 1)
    # Act
    twice = quantize.quantize_value(once, 1)
    # Assert — 재적용해도 값이 더 안 변한다(양자화의 왕복 안정성)
    assert once == twice


def test_quantize_value_error_bounded_by_decimal_precision():
    # Arrange
    original = 13.2345
    decimals = 1
    # Act
    quantized = quantize.quantize_value(original, decimals)
    # Assert — 오차는 항상 0.5 * 10^-decimals 이하(반올림 정의)
    assert abs(original - quantized) <= 0.5 * (10 ** -decimals) + 1e-9


# ────────────────────────── grid pollutant block ──────────────────────────

def test_quantize_grid_pollutant_block_rounds_data_preserves_provenance_fields():
    # Arrange
    block = {"unit": "ug/m3", "sourceVariable": "pm2p5", "conversion": "x",
             "data": [13.2345, 0.0, -1.111]}
    # Act
    result = quantize.quantize_grid_pollutant_block(block, decimals=1)
    # Assert
    assert result["data"] == [13.2, 0.0, -1.1]
    assert result["unit"] == "ug/m3"
    assert result["sourceVariable"] == "pm2p5"
    assert result["conversion"] == "x"


# ────────────────────────── §5 가드: 보호 필드 불변 ──────────────────────────

def test_quantize_reading_never_touches_quality_or_provenance():
    # Arrange
    env = adapter.build_envelope(
        kind="analysis", source="CAMS", source_version="x", generated_at="2026-07-16T15:00:00Z",
        observed_at=None, valid_at="2026-07-16T15:00:00Z", expires_at="2026-07-17T03:00:00Z",
        resolution_km=44, attribution="x", quality={"grade": "B", "score": 84.777, "method": "x"},
    )
    header = {"nx": 1, "ny": 1, "lo1": -180.0, "la1": 90.0, "dx": 0.4, "dy": 0.4}
    pollutants = {"pm25": {"unit": "ug/m3", "sourceVariable": "x", "conversion": "y", "data": [13.2345]}}
    reading = adapter.build_grid_reading(env, header, pollutants)
    reading["provenance"] = {"pipelineVersion": "x", "qa": {"anomalyCount": 3}}
    # Act
    result = quantize.quantize_reading(reading, decimals=1)
    # Assert — quality/provenance 는 원본 그대로(score 소수점도 안 건드림)
    assert result["quality"] == {"grade": "B", "score": 84.777, "method": "x"}
    assert result["provenance"] == {"pipelineVersion": "x", "qa": {"anomalyCount": 3}}
    # pollutants 만 양자화
    assert result["pollutants"]["pm25"]["data"] == [13.2]


def test_quantize_reading_detects_point_reading_and_rounds_value():
    # Arrange
    env = adapter.build_envelope(
        kind="observation", source="AirKorea", source_version="x", generated_at="2026-07-16T15:00:00Z",
        observed_at="2026-07-16T15:00:00Z", valid_at="2026-07-16T15:00:00Z",
        expires_at="2026-07-16T16:00:00Z", resolution_km=1.0, attribution="x",
        quality={"grade": "A", "score": 100},
    )
    pollutants = {"pm25": adapter.pollutant_value(13.2345, "PM25Value", "direct")}
    reading = adapter.build_point_reading(env, lat=37.5, lon=127.0, pollutants=pollutants)
    # Act
    result = quantize.quantize_reading(reading, decimals=1)
    # Assert
    assert result["pollutants"]["pm25"]["value"] == 13.2
    assert result["lat"] == 37.5  # 좌표는 pollutants 밖 — 그대로


# ────────────────────────── size reduction ──────────────────────────

def test_estimate_size_reduction_reports_smaller_or_equal_bytes():
    # Arrange — 소수점이 긴 값 다수(grid 경로를 타도록 "grid" 마커 포함)
    original = {
        "grid": {"nx": 1, "ny": 1},
        "pollutants": {"pm25": {"data": [13.234567, 0.000001, 999.999999] * 50}},
    }
    quantized = quantize.quantize_reading(original, decimals=1)
    # Act
    report = quantize.estimate_size_reduction(original, quantized)
    # Assert
    assert report["savedBytes"] >= 0
    assert report["quantizedBytes"] <= report["originalBytes"]
