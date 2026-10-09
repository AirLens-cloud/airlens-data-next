"""collect_gefs_timeline.py pure-helper 단위 테스트 (AAA). 네트워크/subprocess 제외."""
from datetime import datetime, timedelta, timezone

import collect_gefs_timeline as m

UTC = timezone.utc


def _cycles(latest: datetime, count: int):
    """latest 부터 6h 간격 과거로 count 개 cycle(desc)."""
    return [latest - timedelta(hours=6 * i) for i in range(count)]


def test_floor_to_step_floors_to_3h_grid():
    # Arrange
    dt = datetime(2026, 7, 6, 8, 47, 12, tzinfo=UTC)
    # Act
    out = m.floor_to_step(dt, 3)
    # Assert — 08:47 → 06:00 (3h 격자, 분/초 0)
    assert out == datetime(2026, 7, 6, 6, 0, 0, tzinfo=UTC)


def test_forecast_idx_match_analysis_vs_forecast():
    # Arrange/Act/Assert — lead 0 = anl, lead>0 = 'N hour fcst', species 동일
    assert m.forecast_idx_match(m.PM25_SPECIES, 0) == (
        "PMTF:surface:anl:aerosol=Total aerosol:aerosol_size <2.5e-06")
    assert m.forecast_idx_match(m.PM25_SPECIES, 24) == (
        "PMTF:surface:24 hour fcst:aerosol=Total aerosol:aerosol_size <2.5e-06")


def test_candidate_cycles_are_6h_spaced_desc_in_cycle_hours():
    # Arrange
    now = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
    # Act
    cands = m.candidate_cycles(now, back_h=48)
    # Assert — 최신 우선(desc), 전부 6h 격자·CYCLE_HOURS, 첫 cycle ≤ now
    assert cands == sorted(cands, reverse=True)
    assert all(c.hour in m.CYCLE_HOURS for c in cands)
    assert all((cands[i] - cands[i + 1]) == timedelta(hours=6)
               for i in range(len(cands) - 1))
    assert cands[0] <= now
    assert cands[0] == datetime(2026, 7, 6, 6, 0, tzinfo=UTC)  # floor to 06z


def test_plan_frames_full_window_17_frames_min_lead():
    # Arrange — now 08z, anchor 06z. 충분한 cycle(2일치) 가용
    now = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
    available = _cycles(datetime(2026, 7, 6, 6, 0, tzinfo=UTC), 10)  # 07-06 06z..2일전
    # Act
    frames = m.plan_frames(now, available)
    # Assert — ±24h/3h = 17 프레임, 각 최신 cycle(≤T) 선택 → lead 최소
    assert len(frames) == 17
    assert frames[0]["valid"] == datetime(2026, 7, 5, 6, 0, tzinfo=UTC)   # anchor-24h
    assert frames[-1]["valid"] == datetime(2026, 7, 7, 6, 0, tzinfo=UTC)  # anchor+24h
    # 마지막(미래) 프레임: 최신 cycle 07-06 06z → lead 24h
    assert frames[-1]["cycle"] == datetime(2026, 7, 6, 6, 0, tzinfo=UTC)
    assert frames[-1]["lead_h"] == 24
    # anchor 프레임(06z): cycle 06z lead 0(분석)
    anchor = next(f for f in frames if f["valid"] == datetime(2026, 7, 6, 6, 0, tzinfo=UTC))
    assert anchor["lead_h"] == 0
    # 전부 3h 배수 lead, ≤120
    assert all(f["lead_h"] % 3 == 0 and 0 <= f["lead_h"] <= 120 for f in frames)


def test_plan_frames_excludes_targets_with_no_prior_cycle():
    # Arrange — 오직 최신 cycle 1개(06z)만 가용 → 그보다 과거 target 은 매칭 불가
    now = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
    available = [datetime(2026, 7, 6, 6, 0, tzinfo=UTC)]
    # Act
    frames = m.plan_frames(now, available)
    # Assert — 06z..+24h = 9 프레임(과거 8개 제외), 전부 동일 cycle·lead=오프셋
    assert len(frames) == 9
    assert frames[0]["valid"] == datetime(2026, 7, 6, 6, 0, tzinfo=UTC)
    assert all(f["cycle"] == datetime(2026, 7, 6, 6, 0, tzinfo=UTC) for f in frames)
    assert [f["lead_h"] for f in frames] == [0, 3, 6, 9, 12, 15, 18, 21, 24]


def test_cycle_key_gefs_a2d_format():
    # Arrange/Act
    key = m.cycle_key(datetime(2026, 7, 6, 6, 0, tzinfo=UTC), 24)
    # Assert
    assert key == "gefs.20260706/06/chem/pgrb2ap25/gefs.chem.t06z.a2d_0p25.f024.grib2"


def test_frame_filename_url_safe_sortable():
    assert m.frame_filename(datetime(2026, 7, 6, 6, 0, tzinfo=UTC)) == "pm25-2026070606.json"


def test_build_frame_json_extends_base_contract():
    # Arrange
    points = [{"lat": -90.0, "lon": -180.0, "value": 12.3}]
    meta = {"nLat": 1, "nLon": 1, "latMin": -90.0, "lonMin": -180.0}
    # Act
    d = m.build_frame_json("pm2_5", points, meta, 2.0, 1783317600000, 24, "2026-07-06T06:00:00Z")
    # Assert — parseGridResponse 계약 필드 보존 + provenance 확장
    assert d["variable"] == "pm2_5" and d["source"] == "NOAA GEFS-Aerosols"
    assert d["resolution"] == 2.0 and d["dLat"] == 2.0
    assert d["timestamp"] == 1783317600000 and d["points"] == points
    assert d["leadHours"] == 24
    assert d["cycle"] == "2026-07-06T06:00:00Z"


def test_build_manifest_structure_and_reftime():
    # Arrange
    fm = [{"validTime": "2026-07-06T06:00:00Z", "leadHours": 0,
           "cycle": "2026-07-06T06:00:00Z", "file": "pm25-2026070606.json"}]
    # Act
    man = m.build_manifest(fm, 1783317600000, "2026-07-06T06:00:00Z")
    # Assert
    assert man["variable"] == "pm2_5" and man["source"] == "NOAA GEFS-Aerosols"
    assert man["refTime"] == "2026-07-06T06:00:00Z"
    assert man["stepHours"] == 3 and man["windowHours"] == 24
    assert man["frames"] == fm
    assert man["generatedAt"].endswith("Z")


def test_iso_z_trailing_z():
    assert m.iso_z(datetime(2026, 7, 6, 6, 0, tzinfo=UTC)) == "2026-07-06T06:00:00Z"


if __name__ == "__main__":
    # pytest 미설치 환경용 경량 드라이버.
    import sys
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  ERROR {fn.__name__}: {e}")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)


# ── None 결측 안전성 (2026-09-05 라이브 파손 회귀) ──────────────────────────────
# `build_grid_json` 이 물리 상한 초과 셀을 None 으로 떨구기 시작(#33)했는데 이
# 소비자만 갱신이 안 돼 `max()` 가 TypeError 로 죽었다. 파손은 프레임 루프 안이라
# manifest 를 못 쓰고 종료 → 발행물이 18h 정지. 아래 3케이스가 그 자리를 지킨다.

def _pts(values):
    """value 만 다른 최소 points 리스트."""
    return [{"lat": 0.0, "lon": float(i), "value": v} for i, v in enumerate(values)]


def test_finite_values_drops_none_cells():
    # Arrange — 상한 초과분이 None 으로 떨어진 격자
    points = _pts([12.5, None, 41.0, None])
    # Act
    vals = m.finite_values(points)
    # Assert — None 은 값이 아니라 결측. 0 으로 대체하지도 않는다.
    assert vals == [12.5, 41.0]


def test_frame_span_label_survives_mixed_none():
    # Arrange
    points = _pts([12.5, None, 41.0])
    # Act
    label = m.frame_span_label(points)
    # Assert — TypeError 없이 남은 유한값의 최대치
    assert label == "max=41.0 µg/m³"


def test_frame_span_label_reports_all_missing_honestly():
    # Arrange — 전 셀 결측
    points = _pts([None, None])
    # Act
    label = m.frame_span_label(points)
    # Assert — 0.0 같은 없는 수치를 지어내지 않는다
    assert label == "유한값 0개"
    assert m.finite_values(points) == []
