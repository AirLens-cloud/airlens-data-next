"""merge_point_readings.py 단위 테스트 (AAA). 네트워크 없음.

순수 함수(`merge`/`reading_key`/`restamp_freshness`)가 주 대상이고, `main()` 은
tmp_path 안에서만 파일을 읽고 쓴다 — collector 가 아무것도 못 낸 경우와 baseline 이
읽히지 않는 경우의 분기가 실제 발행 안전성을 좌우하는 지점이라 함께 고정한다.
"""
from datetime import datetime, timedelta, timezone

import merge_point_readings as m

NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


def _reading(code: str, *, observed_hours_ago: float, expires_hours_ahead: float,
             lat: float = 40.0, lon: float = -3.0, now: datetime = NOW) -> dict:
    """`now` 는 이 레코드의 시간축 원점. 순수 함수 테스트는 고정된 NOW 를 쓰지만
    `main()` 은 진짜 현재 시각으로 만료를 판정하므로 그쪽 fixture 는 실시각을 넘겨야
    한다 — 안 그러면 NOW 가 과거로 밀리는 다음 날 테스트가 통째로 만료 판정을 받는다.
    """
    observed = now - timedelta(hours=observed_hours_ago)
    return {
        "sourceVersion": f"E2a-UTD-{code}",
        "lat": lat, "lon": lon,
        "validAt": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observedAt": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expiresAt": (now + timedelta(hours=expires_hours_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quality": {"grade": "A", "score": 100},
        "qaFlags": {"expired": False, "ageHours": 0.0},
        "pollutants": {"pm25": {"value": 7.0}},
    }


# ────────────────────────── 관측소 동일성 키 ──────────────────────────

def test_reading_key_prefers_source_version():
    # Arrange — 좌표가 함께 있어도 sourceVersion 이 이긴다
    station = _reading("ES0001A", observed_hours_ago=1.0, expires_hours_ahead=3.0)
    # Act
    key = m.reading_key(station)
    # Assert
    assert key == station["sourceVersion"]
    assert not key.startswith("@")


def test_reading_key_falls_back_to_rounded_coordinates():
    # Arrange / Act
    key = m.reading_key({"lat": 40.123456, "lon": -3.987654})
    # Assert — 4자리 반올림(QA 의 중복좌표 판정과 같은 정밀도)
    assert key == "@40.1235,-3.9877"


def test_reading_key_none_when_identity_unknowable():
    # Arrange / Act / Assert — 동일성을 주장할 근거가 없으면 이어붙이지 않는다
    assert m.reading_key({"quality": {"grade": "A"}}) is None


# ────────────────────────── 합집합 규칙 ──────────────────────────

def test_merge_carries_over_station_absent_from_this_run():
    # Arrange — 이번 run 은 DE 만, 직전 발행분엔 ES 도 있었다(회전으로 이번엔 안 돎)
    fresh = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    baseline = [_reading("ES0002A", observed_hours_ago=2.0, expires_hours_ahead=2.0)]
    # Act
    merged, report = m.merge(fresh, baseline, NOW)
    # Assert
    assert [r["sourceVersion"] for r in merged] == ["E2a-UTD-DE0001A", "E2a-UTD-ES0002A"]
    assert report["carriedOverCount"] == 1


def test_merge_prefers_fresh_over_baseline_for_same_station():
    # Arrange — 같은 관측소가 양쪽에 있으면 이번 run 값이 이긴다
    fresh = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    baseline = [_reading("DE0001A", observed_hours_ago=3.0, expires_hours_ahead=1.0)]
    # Act
    merged, report = m.merge(fresh, baseline, NOW)
    # Assert
    assert len(merged) == 1
    assert merged[0]["qaFlags"].get("carriedOver") is not True
    assert report["carriedOverCount"] == 0


def test_merge_drops_expired_baseline_station():
    # Arrange — 만료된 관측값은 조용히 되살리지 않는다
    fresh = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    baseline = [_reading("ES0002A", observed_hours_ago=5.0, expires_hours_ahead=-1.0)]
    # Act
    merged, report = m.merge(fresh, baseline, NOW)
    # Assert
    assert len(merged) == 1
    assert report["droppedExpired"] == 1


def test_merge_drops_baseline_older_than_quality_recompute_free_window():
    # Arrange — 만료 전이지만 6h 초과: quality 등급이 낙관적으로 남을 수 있는 구간
    fresh = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    baseline = [_reading("ES0002A", observed_hours_ago=7.0, expires_hours_ahead=1.0)]
    # Act
    merged, report = m.merge(fresh, baseline, NOW)
    # Assert
    assert len(merged) == 1
    assert report["droppedTooOld"] == 1


def test_merge_drops_baseline_with_unknown_age():
    # Arrange — validAt 부재 = 나이 미상. 모르는 나이를 신선으로 간주하지 않는다
    stale = _reading("ES0002A", observed_hours_ago=1.0, expires_hours_ahead=3.0)
    del stale["validAt"]
    # Act
    merged, report = m.merge([_reading("DE0001A", observed_hours_ago=0.5,
                                       expires_hours_ahead=3.5)], [stale], NOW)
    # Assert
    assert len(merged) == 1
    assert report["droppedTooOld"] == 1


def test_merge_drops_baseline_without_identity():
    # Arrange
    unkeyed = {"quality": {"grade": "A"}, "validAt": "2026-07-27T11:00:00Z"}
    # Act
    merged, report = m.merge([_reading("DE0001A", observed_hours_ago=0.5,
                                       expires_hours_ahead=3.5)], [unkeyed], NOW)
    # Assert
    assert len(merged) == 1
    assert report["droppedUnkeyed"] == 1


# ────────────────────────── 이어붙인 레코드의 정직성 ──────────────────────────

def test_carried_over_record_is_restamped_with_current_age():
    # Arrange — 직전 run 이 남긴 ageHours 0.0 이 그대로면 낡은 값이 신선한 척한다
    baseline = [_reading("ES0002A", observed_hours_ago=2.5, expires_hours_ahead=1.5)]
    # Act
    merged, _ = m.merge([], baseline, NOW)
    # Assert
    assert merged[0]["qaFlags"]["ageHours"] == 2.5
    assert merged[0]["qaFlags"]["expired"] is False
    assert merged[0]["qaFlags"]["carriedOver"] is True


def test_carried_over_record_keeps_original_values_and_timestamps():
    # Arrange — 이어붙이기는 재추정이 아니다. 관측값·관측시각은 그대로여야 한다
    baseline = [_reading("ES0002A", observed_hours_ago=2.0, expires_hours_ahead=2.0)]
    original_observed = baseline[0]["observedAt"]
    # Act
    merged, _ = m.merge([], baseline, NOW)
    # Assert
    assert merged[0]["observedAt"] == original_observed
    assert merged[0]["pollutants"] == {"pm25": {"value": 7.0}}
    assert merged[0]["quality"] == {"grade": "A", "score": 100}


def test_carried_over_record_sheds_other_stations_provenance():
    """과거 발행분의 N² provenance 를 그대로 재발행하지 않는지.

    회귀 대상 = 2026-07-30. 생성 쪽을 고친 뒤에도 carry-over 가 옛 레코드(레코드당
    482KB, sources 1,453건)를 만료 전까지 계속 실어 나르며 발행분을 223,969,909 B 로
    유지했다.
    """
    # Arrange — 이웃 관측소들의 출처가 섞여 든 옛 형식 레코드
    stale = _reading("FR02028", observed_hours_ago=2.0, expires_hours_ahead=2.0)
    stale["source"] = "EEA-UTD"
    stale["provenance"] = {
        "pipelineVersion": "mac-p1-qa-1",
        "sources": [
            {"source": "EEA-UTD", "sourceVersion": f"E2a-UTD-FR{i:05d}"} for i in range(1453)
        ],
    }
    # Act
    merged, _ = m.merge([], [stale], NOW)
    # Assert — 자기 자신 1건만 남고, 그 1건이 실제로 자기 sourceVersion 이다
    prov = merged[0]["provenance"]
    assert len(prov["sources"]) == 1
    assert prov["sources"][0]["sourceVersion"] == "E2a-UTD-FR02028"
    assert prov["pipelineVersion"] == "mac-p1-qa-1"  # 나머지 필드는 손대지 않는다


def test_carried_over_provenance_is_rebuilt_not_taken_from_first_entry():
    """`sources[0]` 재사용 금지 — 공유 목록의 첫 항목은 남의 관측소다."""
    # Arrange — 첫 항목이 이 레코드가 아닌 옛 형식
    stale = _reading("FR34026", observed_hours_ago=1.0, expires_hours_ahead=3.0)
    stale["source"] = "EEA-UTD"
    stale["provenance"] = {"sources": [
        {"source": "EEA-UTD", "sourceVersion": "E2a-UTD-FR12021"},
        {"source": "EEA-UTD", "sourceVersion": "E2a-UTD-FR34026"},
    ]}
    # Act
    merged, _ = m.merge([], [stale], NOW)
    # Assert
    assert merged[0]["provenance"]["sources"][0]["sourceVersion"] == "E2a-UTD-FR34026"


def test_carried_over_record_with_single_source_provenance_is_untouched():
    """이미 정상인 레코드는 건드리지 않는다(무의미한 재작성 회피)."""
    # Arrange
    ok = _reading("IT0001", observed_hours_ago=1.0, expires_hours_ahead=3.0)
    original = {"sources": [{"source": "EEA-UTD", "sourceVersion": "E2a-UTD-IT0001"}], "extra": 1}
    ok["provenance"] = original
    # Act
    merged, _ = m.merge([], [ok], NOW)
    # Assert
    assert merged[0]["provenance"] == original


def test_carried_over_record_without_provenance_survives():
    """provenance 부재·비정상 타입이 carry-over 를 죽이지 않는다."""
    # Arrange
    no_prov = _reading("PL0001", observed_hours_ago=1.0, expires_hours_ahead=3.0)
    bad_prov = _reading("NL0001", observed_hours_ago=1.0, expires_hours_ahead=3.0)
    bad_prov["provenance"] = "not-a-dict"
    # Act
    merged, _ = m.merge([], [no_prov, bad_prov], NOW)
    # Assert
    assert len(merged) == 2
    assert "provenance" not in merged[0]
    assert merged[1]["provenance"] == "not-a-dict"


def test_merge_with_empty_baseline_is_identity():
    # Arrange
    fresh = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    # Act
    merged, report = m.merge(fresh, [], NOW)
    # Assert
    assert merged == fresh
    assert report["carriedOverCount"] == 0


# ────────────────────────── 장애 격리 ──────────────────────────

def test_merge_skips_malformed_baseline_record_without_dying():
    # Arrange — baseline 은 라이브 사이트에서 받아온 과거 발행물이라 스키마 보장이 없다.
    # 깨진 레코드 하나가 발행 전체를 죽이면 blast radius 가 원래 문제보다 넓어진다.
    fresh = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    baseline = [
        {"sourceVersion": "E2a-UTD-XX0001A", "validAt": "not-a-timestamp"},
        _reading("ES0003A", observed_hours_ago=1.0, expires_hours_ahead=3.0),
    ]
    # Act
    merged, report = m.merge(fresh, baseline, NOW)
    # Assert — 깨진 것만 버리고 멀쩡한 ES 는 이어붙인다
    assert [r["sourceVersion"] for r in merged] == ["E2a-UTD-DE0001A", "E2a-UTD-ES0003A"]
    assert report["droppedMalformed"] == 1


def test_merge_skips_non_dict_baseline_element():
    # Arrange
    fresh = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    # Act
    merged, report = m.merge(fresh, ["garbage", None], NOW)
    # Assert
    assert merged == fresh
    assert report["droppedMalformed"] == 2


# ────────────────── quality freeze 전제의 회귀 방어 ──────────────────

def test_carry_over_window_matches_the_flat_quality_band():
    # Arrange — "이어붙인 레코드의 quality 를 재계산하지 않아도 된다"는 전제는 전적으로
    # 이 두 값이 같다는 데 의존한다. 어댑터 밴드가 좁아지면 여기서 먼저 깨져야 한다.
    import mac_aq_adapter as adapter
    # Act / Assert
    assert m.MAX_CARRY_OVER_AGE_HOURS == float(adapter.FRESH_QUALITY_BAND_HOURS)


def test_quality_is_age_independent_inside_the_carry_over_window():
    # Arrange
    import mac_aq_adapter as adapter
    # Act — 창 안에서는 나이가 달라도 등급·점수가 같아야 한다(그래서 freeze 가 안전)
    at_zero = adapter.estimate_quality(pollutant_count=6, expected_count=6, age_hours=0.0)
    at_limit = adapter.estimate_quality(pollutant_count=6, expected_count=6,
                                        age_hours=m.MAX_CARRY_OVER_AGE_HOURS)
    just_past = adapter.estimate_quality(pollutant_count=6, expected_count=6,
                                         age_hours=m.MAX_CARRY_OVER_AGE_HOURS + 0.01)
    # Assert
    assert at_zero == at_limit
    assert just_past["score"] < at_limit["score"]  # 창 밖은 실제로 감점된다


# ────────────────────────── main() 파일 I/O ──────────────────────────

def _run_main(monkeypatch, *, fresh_path="", baseline_path="", output_path=""):
    monkeypatch.setattr(m, "FRESH_PATH", str(fresh_path))
    monkeypatch.setattr(m, "BASELINE_PATH", str(baseline_path))
    monkeypatch.setattr(m, "OUTPUT_PATH", str(output_path or fresh_path))
    return m.main()


def test_main_is_noop_when_this_run_produced_nothing(tmp_path, monkeypatch):
    # Arrange — collector 실패. baseline 을 "새 발행분"으로 승격시키면 낡은 스냅샷에
    # 새 발행 시각이 찍힌다. 발행 단계의 파일 단위 last-good 폴백에 맡겨야 한다.
    import json
    baseline = tmp_path / "base.json"
    baseline.write_text(json.dumps([_reading("ES0003A", observed_hours_ago=1.0,
                                             expires_hours_ahead=3.0)]))
    fresh = tmp_path / "fresh.json"
    # Act
    exit_code = _run_main(monkeypatch, fresh_path=fresh, baseline_path=baseline)
    # Assert
    assert exit_code == 0
    assert not fresh.exists()


def test_main_passes_through_when_no_baseline_exists(tmp_path, monkeypatch):
    # Arrange — 최초 발행
    import json
    fresh = tmp_path / "fresh.json"
    records = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    fresh.write_text(json.dumps(records))
    # Act
    exit_code = _run_main(monkeypatch, fresh_path=fresh, baseline_path=tmp_path / "absent.json")
    # Assert
    assert exit_code == 0
    assert json.loads(fresh.read_text()) == records


def test_main_writes_merged_output(tmp_path, monkeypatch):
    # Arrange — main() 은 실시각으로 만료를 판정하므로 fixture 도 실시각 기준이어야
    # baseline 이 살아남는다(고정 NOW 로 만들면 다음 날부터 만료로 탈락한다)
    import json
    real_now = datetime.now(timezone.utc)
    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps([_reading("DE0001A", observed_hours_ago=0.5,
                                          expires_hours_ahead=3.5, now=real_now)]))
    baseline = tmp_path / "base.json"
    baseline.write_text(json.dumps([_reading("ES0003A", observed_hours_ago=1.0,
                                             expires_hours_ahead=3.0, now=real_now)]))
    out = tmp_path / "out.json"
    # Act
    exit_code = _run_main(monkeypatch, fresh_path=fresh, baseline_path=baseline, output_path=out)
    # Assert
    assert exit_code == 0
    assert [r["sourceVersion"] for r in json.loads(out.read_text())] == [
        "E2a-UTD-DE0001A", "E2a-UTD-ES0003A"]
    assert not (tmp_path / "out.json.tmp").exists()  # atomic replace 흔적 없음


def test_main_treats_unreadable_baseline_as_absent(tmp_path, monkeypatch):
    # Arrange — 잘린 JSON. 발행을 죽이지 않고 fresh 만 통과시켜야 한다
    import json
    fresh = tmp_path / "fresh.json"
    records = [_reading("DE0001A", observed_hours_ago=0.5, expires_hours_ahead=3.5)]
    fresh.write_text(json.dumps(records))
    baseline = tmp_path / "base.json"
    baseline.write_text('[{"sourceVersion": "E2a-UTD-ES0003A"')
    # Act
    exit_code = _run_main(monkeypatch, fresh_path=fresh, baseline_path=baseline)
    # Assert
    assert exit_code == 0
    assert json.loads(fresh.read_text()) == records
