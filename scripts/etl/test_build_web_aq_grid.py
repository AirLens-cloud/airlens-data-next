"""build_web_aq_grid.py 단위 테스트 (AAA). 네트워크 없음 — 순수 함수 + 작은 synthetic fixture.

핵심 회귀 대상(R-W1): mac 격자(행0=북단, la1 origin) → 웹 `AQGridResponse`(latMin=남단
origin) 변환에서 남/북이 뒤집히면 지구본이 상하 반전된다 — 코너 값 대조로 이를 막는다.
"""
from __future__ import annotations

import json

import pytest

import build_web_aq_grid as m

UTC_Z = "Z"

# 4x3 synthetic 격자(la1=10 북단, lo1=0 서단, dy=dx=5) — 행0=북단 관례.
#   row0 (lat=10): [1, 2, 3, 4]
#   row1 (lat=5) : [5, 6, 7, 8]
#   row2 (lat=0) : [9, 10, 11, 12]
_GRID = {"nx": 4, "ny": 3, "la1": 10.0, "lo1": 0.0, "dx": 5.0, "dy": 5.0}
_DATA = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]


def _reading(**overrides) -> dict:
    base = {
        "schemaVersion": 1,
        "kind": "analysis",
        "source": "NOAA GEFS-Aerosols",
        "generatedAt": "2026-08-19T09:00:00Z",
        "grid": _GRID,
        "pollutants": {
            "pm25": {"unit": "ug/m3", "sourceVariable": "PMTF", "conversion": "x", "data": list(_DATA)},
            "pm10": {"unit": "ug/m3", "sourceVariable": "PMTC", "conversion": "x", "data": [v * 2 for v in _DATA]},
        },
    }
    base.update(overrides)
    return base


# ────────────────────────── pure helpers ──────────────────────────

def test_parse_generated_at_ms_preserves_original_timestamp_not_now():
    # Arrange / Act
    ts = m.parse_generated_at_ms("2026-07-16T12:00:00Z")
    # Assert — 고정된 과거 시각이 그대로 epoch ms 로 변환됐는지(현재 시각 아님)
    assert ts == 1784203200000


def test_resolve_stride_no_downsample_when_target_equals_native():
    assert m.resolve_stride(target_res_deg=5.0, native_res_deg=5.0) == 1


def test_resolve_stride_downsamples_when_target_coarser():
    # 실 운영 시나리오 — GEFS 1° 원본 -> 웹 5° 목표
    assert m.resolve_stride(target_res_deg=5.0, native_res_deg=1.0) == 5


def test_resolve_stride_never_upsamples_below_native():
    # 목표가 원본보다 촘촘해도 stride 는 최소 1 (없는 해상도를 지어내지 않는다)
    assert m.resolve_stride(target_res_deg=0.1, native_res_deg=1.0) == 1


def test_resolve_stride_rejects_non_positive():
    with pytest.raises(ValueError):
        m.resolve_stride(0, 1.0)


def test_south_anchored_lat_min_flips_from_north_origin_to_south_origin():
    # Arrange — la1=10(북단) 격자, 다운샘플 없음(stride=1, ny2=3)
    # Act
    lat_min = m.south_anchored_lat_min(la1=10.0, dy=5.0, ny2=3, stride=1)
    # Assert — 남단(0) 이어야 한다. la1(10) 을 그대로 쓰면(회귀) 지구본이 뒤집힌다.
    assert lat_min == 0.0


def test_south_anchored_lat_min_accounts_for_downsample_stride():
    # Arrange — stride=2 로 다운샘플된 경우 남단 좌표도 stride 만큼 성기게 재계산돼야 함
    # Act
    lat_min = m.south_anchored_lat_min(la1=10.0, dy=5.0, ny2=2, stride=2)
    # Assert
    assert lat_min == 0.0  # (2-1)*2*5 = 10 -> 10-10=0, 원본 남단과 일치(이 fixture 는 정확히 나뉨)


def test_build_points_corner_values_match_source_grid_no_row_flip_bug():
    # Arrange — stride=1(다운샘플 없음), 4x3 fixture 그대로
    # Act
    points, _, _ = m.build_points(_DATA, nx2=4, ny2=3, la1=10.0, lo1=0.0, stride=1, dy=5.0, dx=5.0)
    by_coord = {(p["lat"], p["lon"]): p["value"] for p in points}
    # Assert — 북서 코너(행0,열0)=1, 남동 코너(행2,열3)=12. 뒤집혔다면 이 두 값이 서로 바뀐다.
    assert by_coord[(10.0, 0.0)] == 1
    assert by_coord[(0.0, 15.0)] == 12
    assert len(points) == 12


def test_build_points_excludes_non_finite_values():
    # Arrange — 결측 셀 하나(None) — 0 으로 메우지 않고 제외해야 한다
    data = list(_DATA)
    data[5] = None
    # Act
    points, _, _ = m.build_points(data, nx2=4, ny2=3, la1=10.0, lo1=0.0, stride=1, dy=5.0, dx=5.0)
    # Assert
    assert len(points) == 11
    assert all(p["value"] is not None for p in points)


def test_convert_pollutant_downsamples_and_matches_web_grid_contract():
    # Arrange — 원본 5° 격자, 웹 목표도 5°(stride=1, 그대로) — AQGridResponse 필드 전수 확인
    reading = _reading()
    # Act
    doc = m.convert_pollutant(reading, "pm25", target_res_deg=5.0)
    # Assert
    assert doc["variable"] == "pm2_5"
    assert doc["nLat"] == 3 and doc["nLon"] == 4
    assert doc["latMin"] == 0.0
    assert doc["lonMin"] == 0.0
    assert doc["dLat"] == 5.0 and doc["dLon"] == 5.0
    assert doc["timestamp"] == m.parse_generated_at_ms(reading["generatedAt"])
    assert doc["source"] == "NOAA GEFS-Aerosols"
    assert len(doc["points"]) == 12


def test_convert_pollutant_downsamples_to_coarser_web_resolution():
    # Arrange — 원본 5°, 웹 목표 10° -> stride=2 -> 2x2 격자로 축소
    reading = _reading()
    # Act
    doc = m.convert_pollutant(reading, "pm25", target_res_deg=10.0)
    # Assert
    assert doc["nLat"] == 2 and doc["nLon"] == 2
    assert doc["dLat"] == 10.0 and doc["dLon"] == 10.0
    assert len(doc["points"]) == 4


def test_convert_pollutant_raises_when_pollutant_absent():
    # Arrange — pm10 없는 입력
    reading = _reading()
    del reading["pollutants"]["pm10"]
    # Act / Assert — 0 으로 메우지 않고 raise
    with pytest.raises(ValueError, match="pm10"):
        m.convert_pollutant(reading, "pm10", 5.0)


def test_convert_pollutant_raises_on_data_length_mismatch():
    reading = _reading()
    reading["pollutants"]["pm25"]["data"] = [1, 2, 3]  # nx*ny=12 인데 3개뿐
    with pytest.raises(ValueError, match="length"):
        m.convert_pollutant(reading, "pm25", 5.0)


def test_convert_pollutant_raises_when_generated_at_missing():
    reading = _reading()
    del reading["generatedAt"]
    with pytest.raises(ValueError, match="generatedAt"):
        m.convert_pollutant(reading, "pm25", 5.0)


def test_build_health_projects_only_four_fields():
    # Arrange — build_mac_index.py 산출 index.json 모양(quality/coverage 등 부가 필드 포함)
    index = {
        "generatedAt": "2026-08-19T09:00:00Z",
        "note": "...",
        "sources": {
            "gefs-chem": {
                "available": True, "lastAttemptAt": "x", "servedFrom": "fresh",
                "kind": "analysis", "generatedAt": "2026-08-19T09:00:00Z",
                "expiresAt": "2026-08-19T15:00:00Z", "quality": {"grade": "A"},
            },
            "cams": {"available": False, "servedFrom": None, "unavailableReason": "failure"},
        },
    }
    # Act
    health = m.build_health(index)
    # Assert — 4필드만 남고 quality/coverage/lastAttemptAt 등은 축약된다
    assert health["generatedAt"] == "2026-08-19T09:00:00Z"
    assert set(health["sources"]["gefs-chem"]) == {"generatedAt", "expiresAt", "servedFrom", "available"}
    assert health["sources"]["gefs-chem"]["available"] is True
    assert health["sources"]["cams"]["available"] is False


# ────────────────────────────── CLI (fixture 파일, 네트워크 0) ──────────────────────────────

def test_main_writes_both_pollutant_grids(tmp_path):
    # Arrange
    input_path = tmp_path / "gefs-chem.json"
    input_path.write_text(json.dumps(_reading()))
    out_dir = tmp_path / "out"

    # Act
    rc = m.main(["--input", str(input_path), "--out-dir", str(out_dir), "--web-resolution-deg", "5.0"])

    # Assert
    assert rc == 0
    pm25 = json.loads((out_dir / "current-pm25-grid.json").read_text())
    pm10 = json.loads((out_dir / "current-pm10-grid.json").read_text())
    assert pm25["variable"] == "pm2_5"
    assert pm10["variable"] == "pm10"
    assert len(pm25["points"]) == 12


def test_main_skips_missing_pollutant_without_failing(tmp_path):
    # Arrange — pm10 없는 입력(예: 형제 소스가 부분 실패)
    reading = _reading()
    del reading["pollutants"]["pm10"]
    input_path = tmp_path / "gefs-chem.json"
    input_path.write_text(json.dumps(reading))
    out_dir = tmp_path / "out"

    # Act
    rc = m.main(["--input", str(input_path), "--out-dir", str(out_dir)])

    # Assert
    assert rc == 0
    assert (out_dir / "current-pm25-grid.json").exists()
    assert not (out_dir / "current-pm10-grid.json").exists()


def test_main_fails_loud_when_input_missing(tmp_path):
    rc = m.main(["--input", str(tmp_path / "nope.json"), "--out-dir", str(tmp_path / "out")])
    assert rc == 1


def test_main_fails_loud_when_all_pollutants_absent(tmp_path):
    # Arrange
    input_path = tmp_path / "gefs-chem.json"
    input_path.write_text(json.dumps({**_reading(), "pollutants": {}}))
    out_dir = tmp_path / "out"

    # Act
    rc = m.main(["--input", str(input_path), "--out-dir", str(out_dir)])

    # Assert
    assert rc == 1


def test_main_enforces_size_gate(tmp_path, monkeypatch):
    # Arrange — 게이트를 인위적으로 아주 작게 낮춰 12점짜리 fixture 도 초과하게 만든다
    monkeypatch.setattr(m, "MAX_OUTPUT_BYTES", 10)
    input_path = tmp_path / "gefs-chem.json"
    input_path.write_text(json.dumps(_reading()))
    out_dir = tmp_path / "out"

    # Act
    rc = m.main(["--input", str(input_path), "--out-dir", str(out_dir)])

    # Assert — 명시적 실패, 부분 산출물을 남기지 않는다(pm25 가 먼저 실패하면 pm10 파일도 없음)
    assert rc == 1
    assert not (out_dir / "current-pm25-grid.json").exists()


def test_main_preserves_last_good_output_on_failure(tmp_path):
    # Arrange — 기존 웹 grid 파일이 있는 상태에서, 이번 입력이 깨져 있다고 가정
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "current-pm25-grid.json"
    existing.write_text('{"note":"last-good"}')

    broken = _reading()
    del broken["generatedAt"]
    input_path = tmp_path / "gefs-chem.json"
    input_path.write_text(json.dumps(broken))

    # Act
    rc = m.main(["--input", str(input_path), "--out-dir", str(out_dir)])

    # Assert — 실패해도 기존 파일은 os.replace 이전이라 그대로 남는다
    assert rc == 1
    assert existing.read_text() == '{"note":"last-good"}'


def test_main_writes_health_json_from_index_file(tmp_path):
    # Arrange
    input_path = tmp_path / "gefs-chem.json"
    input_path.write_text(json.dumps(_reading()))
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "generatedAt": "2026-08-19T09:00:00Z",
        "sources": {"gefs-chem": {"available": True, "servedFrom": "fresh",
                                   "generatedAt": "x", "expiresAt": "y"}},
    }))
    out_dir = tmp_path / "out"

    # Act
    rc = m.main(["--input", str(input_path), "--out-dir", str(out_dir), "--index", str(index_path)])

    # Assert
    assert rc == 0
    health = json.loads((out_dir / "health.json").read_text())
    assert health["sources"]["gefs-chem"]["available"] is True


def test_main_grid_still_publishes_when_index_missing(tmp_path):
    # Arrange — --index 를 아예 안 줌(health.json 은 선택적 부가 산출물)
    input_path = tmp_path / "gefs-chem.json"
    input_path.write_text(json.dumps(_reading()))
    out_dir = tmp_path / "out"

    # Act
    rc = m.main(["--input", str(input_path), "--out-dir", str(out_dir)])

    # Assert
    assert rc == 0
    assert not (out_dir / "health.json").exists()


# ── 음수 결측 처리 (2026-09-04) ─────────────────────────────────────────────
#
# `mac_aq_qa.py` 는 음수를 잡지만 **세기만** 한다(qa_grid_pollutant_block —
# "셀 값을 바꾸지 않고 이상치 개수만 집계"). 그래서 음수는 이 변환기까지 흘러온다.
# NOAA 직행 경로(collect_noaa_aq.py)는 이미 같은 정책을 적용하므로, 안 맞추면
# 같은 파일명(current-pm25-grid.json)이 경로마다 다른 정책으로 발행된다.

def test_build_points_excludes_negative_values_and_counts_them():
    # Arrange — 질량농도는 음수가 될 수 없다 = 값이 아니라 결측
    data = list(_DATA)
    data[5] = -3.2
    # Act
    points, n_negative, _ = m.build_points(data, nx2=4, ny2=3, la1=10.0, lo1=0.0, stride=1, dy=5.0, dx=5.0)
    # Assert — 0 으로 메우지 않고 빼되, 뺐다는 사실을 센다
    assert len(points) == 11
    assert n_negative == 1
    assert all(p["value"] >= 0 for p in points)


def test_build_points_keeps_zero_which_is_a_real_measurement():
    # Arrange — 0 은 "측정값 0" 이지 결측이 아니다. 음수 검사가 0 을 삼키면
    # 청정 지역 셀이 통째로 사라진다.
    data = list(_DATA)
    data[5] = 0
    # Act
    points, n_negative, _ = m.build_points(data, nx2=4, ny2=3, la1=10.0, lo1=0.0, stride=1, dy=5.0, dx=5.0)
    # Assert
    assert len(points) == 12
    assert n_negative == 0


def test_convert_pollutant_discloses_dropped_negatives():
    # Arrange
    reading = _reading()
    reading["pollutants"]["pm25"]["data"][2] = -1.0
    # Act
    doc = m.convert_pollutant(reading, "pm25", target_res_deg=5.0)
    # Assert
    assert doc["nNegativeCellsDropped"] == 1
    assert len(doc["points"]) == 11


def test_convert_pollutant_discloses_zero_not_absence_when_grid_is_clean():
    # Arrange — 깨끗한 격자. 필드를 아예 빼면 "음수 0건" 과 "음수를 세지 않음" 이
    # 소비자 쪽에서 구분되지 않는다.
    reading = _reading()
    # Act
    doc = m.convert_pollutant(reading, "pm25", target_res_deg=5.0)
    # Assert
    assert doc["nNegativeCellsDropped"] == 0
    assert doc["nAbovePhysicalMaxDropped"] == 0
    assert doc["physicalMaxUgm3"] == 1000.0


def test_build_points_drops_above_physical_max_and_counts():
    # Arrange — 2026-09-03 지구본 실사고 값. 발표된 QC 관행(PM2.5 >1,000 제외)
    # 위의 값은 음수와 같은 결측이다 (2026-09-05 사용자 확정, findings.md Q9).
    data = list(_DATA)
    data[5] = 12585.0
    # Act
    points, n_negative, n_above = m.build_points(
        data, nx2=4, ny2=3, la1=10.0, lo1=0.0, stride=1, dy=5.0, dx=5.0,
        physical_max_ugm3=1000.0,
    )
    # Assert — 0 으로 메우지 않고 빼되, 뺐다는 사실을 센다 (음수와 같은 규칙)
    assert len(points) == 11
    assert n_negative == 0
    assert n_above == 1


def test_build_points_without_cap_keeps_everything_and_reports_zero():
    # Arrange — 상한 미지정(None) 이면 안 거른다. "0건" 과 "안 걸렀다" 의 구분은
    # 발행물의 physicalMaxUgm3=null 공시가 담당한다.
    data = list(_DATA)
    data[5] = 12585.0
    # Act
    points, _, n_above = m.build_points(
        data, nx2=4, ny2=3, la1=10.0, lo1=0.0, stride=1, dy=5.0, dx=5.0,
    )
    # Assert
    assert len(points) == 12
    assert n_above == 0


def test_convert_pollutant_physical_max_differs_per_variable():
    # Arrange — 6,460 은 PM10 기록 극값(시간평균) 안이라 서빙되고, PM2.5 로는
    # 발표된 QC 상한(1,000) 위라 제외된다. 상수 하나를 돌려쓰면 PM10 을
    # PM2.5 자로 재게 된다 (feedback_shared_name_hides_different_quantity).
    reading = _reading()
    reading["pollutants"]["pm25"]["data"][2] = 6460.0
    reading["pollutants"]["pm10"]["data"][2] = 6460.0
    # Act
    pm25 = m.convert_pollutant(reading, "pm25", target_res_deg=5.0)
    pm10 = m.convert_pollutant(reading, "pm10", target_res_deg=5.0)
    # Assert
    assert pm25["physicalMaxUgm3"] == 1000.0
    assert pm25["nAbovePhysicalMaxDropped"] == 1
    assert len(pm25["points"]) == 11
    assert pm10["physicalMaxUgm3"] == 10000.0
    assert pm10["nAbovePhysicalMaxDropped"] == 0
    assert len(pm10["points"]) == 12
