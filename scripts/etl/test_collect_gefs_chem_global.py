"""collect_gefs_chem_global.py 단위 테스트 (AAA). 네트워크/subprocess 없음 — fixture mock.

outage 경로(find_latest_cycle 실패)는 monkeypatch 로 시뮬레이트 — 실 API 호출 없이
"last-good snapshot 보존" 계약을 검증한다.
"""
import pytest

import collect_gefs_chem_global as m


# ────────────────────────── pure helpers ──────────────────────────

def test_grid_header_for_matches_north_first_lo1_convention():
    # Arrange / Act
    header = m.grid_header_for(1.0)
    # Assert — collect_gfs_wind 와 동일 관례(la1=북단, lo1=-180)
    assert header == {"nx": 360, "ny": 181, "la1": 90.0, "lo1": -180.0, "dx": 1.0, "dy": 1.0}


def test_rows_to_ugm3_scales_kgm3_values():
    # Arrange — GEFS 값은 kg/m3 (<1)
    rows = [(0.0, 0.0, 8.5e-8), (1.0, 1.0, 9.0e-8)]
    # Act
    out = m.rows_to_ugm3(rows)
    # Assert
    assert out[0] == (0.0, 0.0, pytest.approx(85.0, rel=1e-2))


def test_rows_to_ugm3_rejects_empty():
    with pytest.raises(ValueError):
        m.rows_to_ugm3([])


def test_build_pollutant_block_produces_dense_array_with_provenance():
    # Arrange — 작은 2x2 격자(nx=2,ny=2) 로 축소해 dense 배열 확인
    header = {"nx": 2, "ny": 2, "la1": 1.0, "lo1": 0.0, "dx": 1.0, "dy": 1.0}
    rows = [(1.0, 0.0, 10.0), (1.0, 1.0, 20.0), (0.0, 0.0, 30.0), (0.0, 1.0, 40.0)]
    # Act
    block = m.build_pollutant_block(rows, header, "PMTF")
    # Assert
    assert block["unit"] == "ug/m3"
    assert block["sourceVariable"] == "PMTF"
    assert block["data"] == [10.0, 20.0, 30.0, 40.0]


def test_compute_expires_at_adds_hours():
    # Arrange / Act
    expires = m.compute_expires_at("2026-07-16T12:00:00Z", 6)
    # Assert
    assert expires == "2026-07-16T18:00:00Z"


def test_assemble_reading_matches_adapter_schema_and_validates():
    # Arrange
    header = {"nx": 2, "ny": 2, "la1": 1.0, "lo1": 0.0, "dx": 1.0, "dy": 1.0}
    blocks = {
        "pm25": {"unit": "ug/m3", "sourceVariable": "PMTF", "conversion": "x",
                 "data": [1.0, 2.0, 3.0, 4.0]},
        "pm10": {"unit": "ug/m3", "sourceVariable": "PMTC", "conversion": "x",
                 "data": [2.0, 4.0, 6.0, 8.0]},
    }
    # Act
    reading = m.assemble_reading(blocks, header, "2026-07-16T15:00:00Z",
                                  "2026-07-16T12:00:00Z", age_hours=3.0)
    # Assert
    assert reading["source"] == "NOAA GEFS-Aerosols"
    assert reading["kind"] == "analysis"
    assert reading["quality"]["grade"] == "A"  # complete + fresh
    import mac_aq_adapter as adapter
    assert adapter.validate_grid_reading(reading) == []


def test_assemble_reading_raises_when_pollutant_count_below_expected():
    # Arrange — pm10 만 있음(2개 기대 중 1개) → estimate_quality 는 통과하지만
    # validate_grid_reading 은 스키마만 보므로, 완전성 판단은 fetch_and_decode 책임.
    # 여기서는 assemble_reading 자체가 empty pollutants 는 raise 하는지만 확인.
    header = {"nx": 1, "ny": 1, "la1": 0.0, "lo1": 0.0, "dx": 1.0, "dy": 1.0}
    with pytest.raises(Exception):
        m.assemble_reading({}, header, "2026-07-16T15:00:00Z", "2026-07-16T12:00:00Z", 3.0)


# ────────────────────────── outage 경로 (fixture mock, 네트워크 0) ──────────────────────────

def test_main_preserves_last_good_snapshot_on_source_outage(tmp_path, monkeypatch, capsys):
    # Arrange — 기존 snapshot 파일이 있다고 가정
    existing = tmp_path / "gefs-chem-global-snapshot.json"
    existing.write_text('{"schemaVersion":1,"note":"last-good"}')
    monkeypatch.setattr(m, "OUTPUT_PATH", str(existing))

    def _raise_outage():
        raise RuntimeError("no available GEFS-Aerosols cycle found (today..2d ago)")

    monkeypatch.setattr(m, "find_latest_cycle", _raise_outage)

    # Act
    rc = m.main()

    # Assert — fail-loud exit 1, 기존 파일 완전 불변
    assert rc == 1
    assert existing.read_text() == '{"schemaVersion":1,"note":"last-good"}'
    err = capsys.readouterr().err
    assert "ERROR" in err


def test_main_preserves_last_good_snapshot_when_a_pollutant_decode_fails(tmp_path, monkeypatch):
    # Arrange — cycle 은 찾지만 grib decode 가 빈 rows 를 반환(fetch_and_decode 내부 실패)
    existing = tmp_path / "gefs-chem-global-snapshot.json"
    existing.write_text('{"schemaVersion":1,"note":"last-good"}')
    monkeypatch.setattr(m, "OUTPUT_PATH", str(existing))
    monkeypatch.setattr(m, "find_latest_cycle", lambda: ("20260716", "12", "key", "idx"))
    monkeypatch.setattr(m, "fetch_and_decode", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("pm25: decode 0 rows — 발행 중단")
    ))

    # Act
    rc = m.main()

    # Assert
    assert rc == 1
    assert existing.read_text() == '{"schemaVersion":1,"note":"last-good"}'
