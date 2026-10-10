"""collect_noaa_aq.py pure-helper 단위 테스트 (AAA). 네트워크/subprocess 제외."""
import copy
import json
import math
import sys
from pathlib import Path

import pytest

import collect_noaa_aq as m

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "contracts"))
from validate import ContractError, load_schema, validate_payload  # noqa: E402


def test_parse_idx_range_middle_record():
    # Arrange — 연속 레코드 .idx (PM2.5 record 다음에 다른 record)
    idx = (
        "24:18556327:d=2026061612:PMTC:surface:anl:aerosol=Total aerosol:aerosol_size <1e-05:\n"
        "25:19244143:d=2026061612:PMTF:surface:anl:aerosol=Total aerosol:aerosol_size <2.5e-06:\n"
        "26:19755064:d=2026061612:COLMD:entire atmosphere:anl:aerosol=Total aerosol:aerosol_size <1e-05:\n"
    )
    # Act
    rng = m.parse_idx_range(idx, "PMTF:surface:anl:aerosol=Total aerosol:aerosol_size <2.5e-06")
    # Assert — start = 본 레코드 offset, end = 다음 레코드 offset - 1
    assert rng == (19244143, 19755063)


def test_parse_idx_range_last_record_open_ended():
    idx = (
        "31:23255703:d=2026061612:COLMD:...:\n"
        "32:23630055:d=2026061612:PMTC:surface:anl:aerosol=Total aerosol:aerosol_size <1e-05:\n"
    )
    rng = m.parse_idx_range(idx, "PMTC:surface:anl:aerosol=Total aerosol:aerosol_size <1e-05")
    assert rng == (23630055, None)


def test_parse_idx_range_no_match():
    assert m.parse_idx_range("1:0:d=x:AOTK:...:\n", "PMTF:surface") is None


def test_normalize_lon_wraps_eastern_hemisphere():
    # Arrange/Act/Assert — GEFS 0..360 → [-180,180)
    assert m.normalize_lon(0.0) == 0.0
    assert m.normalize_lon(127.0) == 127.0
    assert m.normalize_lon(190.0) == -170.0
    assert m.normalize_lon(359.75) == -0.25
    assert m.normalize_lon(180.0) == -180.0


def test_unit_factor_kg_per_m3_scales_to_ug():
    # PM 질량농도 kg/m³ (<1) → ×1e9
    assert m.unit_factor(8.5e-8) == 1e9
    # 이미 µg/m³ (>=1) → 1.0
    assert m.unit_factor(42.0) == 1.0


def test_representative_value_is_not_moved_by_one_bad_cell():
    # Arrange — 전지구 kg/m³ 값 사이에 decode 글리치 셀 하나가 섞인다.
    # 이 한 셀이 단위 판정을 뒤집으면 격자 전체가 미변환으로 발행된다.
    values = [8.5e-8, 4.9e-9, 1.2e-8, 3.0e-8, 42.0]
    # Act
    sample = m.representative_value(values)
    # Assert — 중앙값은 여전히 kg/m³ 대역이므로 변환이 살아있다
    assert sample == 3.0e-8
    assert m.unit_factor(sample) == 1e9
    # 대조 — 옛 방식(전역 max)이었다면 변환이 통째로 꺼졌다
    assert m.unit_factor(max(values)) == 1.0


def test_representative_value_none_on_empty_input():
    # Arrange/Act/Assert — 빈 입력·전량 비유한값은 판정 근거가 없다
    assert m.representative_value([]) is None
    assert m.representative_value([float("nan"), None]) is None


def test_converted_median_catches_both_unit_failure_directions():
    # Arrange — 2026-09-04 발행 격자 실측 중앙값
    assert m.converted_median_is_sane(4.97) is True
    # Act/Assert — 변환을 놓친 격자(참값의 1e-9)는 0 에 붙는다
    assert m.converted_median_is_sane(4.97e-9) is False
    # 없어야 할 변환을 한 격자는 1e9 배로 치솟는다
    assert m.converted_median_is_sane(4.97e9) is False


def test_converted_median_keeps_a_severe_but_real_episode():
    # Arrange — 전지구 중앙값이 배경농도대 위쪽에 있어도 자릿수 사고는 아니다.
    # 경계가 정상 발행을 죽이지 않는지 확인한다 (게이트의 위양성 방향).
    assert m.converted_median_is_sane(m.SANE_MEDIAN_MIN_UGM3) is True
    assert m.converted_median_is_sane(m.SANE_MEDIAN_MAX_UGM3) is True
    assert m.converted_median_is_sane(150.0) is True


def test_subsample_grid_alignment_and_lon_normalize():
    # Arrange — 0.25° native rows; 1° subsample 은 정수 격자만
    rows = [
        (0.0, 0.0, 10.0),     # keep (1° 정렬)
        (0.25, 0.0, 99.0),    # drop (0.25 비정렬)
        (0.0, 1.0, 20.0),     # keep
        (1.0, 359.0, 30.0),   # keep, lon 359 → -1.0
        (1.0, 0.25, 88.0),    # drop
    ]
    # Act
    points, meta = m.subsample(rows, 1.0)
    # Assert — 3 점만, lon 정규화, (lat,lon) 정렬
    assert len(points) == 3
    assert {(p["lat"], p["lon"]) for p in points} == {(0.0, 0.0), (0.0, 1.0), (1.0, -1.0)}
    assert meta["latMin"] == 0.0
    assert meta["lonMin"] == -1.0
    assert meta["nLat"] == 2 and meta["nLon"] == 3


def test_build_grid_json_matches_contract():
    points = [{"lat": -90.0, "lon": -180.0, "value": 12.3}]
    meta = {"nLat": 1, "nLon": 1, "latMin": -90.0, "lonMin": -180.0}
    grid = m.build_grid_json("pm2_5", points, meta, 1.0, 1718592000000)
    assert grid["variable"] == "pm2_5"
    assert grid["resolution"] == 1.0
    assert grid["dLat"] == 1.0 and grid["dLon"] == 1.0
    assert grid["timestamp"] == 1718592000000
    assert grid["points"] == points
    assert grid["source"] == "NOAA GEFS-Aerosols"


def test_cycle_to_ts_ms_utc():
    # 2026-06-16 12z → UTC epoch ms
    ts = m.cycle_to_ts_ms("20260616", "12")
    assert ts == 1781611200000


# ── 계약 (contracts/current-aq-grid.v1) ──────────────────────────────────────
#
# 픽스처가 아니라 **실제 생산 함수의 산출물**을 검증한다. 손으로 쓴 픽스처만
# 보면 생산자가 필드를 빼도 초록불이 유지된다 — 계약의 존재 이유가 사라진다.


def _grid(variable="pm2_5", values=(4.9, 5.0, 5.1)):
    """생산 함수를 그대로 태워 계약 대상 산출물을 만든다."""
    points = [{"lat": float(i), "lon": float(i), "value": v} for i, v in enumerate(values)]
    meta = {"nLat": len(points), "nLon": 1, "latMin": 0.0, "lonMin": 0.0}
    return m.build_grid_json(variable, points, meta, 1.0, 1788480000000)


@pytest.fixture(scope="module")
def schema():
    return load_schema("current-aq-grid.v1")


@pytest.mark.parametrize("variable", ["pm2_5", "pm10"])
def test_producer_output_satisfies_contract(schema, variable):
    # Arrange/Act — 두 변수 모두 같은 빌더를 쓰므로 계약도 둘 다 통과해야 한다
    grid = _grid(variable)
    # Assert — 위반이면 ContractError 로 죽는다
    validate_payload(grid, schema)


def test_scale_top_differs_per_variable(schema):
    # Arrange/Act — 같은 값이라도 변수에 따라 "스케일 밖" 판정이 달라야 한다.
    # 상수를 하나로 돌려쓰면 PM10 을 PM2.5 자로 재게 된다.
    pm25 = _grid("pm2_5", values=(4.9, 5.0, 550.0))
    pm10 = _grid("pm10", values=(4.9, 5.0, 550.0))
    # Assert — 550 은 PM2.5 스케일(500.4) 밖이지만 PM10 스케일(604) 안이다
    assert pm25["epaAqiScaleTopUgm3"] == 500.4
    assert pm10["epaAqiScaleTopUgm3"] == 604.0
    assert pm25["nAboveEpaAqiScaleTop"] == 1
    assert pm10["nAboveEpaAqiScaleTop"] == 0


def test_beyond_scale_but_within_physical_max_is_counted_not_removed(schema):
    # Arrange — EPA 스케일(500.4)은 넘지만 물리 상한(1,000)은 안 넘는 셀.
    # 이 구간(회수된 헤이즈 봉투 855.1 안)은 물리적으로 가능하므로 서빙한다 —
    # "등급 불가" 공시 카운터만 올라간다 (findings.md Q9-3: 검증 필요 밴드).
    grid = _grid(values=(4.9, 5.0, 855.1))
    # Act
    validate_payload(grid, schema)
    # Assert — 값은 그대로 남고 건수만 올라간다. 이 단언이 깨지는 방향은
    # "조용히 깎기" 뿐이므로 클램프 회귀 가드이기도 하다.
    assert grid["points"][2]["value"] == 855.1
    assert len(grid["points"]) == 3
    assert grid["nAboveEpaAqiScaleTop"] == 1
    assert grid["nAbovePhysicalMaxNulled"] == 0


def test_above_physical_max_is_nulled_and_disclosed(schema):
    # Arrange — 2026-09-03 지구본 실사고 값(12,585 µg/m³ pm2.5)과 야쿠티아
    # 산불대 실측 모델 출력(22,088.96). 둘 다 발표된 QC 관행(>1,000 제외,
    # Mathieu-Campbell 2024) 위의 값이라 음수와 같은 결측 처분이다 —
    # 2026-09-05 사용자 확정으로 구 정책("상한 없음, 세기만")이 뒤집혔다.
    grid = _grid(values=(4.9, 12585.0, 22088.96))
    # Act
    validate_payload(grid, schema)
    # Assert — 셀은 남고 값만 null (최근접 조회가 먼 셀을 집지 않게), 건수 공시.
    assert grid["points"][1]["value"] is None
    assert grid["points"][2]["value"] is None
    assert len(grid["points"]) == 3
    assert grid["nAbovePhysicalMaxNulled"] == 2
    # null 된 셀은 "등급 불가" 카운터에 이중 계상되지 않는다 (역할 분리)
    assert grid["nAboveEpaAqiScaleTop"] == 0


def test_physical_max_differs_per_variable(schema):
    # Arrange/Act — 6,460 µg/m³ 는 PM10 기록 극값(시간평균) 안이라 서빙되지만
    # PM2.5 로는 발표된 QC 상한(1,000) 위라 결측이다. 상수를 하나로 돌려쓰면
    # PM10 을 PM2.5 자로 재게 된다 (scale-top 테스트와 같은 원칙).
    pm25 = _grid("pm2_5", values=(4.9, 5.0, 6460.0))
    pm10 = _grid("pm10", values=(4.9, 5.0, 6460.0))
    # Assert
    assert pm25["physicalMaxUgm3"] == 1000.0
    assert pm10["physicalMaxUgm3"] == 10000.0
    assert pm25["nAbovePhysicalMaxNulled"] == 1
    assert pm25["points"][2]["value"] is None
    assert pm10["nAbovePhysicalMaxNulled"] == 0
    assert pm10["points"][2]["value"] == 6460.0
    validate_payload(pm25, schema)
    validate_payload(pm10, schema)


def test_contract_catches_unconverted_grid(schema):
    # Arrange — 단위 변환을 놓친 격자(참값의 1e-9). 모든 값이 0 에 붙으므로
    # 상한 검사로는 절대 잡히지 않는다 — 분포 보초만이 잡는다.
    grid = _grid(values=(4.9e-9, 5.0e-9, 5.1e-9))
    # Act/Assert
    with pytest.raises(ContractError, match="p50"):
        validate_payload(grid, schema)


def test_contract_requires_the_disclosure_count(schema):
    # Arrange — 기본값 0 을 허용하면 "측정 안 함" 이 "0건" 으로 둔갑한다.
    grid = _grid()
    del grid["nAboveEpaAqiScaleTop"]
    # Act/Assert
    with pytest.raises(ContractError, match="nAboveEpaAqiScaleTop"):
        validate_payload(grid, schema)


def test_contract_rejects_negative_concentration(schema):
    # Arrange — 질량농도는 음수가 될 수 없다 (격자 경로에 없던 하한)
    grid = _grid()
    grid["points"][0]["value"] = -1.0
    # Act/Assert
    with pytest.raises(ContractError, match="minimum"):
        validate_payload(grid, schema)


def test_contract_allows_an_honest_null_cell(schema):
    # Arrange — non-nullable 로 잠그면 정직한 결측이 발행 전체를 멈춘다
    # (비공개 ML 파이프라인에서 실제로 난 사고). null 은 반드시 통과해야 한다.
    grid = _grid()
    grid["points"][0]["value"] = None
    # Act/Assert — 예외가 나면 실패
    validate_payload(grid, schema)


def test_contract_rejects_forged_provenance(schema):
    grid = _grid()
    grid["source"] = "Open-Meteo"
    with pytest.raises(ContractError, match="const"):
        validate_payload(grid, schema)


def test_grid_carries_generated_at_for_freshness_probes(schema):
    # Arrange/Act — timestamp(사이클 유효시각) 와 generatedAt(파일 생성시각) 은
    # 다른 것이다. 후자가 없으면 수집이 멈춰도 발행물만 보고는 알 수 없다.
    grid = _grid()
    # Assert
    assert grid["timestamp"] == 1788480000000
    assert grid["generatedAt"].endswith("Z")
    validate_payload(grid, schema)


def test_summarize_returns_empty_when_nothing_finite():
    # Arrange/Act/Assert — 요약할 것이 없으면 빈 dict. 그러면 계약의 required
    # 가 걸려 발행이 멈춘다 (빈 분포를 조용히 통과시키지 않는다).
    assert m.summarize([None, float("nan")]) == {}
    grid = _grid()
    grid["distribution"] = {}
    schema_ = load_schema("current-aq-grid.v1")
    with pytest.raises(ContractError):
        validate_payload(grid, schema_)


def test_contract_schema_has_no_unsupported_keywords():
    # Arrange/Act/Assert — 검증기가 모르는 키워드는 "통과" 가 아니라 실패다.
    # load_schema 가 로드 시점에 전수 검사하므로, 이 호출이 곧 그 게이트다.
    assert load_schema("current-aq-grid.v1")["title"] == "Current AQ Grid v1"


def test_deep_copy_of_live_shape_still_validates(schema):
    # Arrange — 발행물의 실제 top-level 키 집합(2026-09-04 실측)이 계약의
    # additionalProperties:false 와 어긋나지 않는지. 생산자가 필드를 늘리면
    # 계약도 같이 늘려야 한다는 것을 여기서 강제한다.
    grid = _grid()
    expected = {
        "schemaVersion", "variable", "resolution", "timestamp", "generatedAt",
        "nLat", "nLon", "latMin", "lonMin", "dLat", "dLon", "points", "source",
        "distribution", "epaAqiScaleTopUgm3", "nAboveEpaAqiScaleTop",
        "nNegativeCellsNulled", "physicalMaxUgm3", "nAbovePhysicalMaxNulled",
    }
    # Assert
    assert set(grid.keys()) == expected
    validate_payload(copy.deepcopy(grid), schema)
    assert json.loads(json.dumps(grid))  # 직렬화 가능(발행 경로와 동일)


def test_real_world_distribution_clears_the_guard_band_with_room(schema):
    # Arrange — 통제된 가짜 값이 아니라 **실측 분위수**로 격자를 만든다
    # (2026-09-04 발행물: pm2_5 p25 1.69 / p50 4.97 / p75 9.89 / max 22088.96,
    #  pm10 p50 14.81). 가짜 값만 쓰면 실제 분포가 경계를 만족하는지는
    #  검증되지 않는다 (council 리뷰 지적).
    pm25 = _grid("pm2_5", values=(0.05, 1.69, 4.97, 9.89, 22088.96))
    pm10 = _grid("pm10", values=(0.06, 5.0, 14.81, 37.74, 22088.97))
    # Act
    validate_payload(pm25, schema)
    validate_payload(pm10, schema)
    # Assert — 경계까지의 여유를 명시적으로 못박는다. 누군가 대역을 다시
    # 좁히면(위양성으로 발행이 죽는 방향) 이 단언이 먼저 깨진다.
    for grid in (pm25, pm10):
        p50 = grid["distribution"]["p50"]
        assert p50 / m.SANE_MEDIAN_MIN_UGM3 >= 100, "하한 여유가 100배 미만 — 정상 발행을 죽일 위험"
        assert m.SANE_MEDIAN_MAX_UGM3 / p50 >= 100, "상한 여유가 100배 미만 — 정상 발행을 죽일 위험"


def test_guard_band_still_catches_order_of_magnitude_errors(schema):
    # Arrange/Act/Assert — 대역을 넓혔어도 자릿수 사고는 여전히 잡아야 한다.
    # 넓히기가 검출력을 깎지 않았다는 증거.
    assert m.converted_median_is_sane(4.97e-9) is False   # 미변환
    assert m.converted_median_is_sane(4.97e9) is False    # 이중 변환
    assert m.converted_median_is_sane(4.97) is True
    assert m.converted_median_is_sane(14.81) is True


def test_p99_is_nearest_rank_not_max_at_the_boundary():
    # Arrange — 0.99n 이 정확히 정수인 지점(n=100)에서 옛 구현은 p99 가 max 로
    # 붕괴했다. "p99" 라는 이름으로 max 를 싣는 조용한 라벨 오류다.
    values = list(range(1, 101))
    # Act
    s = m.summarize(values)
    # Assert — nearest-rank(ceil(0.99n)) 는 99, max 는 100
    assert s["p99"] == 99
    assert s["max"] == 100
    assert s["p99"] != s["max"]


def test_p99_matches_nearest_rank_across_sizes():
    # Arrange/Act/Assert — 실 발행 크기(65,160) 포함
    for n in (1, 2, 3, 99, 100, 101, 1000, 65160):
        values = list(range(1, n + 1))
        expected = values[max(0, math.ceil(n * 0.99) - 1)]
        assert m.summarize(values)["p99"] == expected, f"n={n}"


# ── 음수 셀 처분 (물리적으로 불가능한 값 = 결측) ──────────────────────────────
#
# 상한(스케일 초과)과 하한(음수)은 처분이 반대다. 위 §계약의
# `test_beyond_scale_cells_are_counted_not_removed` 와 짝으로 읽을 것 —
# 두 테스트가 같이 있어야 "왜 하나는 남기고 하나는 지우는가" 가 코드에 남는다.


def test_negative_cells_are_nulled_and_counted():
    # Arrange — 음수 하나가 섞인 격자
    points = [
        {"lat": 0.0, "lon": 0.0, "value": 4.9},
        {"lat": 1.0, "lon": 0.0, "value": -0.3},
        {"lat": 2.0, "lon": 0.0, "value": 5.1},
    ]
    # Act
    nulled = m.nullify_impossible_negatives(points)
    # Assert — 값이 아니라 결측이 된다. 0 이나 평균으로 대체하지 않는다.
    assert nulled == 1
    assert points[1]["value"] is None
    assert points[0]["value"] == 4.9 and points[2]["value"] == 5.1


def test_zero_is_not_negative():
    # Arrange — 0 µg/m³ 는 물리적으로 가능하다 (청정 대기). 경계에서 정상값을
    # 떨어뜨리면 필터가 데이터를 잡아먹는다.
    points = [{"lat": 0.0, "lon": 0.0, "value": 0.0}]
    # Act
    nulled = m.nullify_impossible_negatives(points)
    # Assert
    assert nulled == 0
    assert points[0]["value"] == 0.0


def test_already_null_cells_are_not_counted_as_negatives():
    # Arrange — 이미 결측인 셀을 다시 세면 건수가 부풀어 오탐 신호가 된다
    points = [{"lat": 0.0, "lon": 0.0, "value": None}]
    # Act/Assert
    assert m.nullify_impossible_negatives(points) == 0


def test_producer_nulls_negatives_so_the_grid_still_publishes(schema):
    # Arrange — 필터가 없으면 이 격자는 계약(minimum: 0)에 걸려 발행 자체가
    # 막히고, upload 가 건너뛰어져 **옛 격자가 조용히 계속 발행된다**.
    # 그 무음 실패를 피하는 것이 이 필터의 존재 이유다.
    grid = _grid(values=(4.9, -0.3, 5.1))
    # Act — 예외가 나면 실패 (= 발행 차단 회귀)
    validate_payload(grid, schema)
    # Assert — 정직하게 결측 + 건수 공시. 남은 두 셀의 값은 불변.
    assert grid["points"][1]["value"] is None
    assert grid["nNegativeCellsNulled"] == 1
    assert grid["points"][0]["value"] == 4.9 and grid["points"][2]["value"] == 5.1
    # 분포·초과건수는 유한값만 본다 — null 이 0 으로 합산되면 안 된다
    assert grid["distribution"]["n"] == 2
    assert grid["distribution"]["min"] == 4.9


def test_contract_requires_the_negative_disclosure_count(schema):
    # Arrange — 기본값 0 을 허용하면 "필터가 안 돌았다" 가 "0건" 으로 둔갑한다
    grid = _grid()
    del grid["nNegativeCellsNulled"]
    # Act/Assert
    with pytest.raises(ContractError, match="nNegativeCellsNulled"):
        validate_payload(grid, schema)


def test_clean_grid_discloses_zero_not_absence(schema):
    # Arrange/Act — 음수가 없어도 필드는 실린다 (0 = "쟀는데 없었다")
    grid = _grid(values=(4.9, 5.0, 5.1))
    # Assert
    assert grid["nNegativeCellsNulled"] == 0
    validate_payload(grid, schema)
