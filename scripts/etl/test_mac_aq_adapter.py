"""mac_aq_adapter.py 단위 테스트 (AAA). 네트워크 없음 — 순수 함수만 대상."""
import pytest

import mac_aq_adapter as m


def _envelope(**overrides):
    base = dict(
        kind="forecast",
        source="CAMS",
        source_version="cycle-20260716T1200Z",
        generated_at="2026-07-16T15:00:00Z",
        observed_at=None,
        valid_at="2026-07-16T15:00:00Z",
        expires_at="2026-07-17T03:00:00Z",
        resolution_km=44,
        attribution="Contains modified Copernicus Atmosphere Monitoring Service information 2026",
        quality={"grade": "B", "score": 84, "method": "x"},
    )
    base.update(overrides)
    return m.build_envelope(**base)


# ────────────────────────── 단위 변환 ──────────────────────────

def test_air_density_ideal_gas_standard_surface():
    # Arrange — 표준 해면 조건 근사(101325 Pa, 288.15 K ≈ 15°C)
    # Act
    rho = m.air_density_kg_m3(101325.0, 288.15)
    # Assert — 표준 대기 밀도 ≈ 1.225 kg/m3
    assert 1.2 < rho < 1.25


def test_air_density_rejects_nonpositive_inputs():
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        m.air_density_kg_m3(0.0, 288.15)
    with pytest.raises(ValueError):
        m.air_density_kg_m3(101325.0, -1.0)


def test_mixing_ratio_to_ugm3_scales_by_density():
    # Arrange — 1 kg/kg at density 1.2 kg/m3 → 1.2e9 ug/m3 (sanity, not a real atmos value)
    # Act
    result = m.mixing_ratio_to_ugm3(1.0, 1.2)
    # Assert
    assert result == pytest.approx(1.2e9)


def test_mass_conc_kgm3_to_ugm3():
    # Arrange / Act
    result = m.mass_conc_kgm3_to_ugm3(1.32e-8)
    # Assert — CAMS PM2.5 예시값 13.2 µg/m3 근사
    assert result == pytest.approx(13.2, rel=1e-2)


def test_pollutant_value_rounds_and_records_provenance():
    # Arrange / Act
    pv = m.pollutant_value(13.2345, "pm2p5", "kg/m3 x 1e9")
    # Assert
    assert pv == {
        "value": 13.23,
        "unit": "ug/m3",
        "sourceVariable": "pm2p5",
        "conversion": "kg/m3 x 1e9",
    }


# ────────────────────────── quality 휴리스틱 ──────────────────────────

def test_estimate_quality_full_and_fresh_is_grade_a():
    # Arrange / Act
    q = m.estimate_quality(pollutant_count=7, expected_count=7, age_hours=2)
    # Assert
    assert q["grade"] == "A"
    assert q["score"] == 100
    assert "not platform DQSS" in q["method"]


def test_estimate_quality_partial_and_stale_is_low_grade():
    # Arrange — 절반만 오고 이틀 지남
    # Act
    q = m.estimate_quality(pollutant_count=3, expected_count=7, age_hours=60)
    # Assert — completeness ~0.43, freshness 0.2 → min=0.2 → score 20 → F
    assert q["grade"] == "F"
    assert q["score"] == 20


def test_estimate_quality_rejects_zero_expected():
    with pytest.raises(ValueError):
        m.estimate_quality(pollutant_count=0, expected_count=0, age_hours=1)


def test_estimate_quality_rejects_negative_age():
    with pytest.raises(ValueError):
        m.estimate_quality(pollutant_count=1, expected_count=1, age_hours=-1)


# ────────────────────────── envelope / point / grid 조립 ──────────────────────────

def test_build_envelope_stamps_schema_version():
    env = _envelope()
    assert env["schemaVersion"] == m.SCHEMA_VERSION
    assert env["kind"] == "forecast"


def test_build_envelope_rejects_invalid_kind():
    with pytest.raises(ValueError):
        _envelope(kind="measurement")


def test_build_point_reading_matches_design_doc_schema():
    # Arrange
    env = _envelope(kind="observation", source="AirKorea", observed_at="2026-07-16T15:00:00Z")
    pollutants = {"pm25": m.pollutant_value(13.2, "PM25Value", "direct ug/m3")}
    # Act
    reading = m.build_point_reading(env, lat=37.57, lon=126.98, pollutants=pollutants)
    # Assert
    assert reading["lat"] == 37.57
    assert reading["lon"] == 126.98
    assert reading["pollutants"] == pollutants
    assert m.validate_point_reading(reading) == []


@pytest.mark.parametrize("lat,lon", [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
def test_build_point_reading_rejects_out_of_range_coords(lat, lon):
    env = _envelope()
    with pytest.raises(ValueError):
        m.build_point_reading(env, lat=lat, lon=lon, pollutants={"pm25": m.pollutant_value(1, "x", "y")})


def test_build_point_reading_rejects_empty_pollutants():
    env = _envelope()
    with pytest.raises(ValueError):
        m.build_point_reading(env, lat=0.0, lon=0.0, pollutants={})


def test_build_grid_reading_accepts_matching_length_and_validates():
    # Arrange
    env = _envelope()
    header = {"nx": 2, "ny": 2, "lo1": -180.0, "la1": 90.0, "dx": 0.4, "dy": 0.4}
    pollutants = {"pm25": {"unit": "ug/m3", "sourceVariable": "pm2p5", "conversion": "kg/m3 x 1e9",
                            "data": [1.0, 2.0, 3.0, 4.0]}}
    # Act
    reading = m.build_grid_reading(env, header, pollutants)
    # Assert
    assert reading["grid"] == header
    assert m.validate_grid_reading(reading) == []


def test_build_grid_reading_rejects_incomplete_grid():
    # Arrange — nx*ny=4 인데 data 는 3개 뿐(0 으로 메우지 않고 실패)
    env = _envelope()
    header = {"nx": 2, "ny": 2, "lo1": -180.0, "la1": 90.0, "dx": 0.4, "dy": 0.4}
    pollutants = {"pm25": {"unit": "ug/m3", "sourceVariable": "pm2p5", "conversion": "x",
                            "data": [1.0, 2.0, 3.0]}}
    # Act / Assert
    with pytest.raises(ValueError):
        m.build_grid_reading(env, header, pollutants)


def test_validate_grid_reading_flags_missing_header_keys():
    # Arrange — validate 함수는 raise 하지 않고 error list 반환(호출자가 발행 게이트로 사용)
    record = {
        "schemaVersion": m.SCHEMA_VERSION, "kind": "forecast", "source": "CAMS",
        "sourceVersion": "x", "generatedAt": "x", "observedAt": None, "validAt": "x",
        "expiresAt": "x", "resolutionKm": 44, "quality": {}, "attribution": "x",
        "grid": {"nx": 2, "ny": 2},  # lo1/la1/dx/dy 누락
        "pollutants": {"pm25": {"data": [1, 2, 3, 4]}},
    }
    # Act
    errors = m.validate_grid_reading(record)
    # Assert
    assert any("grid header" in e for e in errors)
