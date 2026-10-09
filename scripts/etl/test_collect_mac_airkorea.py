"""collect_mac_airkorea.py 단위 테스트 (AAA). 네트워크 없음 — 순수 함수만 대상."""
import pytest

import collect_mac_airkorea as m


# ────────────────────────── ppm <-> ug/m3 변환 ──────────────────────────

def test_ppm_to_ugm3_ozone_matches_known_conversion():
    # Arrange — 공인 환산값: 1 ppm O3(MW=48) ≈ 1962-1964 µg/m3 (25C/1atm)
    # Act
    result = m.ppm_to_ugm3(1.0, 48.00)
    # Assert
    assert result == pytest.approx(1963.0, rel=1e-2)


def test_ppm_to_ugm3_rejects_nonpositive_inputs():
    with pytest.raises(ValueError):
        m.ppm_to_ugm3(1.0, 0.0)
    with pytest.raises(ValueError):
        m.ppm_to_ugm3(1.0, 48.0, molar_volume_l_mol=0.0)


# ────────────────────────── 값 파싱 ──────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("12.3", 12.3),
    ("-", None),
    ("", None),
    (None, None),
    ("null", None),
    ("abc", None),
])
def test_parse_airkorea_value(raw, expected):
    # Arrange / Act
    result = m.parse_airkorea_value(raw)
    # Assert
    assert result == expected


# ────────────────────────── lat/lon 축 판별 (resolve_lat_lon) ──────────────────────────

def test_resolve_lat_lon_accepts_seoul_regardless_of_arg_order():
    # Arrange / Act — 문서 필드명(dmX/dmY) 순서와 무관하게 범위로 판별
    result_a = m.resolve_lat_lon(37.5665, 126.9780)  # a=lat, b=lon
    result_b = m.resolve_lat_lon(126.9780, 37.5665)  # a=lon, b=lat (뒤바뀐 경우)
    # Assert
    assert result_a == (37.5665, 126.9780)
    assert result_b == (37.5665, 126.9780)


@pytest.mark.parametrize("a,b", [(51.5, -0.1), (0.0, 0.0), (90.0, 180.0)])
def test_resolve_lat_lon_rejects_out_of_range(a, b):
    assert m.resolve_lat_lon(a, b) is None


def test_resolve_lat_lon_none_when_either_value_missing():
    assert m.resolve_lat_lon(None, 126.9780) is None
    assert m.resolve_lat_lon(37.5665, None) is None


# ────────────────────────── 시도명 정규화 (resolve_sido_from_addr) ──────────────────────────

@pytest.mark.parametrize("addr,expected", [
    ("서울특별시 종로구 종로 1", "서울"),
    ("서울 종로구", "서울"),
    ("경기도 수원시", "경기"),
    ("강원특별자치도 춘천시", "강원"),
    (None, None),
    ("", None),
    ("Atlantis 어딘가", None),
])
def test_resolve_sido_from_addr(addr, expected):
    assert m.resolve_sido_from_addr(addr) == expected


# ────────────────────────── station list 파싱 (복합키) ──────────────────────────

def test_parse_station_list_response_extracts_lat_lon_with_composite_key():
    # Arrange — dmX/dmY 순서 무관 + addr 로 시도 파생
    data = {
        "response": {"body": {"items": [
            {"stationName": "종로구", "addr": "서울특별시 종로구 종로 1",
             "dmX": "126.9822", "dmY": "37.5720"},
        ]}}
    }
    # Act
    coords = m.parse_station_list_response(data)
    # Assert
    assert coords == {"서울|종로구": (37.572, 126.9822)}


def test_parse_station_list_response_skips_out_of_range_and_invalid():
    # Arrange — 하나는 범위 밖(런던), 하나는 파싱 불가, 하나는 이름 없음
    data = {
        "response": {"body": {"items": [
            {"stationName": "London", "addr": "서울특별시 종로구", "dmX": "-0.1", "dmY": "51.5"},
            {"stationName": "Bad", "addr": "서울특별시 종로구", "dmX": "abc", "dmY": "37.5"},
            {"stationName": "NoName", "addr": "서울특별시 종로구", "dmX": "127.0", "dmY": ""},
        ]}}
    }
    # Act
    coords = m.parse_station_list_response(data)
    # Assert
    assert coords == {}


def test_parse_station_list_response_skips_when_sido_unresolvable():
    # Arrange — addr 미해석(별칭 표 밖) 항목은 좌표가 유효해도 skip
    data = {
        "response": {"body": {"items": [
            {"stationName": "종로구", "addr": "Atlantis 어딘가", "dmX": "126.9822", "dmY": "37.5720"},
        ]}}
    }
    # Act
    coords = m.parse_station_list_response(data)
    # Assert
    assert coords == {}


def test_parse_station_list_response_disambiguates_same_name_across_provinces():
    # Arrange — '중구' 는 서울/부산 둘 다 실재(리뷰 지적 사례). bare name 조회였다면
    # 나중 항목(부산 중구)이 먼저 항목(서울 중구)의 좌표를 덮어써야 정상 동작하지만,
    # 실제 결함은 반대 방향(같은 이름이 다른 시도의 좌표를 잘못 받음)이었다 —
    # 복합키라면 둘 다 각자의 정확한 좌표를 유지해야 한다.
    data = {
        "response": {"body": {"items": [
            {"stationName": "중구", "addr": "서울특별시 중구 다산로", "dmX": "126.9975", "dmY": "37.5641"},
            {"stationName": "중구", "addr": "부산광역시 중구 중앙대로", "dmX": "129.0306", "dmY": "35.1073"},
        ]}}
    }
    # Act
    coords = m.parse_station_list_response(data)
    # Assert — 두 항목 모두 보존되고, 서로의 좌표를 침범하지 않는다
    assert coords == {
        "서울|중구": (37.5641, 126.9975),
        "부산|중구": (35.1073, 129.0306),
    }


# ────────────────────────── 시각 변환 ──────────────────────────

def test_parse_datetime_kst_to_iso_utc():
    # Arrange / Act — KST 15:00 = UTC 06:00
    result = m.parse_datetime_kst_to_iso_utc("2026-07-23 15:00")
    # Assert
    assert result == "2026-07-23T06:00:00Z"


# ────────────────────────── pollutants 조립 ──────────────────────────

def test_build_pollutants_for_item_converts_gases_and_keeps_pm_direct():
    # Arrange
    item = {"pm25Value": "13.2", "pm10Value": "22.8", "o3Value": "0.03",
            "no2Value": "-", "so2Value": "-", "coValue": "-"}
    # Act
    pollutants = m.build_pollutants_for_item(item)
    # Assert
    assert pollutants["pm25"]["value"] == 13.2
    assert pollutants["pm25"]["conversion"].startswith("direct")
    assert pollutants["o3"]["value"] == pytest.approx(1963.0 * 0.03, rel=1e-2)
    assert "no2" not in pollutants
    assert "so2" not in pollutants
    assert "co" not in pollutants


def test_build_pollutants_for_item_returns_empty_when_all_missing():
    # Arrange
    item = {"pm25Value": "-", "pm10Value": "-", "o3Value": "-",
            "no2Value": "-", "so2Value": "-", "coValue": "-"}
    # Act
    pollutants = m.build_pollutants_for_item(item)
    # Assert
    assert pollutants == {}


# ────────────────────────── station reading 조립 ──────────────────────────

def _base_item(**overrides):
    base = dict(
        stationName="종로구",
        dataTime="2026-07-23 15:00",
        pm25Value="13.2",
        pm10Value="22.8",
        o3Value="0.03",
        no2Value="0.02",
        so2Value="0.005",
        coValue="0.4",
    )
    base.update(overrides)
    return base


def test_assemble_station_reading_builds_valid_point_reading():
    # Arrange
    coords = {"서울|종로구": (37.572, 126.9822)}
    item = _base_item()
    generated_at = "2026-07-23T06:05:00Z"
    # Act
    reading = m.assemble_station_reading("서울", "종로구", coords, item, generated_at)
    # Assert
    assert reading is not None
    assert reading["lat"] == 37.572
    assert reading["lon"] == 126.9822
    assert reading["kind"] == "observation"
    assert reading["source"] == "AirKorea"
    assert reading["observedAt"] == "2026-07-23T06:00:00Z"
    assert reading["quality"]["grade"] in ("A", "B", "C", "D", "F")
    assert len(reading["pollutants"]) == 6


def test_assemble_station_reading_skips_when_no_coords():
    # Arrange
    item = _base_item()
    # Act
    reading = m.assemble_station_reading("서울", "Unknown", {}, item, "2026-07-23T06:05:00Z")
    # Assert
    assert reading is None


def test_assemble_station_reading_skips_when_all_pollutants_missing():
    # Arrange
    coords = {"서울|종로구": (37.572, 126.9822)}
    item = _base_item(pm25Value="-", pm10Value="-", o3Value="-",
                       no2Value="-", so2Value="-", coValue="-")
    # Act
    reading = m.assemble_station_reading("서울", "종로구", coords, item, "2026-07-23T06:05:00Z")
    # Assert
    assert reading is None


def test_assemble_station_reading_skips_when_datatime_missing():
    # Arrange
    coords = {"서울|종로구": (37.572, 126.9822)}
    item = _base_item(dataTime="")
    # Act
    reading = m.assemble_station_reading("서울", "종로구", coords, item, "2026-07-23T06:05:00Z")
    # Assert
    assert reading is None


def test_assemble_station_reading_disambiguates_same_name_across_sido():
    # Arrange — 두 시도에 동명 '중구' 좌표가 모두 존재. sido 를 넘기지 않으면 어느
    # 쪽인지 알 수 없어 잘못된 좌표가 붙을 위험이 있는 시나리오(리뷰 지적 사례) —
    # 복합키 join 이 각 시도의 정확한 좌표만 선택하는지 확인한다.
    coords = {
        "서울|중구": (37.5641, 126.9975),
        "부산|중구": (35.1073, 129.0306),
    }
    item = _base_item(stationName="중구")
    # Act
    seoul_reading = m.assemble_station_reading("서울", "중구", coords, item, "2026-07-23T06:05:00Z")
    busan_reading = m.assemble_station_reading("부산", "중구", coords, item, "2026-07-23T06:05:00Z")
    # Assert
    assert seoul_reading is not None and (seoul_reading["lat"], seoul_reading["lon"]) == (37.5641, 126.9975)
    assert busan_reading is not None and (busan_reading["lat"], busan_reading["lon"]) == (35.1073, 129.0306)
