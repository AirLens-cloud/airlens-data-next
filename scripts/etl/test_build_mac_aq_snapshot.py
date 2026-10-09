"""build_mac_aq_snapshot.py 통합 테스트 (AAA). monkeypatch 로 INPUT/OUTPUT_PATH 를
tmp 경로에 고정 — `test_collect_cams_global.py`/`test_collect_gefs_chem_global.py` 와
동일 관례(형제 producer 의 last-good 보존 테스트 패턴)."""
import json
from datetime import datetime, timezone

import pytest

import build_mac_aq_snapshot as m
import mac_aq_adapter as adapter


def _grid_input(tmp_path):
    env = adapter.build_envelope(
        kind="analysis", source="CAMS", source_version="cycle-20260716T1200Z",
        generated_at="2026-07-16T15:00:00Z", observed_at=None,
        valid_at="2026-07-16T15:00:00Z", expires_at="2026-07-17T03:00:00Z",
        resolution_km=44, attribution="x", quality={"grade": "B", "score": 84},
    )
    header = {"nx": 2, "ny": 2, "lo1": -180.0, "la1": 90.0, "dx": 0.4, "dy": 0.4}
    pollutants = {
        "pm25": {"unit": "ug/m3", "sourceVariable": "pm2p5", "conversion": "x",
                 "data": [13.2345, -5.0, 3.001, 4.999]},
    }
    reading = adapter.build_grid_reading(env, header, pollutants)
    path = tmp_path / "cams-input.json"
    path.write_text(json.dumps(reading))
    return str(path)


def _point_list_input(tmp_path):
    env = adapter.build_envelope(
        kind="observation", source="AirKorea", source_version="run-1",
        generated_at="2026-07-16T15:00:00Z", observed_at="2026-07-16T15:00:00Z",
        valid_at="2026-07-16T15:00:00Z", expires_at="2026-07-16T16:00:00Z",
        resolution_km=1.0, attribution="x", quality={"grade": "A", "score": 100},
    )
    readings = [
        adapter.build_point_reading(env, lat=37.5, lon=127.0,
                                     pollutants={"pm25": adapter.pollutant_value(13.2345, "PM25Value", "direct")}),
        adapter.build_point_reading(env, lat=35.1, lon=129.0,
                                     pollutants={"pm25": adapter.pollutant_value(-9.0, "PM25Value", "direct")}),
    ]
    path = tmp_path / "airkorea-input.json"
    path.write_text(json.dumps(readings))
    return str(path)


# ────────────────────────── happy path — grid ──────────────────────────

def test_main_processes_grid_reading_end_to_end(tmp_path, monkeypatch):
    # Arrange
    input_path = _grid_input(tmp_path)
    output_path = tmp_path / "cams-output.json"
    monkeypatch.setattr(m, "INPUT_PATH", input_path)
    monkeypatch.setattr(m, "OUTPUT_PATH", str(output_path))
    # Act
    rc = m.main()
    # Assert
    assert rc == 0
    result = json.loads(output_path.read_text())
    assert adapter.validate_grid_reading(result) == []
    assert "provenance" in result
    assert result["provenance"]["qa"]["kind"] == "grid"
    # 이상치(음수) 1/4 존재 → quality 강등, 값은 변조 없이 quantize 만 적용
    assert result["quality"]["grade"] != "B" or result["quality"]["score"] < 84
    assert result["pollutants"]["pm25"]["data"] == [13.2, -5.0, 3.0, 5.0]


# ────────────────────────── happy path — point list ──────────────────────────

def test_main_processes_point_reading_list_end_to_end(tmp_path, monkeypatch):
    # Arrange
    input_path = _point_list_input(tmp_path)
    output_path = tmp_path / "airkorea-output.json"
    monkeypatch.setattr(m, "INPUT_PATH", input_path)
    monkeypatch.setattr(m, "OUTPUT_PATH", str(output_path))
    # Act
    rc = m.main()
    # Assert
    assert rc == 0
    result = json.loads(output_path.read_text())
    assert isinstance(result, list)
    assert len(result) == 2  # drop 없음
    for record in result:
        assert adapter.validate_point_reading(record) == []
        assert "provenance" in record
        assert "qaFlags" in record
    assert result[1]["qaFlags"]["valueAnomalies"]  # 음수값 flag 남음


# ────────────────────────── fail-loud + last-good 보존 ──────────────────────────

def test_main_preserves_last_good_on_malformed_input_json(tmp_path, monkeypatch):
    # Arrange — 입력 파일이 유효한 JSON 이 아님
    bad_input = tmp_path / "broken.json"
    bad_input.write_text("{not valid json")
    existing_output = tmp_path / "output.json"
    existing_output.write_text('{"last": "good"}')
    monkeypatch.setattr(m, "INPUT_PATH", str(bad_input))
    monkeypatch.setattr(m, "OUTPUT_PATH", str(existing_output))
    # Act
    rc = m.main()
    # Assert — 실패 시 exit 1, 기존 output 그대로
    assert rc == 1
    assert json.loads(existing_output.read_text()) == {"last": "good"}


def test_main_preserves_last_good_when_input_missing(tmp_path, monkeypatch):
    # Arrange
    missing_input = tmp_path / "does-not-exist.json"
    existing_output = tmp_path / "output.json"
    existing_output.write_text('{"last": "good"}')
    monkeypatch.setattr(m, "INPUT_PATH", str(missing_input))
    monkeypatch.setattr(m, "OUTPUT_PATH", str(existing_output))
    # Act
    rc = m.main()
    # Assert
    assert rc == 1
    assert json.loads(existing_output.read_text()) == {"last": "good"}


def test_main_requires_both_env_paths(monkeypatch):
    # Arrange
    monkeypatch.setattr(m, "INPUT_PATH", "")
    monkeypatch.setattr(m, "OUTPUT_PATH", "")
    # Act
    rc = m.main()
    # Assert
    assert rc == 1


# ────────────────────────── process() shape dispatch ──────────────────────────

def test_process_dispatches_single_point_dict_without_list_wrapper():
    # Arrange
    env = adapter.build_envelope(
        kind="observation", source="AirKorea", source_version="run-1",
        generated_at="2026-07-16T15:00:00Z", observed_at="2026-07-16T15:00:00Z",
        valid_at="2026-07-16T15:00:00Z", expires_at="2026-07-16T16:00:00Z",
        resolution_km=1.0, attribution="x", quality={"grade": "A", "score": 100},
    )
    reading = adapter.build_point_reading(
        env, lat=37.5, lon=127.0,
        pollutants={"pm25": adapter.pollutant_value(13.2345, "PM25Value", "direct")},
    )
    now = datetime(2026, 7, 16, 15, 30, tzinfo=timezone.utc)
    # Act
    result, summary = m.process(reading, now)
    # Assert — 입력이 dict 였으니 출력도 dict(list 로 안 감쌈)
    assert isinstance(result, dict)
    assert adapter.validate_point_reading(result) == []
    assert summary["qa"]["kind"] == "point"


# ────────────────────────── publisher-side validAt<=generatedAt invariant (PR7) ──────────────────────────

def _point_reading(*, generated_at: str, valid_at: str, source_version: str, lat: float = 52.52, lon: float = 13.405) -> dict:
    env = adapter.build_envelope(
        kind="observation", source="EEA-UTD", source_version=source_version,
        generated_at=generated_at, observed_at=valid_at,
        valid_at=valid_at, expires_at="2026-07-25T09:35:00Z",
        resolution_km=1.0, attribution="x", quality={"grade": "A", "score": 100},
    )
    return adapter.build_point_reading(
        env, lat=lat, lon=lon,
        pollutants={"pm25": adapter.pollutant_value(13.2, "PM25Value", "direct")},
    )


def test_is_valid_at_future_true_beyond_tolerance():
    # Arrange — validAt 이 generatedAt 보다 1시간 미래
    record = {"validAt": "2026-07-25T08:35:00Z", "generatedAt": "2026-07-25T07:35:00Z"}
    # Act / Assert
    assert m.is_valid_at_future(record) is True


def test_is_valid_at_future_false_within_clock_skew_tolerance():
    # Arrange — 5분 이내 시계 오차는 허용(기본 tolerance)
    record = {"validAt": "2026-07-25T07:38:00Z", "generatedAt": "2026-07-25T07:35:00Z"}
    # Act / Assert
    assert m.is_valid_at_future(record) is False


def test_is_valid_at_future_false_when_valid_at_in_past():
    # Arrange
    record = {"validAt": "2026-07-25T06:00:00Z", "generatedAt": "2026-07-25T07:35:00Z"}
    # Act / Assert
    assert m.is_valid_at_future(record) is False


def test_is_valid_at_future_false_when_fields_missing():
    # Arrange — 스키마 결손은 adapter.validate_* 가 별도로 잡는다, 여기선 판단 보류
    assert m.is_valid_at_future({}) is False
    assert m.is_valid_at_future({"validAt": "2026-07-25T08:35:00Z"}) is False


def test_process_point_reading_list_drops_future_valid_at_keeps_others():
    # Arrange — 관측소 1(정상) + 관측소 2(구버전 EEA 버그 재현: validAt 이 generatedAt 보다
    # 미래) 혼합. QA 는 F 등급만 매기고 발행을 막지 않지만(팀 정책), 이 게이트는 실제로
    # 드롭해야 한다 — silent-ship 금지가 PR7 의 목적.
    good = _point_reading(
        generated_at="2026-07-25T07:35:00Z", valid_at="2026-07-25T07:30:00Z",
        source_version="E2a-UTD-DE0001A", lat=52.52, lon=13.405,
    )
    future = _point_reading(
        generated_at="2026-07-25T07:35:00Z", valid_at="2026-07-26T00:00:00Z",
        source_version="E2a-UTD-DE0002A", lat=48.8566, lon=2.3522,
    )
    now = datetime(2026, 7, 25, 7, 40, tzinfo=timezone.utc)
    # Act
    final_records, summary = m.process_point_reading_list([good, future], now)
    # Assert — future 만 드롭, good 은 그대로 발행
    assert len(final_records) == 1
    assert final_records[0]["sourceVersion"] == "E2a-UTD-DE0001A"
    assert summary["qa"]["droppedFutureValidAtCount"] == 1


def test_process_point_reading_list_raises_when_all_readings_have_future_valid_at():
    # Arrange — 전량이 미래 validAt → 발행할 게 없다. 빈 배열로 last-good 을 덮어쓰지
    # 않고 다른 파이프라인 실패와 동일하게 raise(main() 이 exit 1 로 변환, last-good 유지).
    future = _point_reading(
        generated_at="2026-07-25T07:35:00Z", valid_at="2026-07-26T00:00:00Z",
        source_version="E2a-UTD-DE0002A",
    )
    now = datetime(2026, 7, 25, 7, 40, tzinfo=timezone.utc)
    # Act / Assert
    with pytest.raises(ValueError, match="validAt after generatedAt"):
        m.process_point_reading_list([future], now)


def test_process_grid_reading_raises_when_valid_at_future():
    # Arrange — grid_reading 도 동일 게이트 적용
    env = adapter.build_envelope(
        kind="analysis", source="CAMS", source_version="cycle-1",
        generated_at="2026-07-25T07:35:00Z", observed_at=None,
        valid_at="2026-07-26T00:00:00Z", expires_at="2026-07-26T12:00:00Z",
        resolution_km=44, attribution="x", quality={"grade": "A", "score": 100},
    )
    header = {"nx": 1, "ny": 1, "lo1": 0.0, "la1": 0.0, "dx": 1.0, "dy": 1.0}
    pollutants = {"pm25": {"unit": "ug/m3", "sourceVariable": "pm2p5", "conversion": "x", "data": [10.0]}}
    reading = adapter.build_grid_reading(env, header, pollutants)
    now = datetime(2026, 7, 25, 7, 40, tzinfo=timezone.utc)
    # Act / Assert
    with pytest.raises(ValueError, match="grid reading dropped"):
        m.process_grid_reading(reading, now)


def test_main_drops_future_valid_at_record_end_to_end(tmp_path, monkeypatch):
    # Arrange — main() 를 통한 전체 경로(입력 파일 -> 출력 파일)에서도 드롭이 적용되는지
    good = _point_reading(
        generated_at="2026-07-25T07:35:00Z", valid_at="2026-07-25T07:30:00Z",
        source_version="E2a-UTD-DE0001A", lat=52.52, lon=13.405,
    )
    future = _point_reading(
        generated_at="2026-07-25T07:35:00Z", valid_at="2026-07-26T00:00:00Z",
        source_version="E2a-UTD-DE0002A", lat=48.8566, lon=2.3522,
    )
    input_path = tmp_path / "eea-utd-input.json"
    input_path.write_text(json.dumps([good, future]))
    output_path = tmp_path / "eea-utd-output.json"
    monkeypatch.setattr(m, "INPUT_PATH", str(input_path))
    monkeypatch.setattr(m, "OUTPUT_PATH", str(output_path))
    # Act
    rc = m.main()
    # Assert
    assert rc == 0
    result = json.loads(output_path.read_text())
    assert len(result) == 1
    assert result[0]["sourceVersion"] == "E2a-UTD-DE0001A"
    assert result[0]["validAt"] <= result[0]["generatedAt"]
