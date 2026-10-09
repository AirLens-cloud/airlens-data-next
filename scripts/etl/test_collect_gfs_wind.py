"""collect_gfs_wind.py pure-helper 단위 테스트 (AAA). 네트워크/subprocess 제외.

핵심 회귀: GFS native 경도는 0..359 인데 우리 JSON 계약은 lo1=-180.
build_dense 가 이 roll 을 하지 않으면 지구본이 정확히 180° 어긋난다.
"""
from datetime import datetime, timezone

import collect_gfs_wind as m

UTC = timezone.utc


def test_gfs_key_uses_cycle_date_and_hour():
    # Arrange / Act
    key = m.gfs_key("20260713", "06")
    # Assert
    assert key == "gfs.20260713/06/atmos/gfs.t06z.pgrb2.1p00.f000"


def test_idx_match_targets_analysis_record():
    # Arrange / Act
    match = m.idx_match("UGRD", "850 mb")
    # Assert — f000 세그먼트는 anl
    assert match == "UGRD:850 mb:anl:"


def test_build_dense_rolls_gfs_0_359_longitude_to_minus180_origin():
    # Arrange — GFS convention: lon 0..359. 값을 경도로 두면 roll 여부가 그대로 보인다.
    rows = []
    for lat in (90.0, 0.0, -90.0):
        for lon in range(0, 360):
            rows.append((lat, float(lon), float(lon)))

    # Act — 1° 격자를 만들되 ny=3 (lat 90/0/-90) 만 검사하도록 축소 격자 사용
    dense = m.build_dense(rows, nx=360, ny=3, la1=90.0, lo1=-180.0, dx=1.0, dy=90.0)

    # Assert — 열 0 = lon -180 → GFS lon 180 의 값. 열 180 = lon 0 → GFS lon 0.
    assert dense[0] == 180.0          # 행0(북), 열0(-180°)
    assert dense[180] == 0.0          # 행0, 열180 (0°)
    assert dense[359] == 179.0        # 행0, 열359 (+179°)
    # 행1(적도)도 동일 roll
    assert dense[360 + 0] == 180.0
    assert dense[360 + 180] == 0.0


def test_build_dense_row0_is_north():
    # Arrange — 위도로 값을 채워 행 순서를 드러낸다.
    rows = [(lat, float(lon), lat) for lat in (90.0, 0.0, -90.0) for lon in range(0, 360)]

    # Act
    dense = m.build_dense(rows, nx=360, ny=3, la1=90.0, lo1=-180.0, dx=1.0, dy=90.0)

    # Assert — 행0 = la1 = 북단(90), 마지막 행 = 남단(-90)
    assert dense[0] == 90.0
    assert dense[2 * 360] == -90.0


def test_build_dense_raises_when_grid_incomplete():
    # Arrange — 한 셀 누락 (0 으로 조용히 메우면 안 됨)
    rows = [(90.0, float(lon), 1.0) for lon in range(0, 359)]

    # Act / Assert
    try:
        m.build_dense(rows, nx=360, ny=1, la1=90.0, lo1=-180.0, dx=1.0, dy=1.0)
    except ValueError as e:
        assert "incomplete" in str(e).lower()
    else:
        raise AssertionError("불완전 격자를 통과시켰다 — 0 으로 메우면 안 됨")


def test_downsample_keeps_every_other_cell():
    # Arrange — 4×2 격자 (nx=4, ny=2), 값 = 인덱스
    dense = [0.0, 1.0, 2.0, 3.0, 10.0, 11.0, 12.0, 13.0]

    # Act — stride 2
    out, nx2, ny2 = m.downsample(dense, nx=4, ny=2, stride=2)

    # Assert — 열 0,2 / 행 0 만 남음
    assert (nx2, ny2) == (2, 1)
    assert out == [0.0, 2.0]


def test_cycle_candidates_walks_back_from_newest():
    # Arrange — 07:30Z 시점: 06z 는 나왔지만 12z/18z 는 미래
    now = datetime(2026, 7, 13, 7, 30, tzinfo=UTC)

    # Act
    cands = list(m.cycle_candidates(now))

    # Assert — 오늘 06 → 오늘 00 → 어제 18 ... 순 (최신 우선, 미래 제외)
    assert cands[0] == ("20260713", "06")
    assert cands[1] == ("20260713", "00")
    assert cands[2] == ("20260712", "18")
    assert ("20260713", "12") not in cands
    assert ("20260713", "18") not in cands
