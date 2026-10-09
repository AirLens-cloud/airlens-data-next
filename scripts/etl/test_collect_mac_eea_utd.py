"""collect_mac_eea_utd.py 단위 테스트 (AAA). 네트워크 없음 — 순수 함수만 대상."""
import inspect
import re

import pytest

import collect_mac_eea_utd as m


# ────────────────────────── request body ──────────────────────────

def test_build_request_body_uses_utd_dataset_by_default():
    # Arrange / Act
    body = m.build_request_body(["DE"], ["PM2.5", "NO2"], "2026-07-23T00:00:00", "2026-07-23T06:00:00")
    # Assert — dataset=1 = UTD/E2a 근실시간 (2026-07-23 DE 프로브 실측: 2 는 E1a 검증
    # 아카이브라 12년×105k행 파일 → OOM + 0 fresh rows 의 근본원인이었다)
    assert body["dataset"] == 1
    assert body["countries"] == ["DE"]
    assert body["aggregationType"] == "hour"
    assert body["cities"] == []


# ────────────────────────── unit 정규화 ──────────────────────────

@pytest.mark.parametrize("unit,expected", [
    ("ug.m-3", 10.0), ("ug/m3", 10.0), ("µg/m³", 10.0),
    ("mg.m-3", 10000.0), ("MG/M3", 10000.0),
])
def test_normalize_unit_to_ugm3_known_units(unit, expected):
    # Arrange / Act
    result = m.normalize_unit_to_ugm3(10.0, unit)
    # Assert
    assert result == pytest.approx(expected)


def test_normalize_unit_to_ugm3_unknown_unit_returns_none():
    # Arrange / Act / Assert — 정직성: 임의 배율 추정 금지
    assert m.normalize_unit_to_ugm3(10.0, "ppm") is None


# ────────────────────────── Samplingpoint -> EoI 코드 ──────────────────────────

def test_extract_station_eoi_code_finds_substring_match():
    # Arrange
    known = {"DE0001A", "DE0002A"}
    # Act
    result = m.extract_station_eoi_code("SPO.DE.DE0001A.PM2.5.1h", known)
    # Assert
    assert result == "DE0001A"


def test_extract_station_eoi_code_picks_longest_match_on_ambiguity():
    # Arrange — "DE0001A" 도 "DE00" 도 둘 다 substring 일 수 있는 인위적 케이스
    known = {"DE00", "DE0001A"}
    # Act
    result = m.extract_station_eoi_code("SPO.DE0001A.PM2.5", known)
    # Assert
    assert result == "DE0001A"


def test_extract_station_eoi_code_returns_none_when_no_match():
    known = {"FR0001A"}
    assert m.extract_station_eoi_code("SPO.DE.DE0001A", known) is None
    assert m.extract_station_eoi_code("", known) is None


# ────────────────────────── station 좌표 파싱(ArcGIS) ──────────────────────────

def test_parse_stations_geojson_response_extracts_coords():
    # Arrange — geometry.x=lon, geometry.y=lat (ArcGIS 관례, outSR=4326)
    data = {"features": [
        {"attributes": {"AirQualityStationEoICode": "DE0001A"},
         "geometry": {"x": 13.4050, "y": 52.5200}},
    ]}
    # Act
    coords = m.parse_stations_geojson_response(data)
    # Assert
    assert coords == {"DE0001A": (52.52, 13.405)}


def test_parse_stations_geojson_response_skips_incomplete_features():
    data = {"features": [
        {"attributes": {}, "geometry": {"x": 1.0, "y": 2.0}},  # 코드 없음
        {"attributes": {"AirQualityStationEoICode": "X"}, "geometry": {}},  # 좌표 없음
    ]}
    coords = m.parse_stations_geojson_response(data)
    assert coords == {}


# ────────────────────────── station 좌표 페이지네이션 ──────────────────────────

def _page(codes, exceeded):
    """오프셋 페이지 1개분 ArcGIS 응답."""
    return {
        "features": [
            {"attributes": {"AirQualityStationEoICode": c}, "geometry": {"x": 1.0, "y": 2.0}}
            for c in codes
        ],
        "exceededTransferLimit": exceeded,
    }


def test_build_stations_query_url_carries_offset_and_page_size():
    # Act
    url = m.build_stations_query_url(offset=4000, page_size=2000)
    # Assert — 서버가 자르는 지점을 우리가 지정한다
    assert "resultOffset=4000" in url
    assert "resultRecordCount=2000" in url
    assert "outSR=4326" in url


def test_paginate_station_coords_follows_pages_until_limit_clears():
    # Arrange — 2페이지 후 exceededTransferLimit 소멸
    pages = {0: _page(["A1", "A2"], True), 2000: _page(["B1"], False)}
    seen = []

    def fetch(offset):
        seen.append(offset)
        return pages[offset]

    # Act
    coords = m.paginate_station_coords(fetch)

    # Assert
    assert set(coords) == {"A1", "A2", "B1"}
    assert seen == [0, 2000]


def test_paginate_station_coords_stops_when_server_ignores_offset():
    # Arrange — 서버가 매번 같은 페이지 + exceededTransferLimit 유지 (진행 없음)
    calls = []

    def fetch(offset):
        calls.append(offset)
        return _page(["A1"], True)

    # Act
    coords = m.paginate_station_coords(fetch)

    # Assert — 상한(12페이지)까지 반복하지 않고 2회에서 끊는다
    assert coords == {"A1": (2.0, 1.0)}
    assert calls == [0, 2000]


def test_paginate_station_coords_keeps_partial_result_on_mid_page_failure():
    # Arrange — 2번째 페이지에서 네트워크 실패
    def fetch(offset):
        if offset == 0:
            return _page(["A1"], True)
        raise TimeoutError("boom")

    # Act
    coords = m.paginate_station_coords(fetch)

    # Assert — 첫 페이지 실패(=전량 상실, 호출자 abort)와 구분되게 부분 결과를 살린다
    assert coords == {"A1": (2.0, 1.0)}


def test_paginate_station_coords_returns_empty_when_first_page_fails():
    def fetch(offset):
        raise TimeoutError("boom")

    # 호출자(main)는 빈 dict 를 abort 신호로 쓴다 — 그 계약이 유지되는지
    assert m.paginate_station_coords(fetch) == {}


# ────────────────────────── flexible ISO 파싱 ──────────────────────────

@pytest.mark.parametrize("raw", [
    "2026-07-23 04:00:00", "2026-07-23T04:00:00", "2026-07-23T04:00:00Z",
    "2026-07-23 04:00:00+00:00",
])
def test_parse_flexible_iso_normalizes_various_formats(raw):
    # Arrange / Act
    dt = m.parse_flexible_iso(raw)
    # Assert
    assert dt.year == 2026 and dt.month == 7 and dt.day == 23
    assert dt.hour == 4
    assert dt.tzinfo is not None


def test_parse_flexible_iso_preserves_non_utc_offset():
    # Arrange — pandas 가 tz-aware parquet 컬럼(예: Europe/Berlin)을 문자열화하면 이 형태로
    # 나온다(`pd.Series(...).astype(str)` 실측: "2026-07-25 09:35:00+02:00"). parse_flexible_iso
    # 는 이 오프셋을 UTC 로 뭉개지 않고 그대로 보존해야 한다 — 정규화는 format_utc_z() 의 몫.
    dt = m.parse_flexible_iso("2026-07-25 09:35:00+02:00")
    assert dt.utcoffset().total_seconds() == 2 * 3600
    assert dt.hour == 9  # 로컬 wall-clock 그대로 보존(아직 UTC 변환 안 함)


# ────────────────────────── UTC 포맷팅 (버그 회귀 방지) ──────────────────────────

def test_format_utc_z_normalizes_non_utc_offset():
    # Arrange — CEST(+02:00) aware datetime. 구버전 버그: naive strftime + 리터럴 "Z" 로
    # 찍으면 로컬 시각(09:35)을 그대로 "09:35:00Z"(UTC 라고 오라벨링) 로 냈다 — 실제보다
    # 2시간 미래인 validAt 이 되는 근본 원인(2026-07-25 PR7 라이브 사례).
    dt = m.parse_flexible_iso("2026-07-25 09:35:00+02:00")
    # Act
    result = m.format_utc_z(dt)
    # Assert — astimezone(UTC) 정규화 후 포맷 → 진짜 UTC 시각(07:35)
    assert result == "2026-07-25T07:35:00Z"


def test_format_utc_z_naive_datetime_treated_as_utc():
    # Arrange — tz 없는 naive datetime(기존 대다수 경로)은 그대로 UTC 로 간주(회귀 없음).
    from datetime import datetime
    dt = datetime(2026, 7, 25, 7, 35, 0)
    # Act / Assert
    assert m.format_utc_z(dt) == "2026-07-25T07:35:00Z"


# ────────────────────────── 최신 행 선택 ──────────────────────────

def test_latest_rows_by_samplingpoint_keeps_max_end_per_key():
    # Arrange
    rows = [
        {"Samplingpoint": "SP1", "Pollutant": "6001", "End": "2026-07-23 03:00:00", "Value": 10.0, "Unit": "ug.m-3", "Validity": 1},
        {"Samplingpoint": "SP1", "Pollutant": "6001", "End": "2026-07-23 05:00:00", "Value": 12.0, "Unit": "ug.m-3", "Validity": 1},
    ]
    # Act
    latest = m.latest_rows_by_samplingpoint(rows)
    # Assert
    assert len(latest) == 1
    assert latest["SP1::6001"]["Value"] == 12.0


def test_latest_rows_by_samplingpoint_drops_invalid_flag():
    # Arrange — Validity < 1 = 무효(regulatory invalid), 제외
    rows = [
        {"Samplingpoint": "SP1", "Pollutant": "5", "End": "2026-07-23 05:00:00", "Value": 999.0, "Unit": "ug.m-3", "Validity": -1},
        {"Samplingpoint": "SP1", "Pollutant": "5", "End": "2026-07-23 04:00:00", "Value": 20.0, "Unit": "ug.m-3", "Validity": 1},
    ]
    # Act
    latest = m.latest_rows_by_samplingpoint(rows)
    # Assert
    assert latest["SP1::5"]["Value"] == 20.0


def test_parquet_columns_cover_validity_guard():
    """`latest_rows_by_samplingpoint()` 이 읽는 키는 전부 parquet 에서 실제로 읽어와야 한다.

    위 `..._drops_invalid_flag` 는 2026-07-28 까지 계속 초록불이었는데도 프로덕션에선
    가드가 한 번도 발화하지 않았다 — fixture 가 `Validity` 를 손으로 넣어준 반면
    `PARQUET_COLUMNS` 에는 그 컬럼이 없어서 실제 row dict 엔 키 자체가 없었고,
    `row.get("Validity") is None` 이라 가드가 조용히 통과했기 때문이다.
    그래서 값이 아니라 **읽는 컬럼과 가드가 의존하는 키 사이의 계약**을 고정한다.
    소스에서 키를 뽑으므로 앞으로 가드가 새 컬럼을 읽기 시작하면 그것도 같이 잡는다.
    """
    # Arrange — 가드 본문이 row 에서 꺼내 쓰는 키를 소스에서 추출
    source = inspect.getsource(m.latest_rows_by_samplingpoint)
    keys_read = set(re.findall(r"""row\.get\(\s*["'](\w+)["']""", source))
    assert "Validity" in keys_read, "가드가 Validity 를 안 읽는다 — 테스트 전제가 깨졌다"
    # Act / Assert
    assert keys_read <= set(m.PARQUET_COLUMNS), (
        f"가드가 읽는 {sorted(keys_read - set(m.PARQUET_COLUMNS))} 가 PARQUET_COLUMNS 에 "
        f"없다 — row 에 키가 없어 가드가 조용히 통과한다"
    )


def test_latest_rows_rejects_eea_future_placeholder_rows():
    """EEA 가 당일 남은 시간대를 미리 채워두는 플레이스홀더 행이 경합에서 이기면 안 된다.

    2026-07-28 라이브 실측 shape: `Value=-999`, `Validity=-1`, `End` 는 다음날 자정.
    이게 "End 최대" 로 낙찰되면 DE 는 `validAt` 이 미래로 찍혀 하류 가드에 관측소째
    버려졌고(416/416), 다른 국가에선 `-999` 가 실측치로 발행됐다.
    """
    # Arrange — 실제 parquet 에서 관측한 배열 그대로
    rows = [
        {"Samplingpoint": "SPO.DE_DEBB049_PM2", "Pollutant": "6001",
         "End": "2026-07-28 04:00:00", "Value": 8.4, "Unit": "ug.m-3", "Validity": 1},
        {"Samplingpoint": "SPO.DE_DEBB049_PM2", "Pollutant": "6001",
         "End": "2026-07-29 00:00:00", "Value": -999.0, "Unit": "ug.m-3", "Validity": -1},
    ]
    # Act
    latest = m.latest_rows_by_samplingpoint(rows)
    # Assert — 실측된 마지막 시각이 낙찰되고, 센티널도 미래 End 도 살아남지 않는다
    assert latest["SPO.DE_DEBB049_PM2::6001"]["Value"] == 8.4
    assert latest["SPO.DE_DEBB049_PM2::6001"]["End"] == "2026-07-28 04:00:00"


# ────────────────────────── country readings 조립 ──────────────────────────

def _rows_for(eoi_code: str):
    return {
        f"SPO.DE.{eoi_code}.PM2.5::6001": {
            "Samplingpoint": f"SPO.DE.{eoi_code}.PM2.5", "Pollutant": "6001",
            "End": "2026-07-23 05:00:00", "Value": 13.2, "Unit": "ug.m-3", "Validity": 1,
        },
        f"SPO.DE.{eoi_code}.NO2::8": {
            "Samplingpoint": f"SPO.DE.{eoi_code}.NO2", "Pollutant": "8",
            "End": "2026-07-23 05:00:00", "Value": 0.02, "Unit": "mg.m-3", "Validity": 1,
        },
    }


def test_assemble_country_readings_builds_valid_point_reading():
    # Arrange
    latest_rows = _rows_for("DE0001A")
    station_coords = {"DE0001A": (52.52, 13.405)}
    # Act
    readings = m.assemble_country_readings(latest_rows, station_coords, "2026-07-23T06:00:00Z")
    # Assert
    assert len(readings) == 1
    r = readings[0]
    assert r["lat"] == 52.52 and r["lon"] == 13.405
    assert r["kind"] == "observation"
    assert r["source"] == "EEA-UTD"
    assert r["pollutants"]["pm25"]["value"] == 13.2
    assert r["pollutants"]["no2"]["value"] == pytest.approx(20.0)  # 0.02 mg/m3 -> 20 ug/m3


def test_assemble_country_readings_skips_unknown_station():
    # Arrange — station_coords 에 해당 EoI 코드 없음
    latest_rows = _rows_for("DE9999Z")
    # Act
    readings = m.assemble_country_readings(latest_rows, {"DE0001A": (52.52, 13.405)}, "2026-07-23T06:00:00Z")
    # Assert
    assert readings == []


def test_assemble_country_readings_valid_at_not_future_for_non_utc_offset_end():
    # Arrange — End 가 CEST(+02:00) 오프셋을 달고 온 행(구버전 버그 재현 시나리오: EEA
    # parquet 의 tz-aware End 컬럼이 pandas 문자열화되면 이런 형태로 온다). generated_at 은
    # 그 행의 실제 UTC 환산 시각과 동일한 시점 — 즉 "지금 막 나온" 정상 관측이다.
    # 구버전은 여기서 observed_at(=validAt) 을 "09:35:00Z"(실제보다 2시간 미래)로 냈다.
    rows = {
        "k": {"Samplingpoint": "SPO.DE.DE0001A.PM2.5", "Pollutant": "6001",
              "End": "2026-07-25 09:35:00+02:00", "Value": 13.2, "Unit": "ug.m-3", "Validity": 1},
    }
    generated_at = "2026-07-25T07:35:00Z"  # = 09:35 CEST 의 정확한 UTC 환산
    # Act
    readings = m.assemble_country_readings(rows, {"DE0001A": (52.52, 13.405)}, generated_at)
    # Assert — validAt 이 진짜 UTC(07:35)로 정규화되어 generatedAt 을 넘지 않는다(더 이상 미래 아님)
    assert len(readings) == 1
    reading = readings[0]
    assert reading["validAt"] == "2026-07-25T07:35:00Z"
    assert reading["observedAt"] == "2026-07-25T07:35:00Z"
    assert reading["validAt"] <= reading["generatedAt"]


def test_assemble_country_readings_skips_unrecognized_unit():
    # Arrange
    rows = {
        "k": {"Samplingpoint": "SPO.DE.DE0001A.PM2.5", "Pollutant": "6001",
              "End": "2026-07-23 05:00:00", "Value": 13.2, "Unit": "ppm", "Validity": 1},
    }
    # Act
    readings = m.assemble_country_readings(rows, {"DE0001A": (52.52, 13.405)}, "2026-07-23T06:00:00Z")
    # Assert
    assert readings == []


def test_assemble_country_readings_isolates_per_station_validation_failure(monkeypatch):
    # Arrange — 두 관측소(DE0001A 정상, DE0002A 는 validate_point_reading 을 강제 실패
    # 시킴). 리뷰 권장사항(#2): 한 관측소의 검증 실패가 나머지 관측소를 폐기해선 안 된다
    # (이전 버전은 여기서 raise 해 호출자가 국가 전체를 skip 했다).
    latest_rows = {**_rows_for("DE0001A"), **_rows_for("DE0002A")}
    station_coords = {"DE0001A": (52.52, 13.405), "DE0002A": (48.8566, 2.3522)}
    original_validate = m.adapter.validate_point_reading

    def fake_validate(record):
        if record["sourceVersion"] == "E2a-UTD-DE0002A":
            return ["forced failure for isolation test"]
        return original_validate(record)

    monkeypatch.setattr(m.adapter, "validate_point_reading", fake_validate)

    # Act
    readings = m.assemble_country_readings(latest_rows, station_coords, "2026-07-23T06:00:00Z")

    # Assert — DE0001A 는 살아남고 DE0002A 만 skip
    assert len(readings) == 1
    assert readings[0]["sourceVersion"] == "E2a-UTD-DE0001A"


# ────────────────────────── 병렬 다운로드 오케스트레이터 ──────────────────────────

def test_download_country_parquets_collects_all_within_budget(monkeypatch):
    # Arrange — 네트워크 대신 URL 별 고정 행을 돌려주는 fake. 예산은 넉넉히.
    def fake_download(url, cutoff=None):
        return [{"url": url}]

    monkeypatch.setattr(m, "download_parquet_rows", fake_download)
    urls = [f"http://x/{i}.parquet" for i in range(30)]

    # Act
    got = m.download_country_parquets("DE", urls, deadline=m.time.monotonic() + 60)

    # Assert — 순서 무관 전량 수집
    assert sorted(r["url"] for r in got.rows) == sorted(urls)
    assert (got.files_total, got.dropped, got.failures) == (len(urls), 0, 0)


def test_download_country_parquets_stops_submitting_past_deadline(monkeypatch, capsys):
    # Arrange — 이미 지난 deadline. 새 제출 0 → 결과 0 + dropped WARN (조용한 절단 금지).
    def fake_download(url, cutoff=None):  # pragma: no cover — 제출 자체가 없어야 한다
        raise AssertionError("must not download past deadline")

    monkeypatch.setattr(m, "download_parquet_rows", fake_download)
    urls = [f"http://x/{i}.parquet" for i in range(5)]

    # Act
    got = m.download_country_parquets("DE", urls, deadline=m.time.monotonic() - 1)

    # Assert — 전량 미제출은 dropped 이지 failures 가 아니다 (예산 문제 ≠ 네트워크 문제)
    assert got.rows == []
    assert (got.dropped, got.failures) == (5, 0)
    assert "5/5 parquet files not downloaded" in capsys.readouterr().err


def test_download_country_parquets_isolates_single_failure(monkeypatch, capsys):
    # Arrange — 1개 URL 만 실패시키고 나머지는 정상 (기존 순차 루프의 WARN+계속 관례 유지).
    def fake_download(url, cutoff=None):
        if url.endswith("2.parquet"):
            raise RuntimeError("boom")
        return [{"url": url}]

    monkeypatch.setattr(m, "download_parquet_rows", fake_download)
    urls = [f"http://x/{i}.parquet" for i in range(4)]

    # Act
    got = m.download_country_parquets("DE", urls, deadline=m.time.monotonic() + 60)

    # Assert — 실패 1건 WARN, 나머지 3건 수집. 예산은 멀쩡하므로 dropped=0
    assert len(got.rows) == 3
    assert (got.dropped, got.failures) == (0, 1)
    assert "download/parse failed — boom" in capsys.readouterr().err


def test_download_country_parquets_drops_remainder_when_deadline_passes_mid_run(
        monkeypatch, capsys):
    # Arrange — 가짜 단조 시계: 다운로드 1건마다 10 tick 전진, deadline=25 →
    # worker 1개 기준 3건 제출 후 예산 소진, 나머지 2건 dropped (mid-drain 경계).
    class FakeClock:
        def __init__(self):
            self.t = 0.0

        def monotonic(self):
            return self.t

    clock = FakeClock()
    monkeypatch.setattr(m.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(m, "DOWNLOAD_WORKERS", 1)

    def fake_download(url, cutoff=None):
        clock.t += 10.0
        return [{"url": url}]

    monkeypatch.setattr(m, "download_parquet_rows", fake_download)
    urls = [f"http://x/{i}.parquet" for i in range(5)]

    # Act
    got = m.download_country_parquets("DE", urls, deadline=25.0)

    # Assert — 부분 수집 + 잔여분 WARN
    assert len(got.rows) == 3
    assert (got.dropped, got.failures) == (2, 0)
    assert "2/5 parquet files not downloaded" in capsys.readouterr().err


def test_download_country_parquets_abandons_stuck_inflight_after_grace(monkeypatch, capsys):
    # Arrange — 완료되지 않는 다운로드(Event 대기)로 in-flight 스톨을 재현.
    # deadline 은 곧 지나고 grace 도 짧게 — 드레인이 무한정 기다리지 않고
    # abandon + dropped 집계 후 반환해야 한다 (CI 드립피딩 스톨 회귀 방지).
    import threading
    import time as _time

    release = threading.Event()

    def stuck_download(url, cutoff=None):
        release.wait(timeout=10)
        return []

    monkeypatch.setattr(m, "download_parquet_rows", stuck_download)
    monkeypatch.setattr(m, "DOWNLOAD_WORKERS", 1)
    monkeypatch.setattr(m, "DRAIN_GRACE_SECONDS", 0.1)

    try:
        # Act
        start = _time.monotonic()
        got = m.download_country_parquets(
            "DE", ["http://x/0.parquet", "http://x/1.parquet"],
            deadline=_time.monotonic() + 0.2,
        )
        elapsed = _time.monotonic() - start

        # Assert — 스톨에도 1초 안에 반환, 수집 0 + 전량 dropped WARN
        assert got.rows == []
        # 유기된 in-flight 는 dropped — 요청이 죽은 게 아니라 안 기다린 것이다
        assert (got.dropped, got.failures) == (2, 0)
        assert elapsed < 5
        err = capsys.readouterr().err
        assert "abandoning 1 in-flight downloads" in err
        assert "2/2 parquet files not downloaded" in err
    finally:
        release.set()


def test_api_post_json_abandons_dripping_response_at_wall_cap(monkeypatch, capsys):
    # Arrange — 드립피딩 응답 재현: urlopen 자체가 반환하지 않음 (per-op timeout 무력 상황과
    # 동등한 관측면). 벽시계 상한이 끊고 None 반환해야 budget deadline 이 살아난다.
    import threading
    import time as _time

    release = threading.Event()

    def stuck_urlopen(*args, **kwargs):
        release.wait(timeout=10)
        raise AssertionError("should have been abandoned before completing")

    monkeypatch.setattr("urllib.request.urlopen", stuck_urlopen)
    monkeypatch.setattr(m, "API_WALL_TIMEOUT_SECONDS", 0.2)

    try:
        # Act
        start = _time.monotonic()
        result = m.api_post_json("ParquetFile/urls", {"countries": ["DE"]})
        elapsed = _time.monotonic() - start

        # Assert — None + 1초 내 반환 + abandoned 로그
        assert result is None
        assert elapsed < 5
        assert "exceeded wall-clock cap" in capsys.readouterr().err
    finally:
        release.set()


def test_download_parquet_rows_filters_history_to_cutoff_window(monkeypatch, tmp_path):
    # Arrange — UTD parquet 는 samplingpoint 당 전체 히스토리(실측 12년/105k행)를 담는다.
    # cutoff 이전 행이 to_dict 전에 걸러져야 메모리 폭주(CI 러너 OOM)가 재발하지 않는다.
    import io
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    now = datetime.now(timezone.utc)
    df = pd.DataFrame({
        "Samplingpoint": ["DE/SPO1"] * 3,
        "Pollutant": ["6001"] * 3,
        "End": [
            "2013-01-01 01:00:00",                                # 12년 전 히스토리
            (now - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"),  # 창 밖
            (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),   # 창 안
        ],
        "Value": [1.0, 2.0, 3.0],
        "Unit": ["ug.m-3"] * 3,
        "Validity": [1] * 3,      # 가드가 읽는 컬럼 — 프루닝에서 빠지면 안 된다
        "AggType": ["hour"] * 3,  # 파서 미사용 컬럼 — 컬럼 프루닝 검증용
    })
    buf = io.BytesIO()
    df.to_parquet(buf)
    payload = buf.getvalue()

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return payload

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())

    # Act
    rows = m.download_parquet_rows("http://x/sp.parquet", cutoff=now - timedelta(hours=8))

    # Assert — 창 안 1행만, 미사용 컬럼 제거됨, cutoff 없으면 전체 유지(하위호환)
    assert len(rows) == 1
    assert rows[0]["Value"] == 3.0
    assert "AggType" not in rows[0]
    # 프루닝이 가드용 컬럼까지 걷어내면 `Validity>=1` 검사가 조용히 무력화된다 (2026-07-28)
    assert rows[0]["Validity"] == 1
    assert len(m.download_parquet_rows("http://x/sp.parquet")) == 3


# ────────────────────────── 국가 순회 회전 ──────────────────────────

def test_rotate_countries_shifts_start_by_utc_hour():
    # Arrange
    countries = ["DE", "FR", "IT", "ES", "PL", "NL"]
    # Act / Assert — 6개국이면 시각마다 다른 국가가 첫 순번
    assert m.rotate_countries(countries, 0) == ["DE", "FR", "IT", "ES", "PL", "NL"]
    assert m.rotate_countries(countries, 3) == ["ES", "PL", "NL", "DE", "FR", "IT"]


def test_rotate_countries_wraps_past_list_length():
    # Arrange / Act — 시각은 0..23, 국가 수는 그보다 적다
    rotated = m.rotate_countries(["DE", "FR", "IT"], 23)
    # Assert — 23 % 3 == 2
    assert rotated == ["IT", "DE", "FR"]


def test_rotate_countries_covers_every_country_first_across_a_day():
    # Arrange — 회전의 존재 이유: 예산이 한 국가밖에 못 덮어도 전부 첫 순번을 갖는다
    countries = ["DE", "FR", "IT", "ES", "PL", "NL"]
    # Act
    firsts = {m.rotate_countries(countries, h)[0] for h in range(24)}
    # Assert
    assert firsts == set(countries)


def test_rotate_countries_is_deterministic_for_the_same_hour():
    # Arrange / Act — 재시도·workflow_dispatch 가 같은 순서를 내야 로그 대조가 된다
    countries = ["DE", "FR", "IT", "ES", "PL", "NL"]
    # Assert
    assert m.rotate_countries(countries, 7) == m.rotate_countries(countries, 7)


def test_rotate_countries_empty_list():
    # Arrange / Act / Assert
    assert m.rotate_countries([], 5) == []


def test_rotate_countries_preserves_membership():
    # Arrange
    countries = ["DE", "FR", "IT", "ES", "PL", "NL"]
    # Act / Assert — 회전은 순서만 바꾼다. 국가를 떨어뜨리지 않는다
    for hour in range(24):
        assert sorted(m.rotate_countries(countries, hour)) == sorted(countries)


# ─────────────────── 커버리지 계측 + 워크플로 배선 계약 ───────────────────
#
# EEA 잔여 증상은 **두 개**다. `WARN [PL] budget exhausted` 는 예산 문제이고,
# FR 의 `<urlopen error timed out>` 다수는 개별 파일 네트워크 문제다. 예산을 올려도
# 후자는 안 고쳐지는데, 지금까지 둘 다 같은 WARN 로그로만 남아 구분되지 않았다
# (그리고 로그는 run 이 지나가면 사라진다). 그래서 숫자를 먼저 조정하지 않고 **계측만**
# 넣는다 — 480→360→600 으로 이미 두 번 튜닝했는데 재발했다는 건 튜닝이 답이 아니라는
# 신호다.

def test_country_coverage_separates_budget_drop_from_download_failure():
    # Arrange — 같은 "덜 받았다" 인데 원인이 다른 두 국가
    starved = m.CountryDownload(rows=[{}], files_total=10, dropped=7, failures=0)
    flaky = m.CountryDownload(rows=[{}], files_total=10, dropped=0, failures=7)

    # Act
    a = m.country_coverage("visited", starved, stations=3)
    b = m.country_coverage("visited", flaky, stations=3)

    # Assert — 한 숫자로 합치면 예산을 올려야 할 상황과 올려도 소용없는 상황이 섞인다
    assert (a["files_dropped_budget"], a["download_failures"]) == (7, 0)
    assert (b["files_dropped_budget"], b["download_failures"]) == (0, 7)


def test_country_coverage_records_skips_without_a_download_result():
    # Arrange / Act — 시작조차 못 한 국가도 *왜* 인지 남아야 한다
    skipped = m.country_coverage("skipped_budget")

    # Assert
    assert skipped["status"] == "skipped_budget"
    assert skipped["stations"] == 0
    assert skipped["files_total"] == 0


def test_write_coverage_is_a_noop_without_a_path(monkeypatch):
    # Arrange — 로컬 실행에 부담을 주지 않는다
    monkeypatch.setattr(m, "COVERAGE_PATH", "")

    # Act / Assert — 예외 없이 지나가면 통과
    m.write_coverage({"DE": m.country_coverage("visited")}, ["DE"], m.datetime.now(m.timezone.utc))


def test_write_coverage_persists_country_records(tmp_path, monkeypatch):
    # Arrange
    out = tmp_path / "eea-utd.json"
    monkeypatch.setattr(m, "COVERAGE_PATH", str(out))
    now = m.datetime(2026, 7, 28, 9, 0, tzinfo=m.timezone.utc)

    # Act
    m.write_coverage({"DE": m.country_coverage("visited"), "PL": m.country_coverage("skipped_budget")},
                     ["DE", "PL"], now)

    # Assert
    doc = m.json.loads(out.read_text())
    assert doc["countries"]["PL"]["status"] == "skipped_budget"
    assert doc["countryOrder"] == ["DE", "PL"]
    assert doc["rotationHourUtc"] == 9
    assert doc["budgetSeconds"] == m.BUDGET_SECONDS


def test_render_coverage_names_every_country():
    # Arrange / Act
    out = m.render_coverage({
        "DE": m.country_coverage("visited"),
        "PL": m.country_coverage("skipped_budget"),
    })

    # Assert
    assert "DE" in out and "PL" in out and "skipped_budget" in out


# 워크플로 YAML 을 **직접 읽는다** — fixture 에 복사하면 실물이 바뀌어도 테스트가 모른다
# (그게 #1028 의 구조였다: 테스트가 프로덕션의 선언 자체를 검증하지 않았다).

def _workflow_text() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[2] / ".github/workflows/mac-data-publish.yml").read_text(
        encoding="utf-8"
    )


def _workflow_budget_seconds() -> float:
    declared = re.search(r"MAC_EEA_BUDGET_SECONDS:\s*'(\d+)'", _workflow_text())
    assert declared, "워크플로에 MAC_EEA_BUDGET_SECONDS 선언이 없다"
    return float(declared.group(1))


def test_code_budget_default_matches_the_workflow():
    # Arrange / Act
    declared = _workflow_budget_seconds()

    # Assert — 기본값이 갈리면 로컬에서 잰 커버리지가 CI 의 커버리지가 아니다.
    # 480(코드) vs 600(워크플로) 로 실제로 갈려 있었다.
    assert declared == m.BUDGET_SECONDS


def test_shell_backstop_keeps_the_documented_margin_over_the_budget():
    # Arrange — 백스톱 주석은 "budget + 240s" 라고 적어놨지만 강제하는 건 없었다.
    # 예산만 올리고 백스톱을 안 올리면 수집기가 부분 결과를 쓰기 전에 exit 124 로 죽는다
    # (run 29989225749 가 정확히 그 사고였다).
    #
    # 기준은 **워크플로가 실제로 넘기는 예산**이다. 코드 상수를 기준 삼으면 워크플로
    # 예산만 올렸을 때 (= 실제 사고 형태) 이 테스트가 조용히 통과한다.
    backstop = re.search(
        r"timeout (\d+) python3 scripts/etl/collect_mac_eea_utd\.py", _workflow_text()
    )

    # Act / Assert
    assert backstop, "EEA 스텝의 shell timeout 백스톱을 찾지 못했다"
    assert int(backstop.group(1)) >= _workflow_budget_seconds() + 240


def test_workflow_wires_the_coverage_sidecar_end_to_end():
    # Arrange
    text = _workflow_text()

    # Assert — 수집기만 쓰고 index 가 안 읽으면 발행물에 아무것도 안 실린다(반쪽 수정)
    assert "MAC_EEA_COVERAGE_PATH:" in text
    assert "MAC_INDEX_COVERAGE_DIR=" in text


# ────────────────── samplingpoint→EoI 인덱스 (ES 조인 갭) ──────────────────
#
# 아래 문자열은 지어낸 픽스처가 아니라 2026-07-29 라이브 실측값이다.
# ES 는 Samplingpoint 에 EoI 코드를 담지 않고 국가 내부 숫자 ID 만 쓴다 —
# substring 매칭만 쓰던 시절 ES 관측소 743 개가 전량 조용히 탈락했다(발행물 stations 0).

ES_LIVE_SAMPLINGPOINT = "ES/SP_39075006_10_49"   # → ES1580A
DE_LIVE_SAMPLINGPOINT = "DE/SPO.DE_DEBB049_PM2_dataGroup1"  # → DEBB049 (substring 으로 풀림)


def test_resolve_station_eoi_prefers_substring_match_over_index():
    # Arrange — 인덱스가 다른 답을 들고 있어도 substring 이 이긴다.
    # 근거: PL 실측에서 인덱스는 7건 중 1건을 놓쳤다(카탈로그 미등재 신규 관측소).
    known = {"DEBB049"}
    sp_index = {DE_LIVE_SAMPLINGPOINT: "DE9999Z"}
    # Act
    result = m.resolve_station_eoi(DE_LIVE_SAMPLINGPOINT, known, sp_index)
    # Assert
    assert result == "DEBB049"


def test_resolve_station_eoi_falls_back_to_index_for_numeric_id_country():
    # Arrange — ES 실측 포맷: 문자열 어디에도 EoI 코드가 없다
    known = {"ES1580A"}
    assert m.extract_station_eoi_code(ES_LIVE_SAMPLINGPOINT, known) is None
    # Act
    result = m.resolve_station_eoi(
        ES_LIVE_SAMPLINGPOINT, known, {ES_LIVE_SAMPLINGPOINT: "ES1580A"},
    )
    # Assert
    assert result == "ES1580A"


def test_resolve_station_eoi_returns_none_without_index():
    # Arrange — 인덱스 없이 ES 포맷이면 못 푼다(=수정 전 동작. 회귀 시 이 테스트가 먼저 깨진다)
    known = {"ES1580A"}
    # Act / Assert
    assert m.resolve_station_eoi(ES_LIVE_SAMPLINGPOINT, known, None) is None
    assert m.resolve_station_eoi(ES_LIVE_SAMPLINGPOINT, known, {}) is None


def test_parse_samplingpoint_index_response_extracts_from_popup_html():
    # Arrange — PopupInfo 안 다운로드 링크(2026-07-29 실측 형태). 컨테이너명은 dataset 별로
    # 다르므로 앵커로 쓰지 않는다.
    data = {"features": [{"attributes": {
        "AirQualityStationEoICode": "ES1838A",
        "PopupInfo": (
            '<p><a href="https://eeadmz1batchservice02.blob.core.windows.net/'
            'airquality-p-e1a/ES/SP_28009001_10_49.parquet">PM10</a>, ug/m<sup>3</sup></p>'
            '<p><a href="https://eeadmz1batchservice02.blob.core.windows.net/'
            'airquality-p-e1a/ES/SP_28009001_14_6.parquet">O3</a></p>'
        ),
    }}]}
    # Act
    index = m.parse_samplingpoint_index_response(data)
    # Assert — 관측소 1개가 여러 samplingpoint 를 가진다(오염물질마다 하나)
    assert index == {
        "ES/SP_28009001_10_49": "ES1838A",
        "ES/SP_28009001_14_6": "ES1838A",
    }


def test_parse_samplingpoint_index_response_skips_features_without_popup_or_code():
    data = {"features": [
        {"attributes": {"AirQualityStationEoICode": "ES1838A", "PopupInfo": None}},
        {"attributes": {"AirQualityStationEoICode": None, "PopupInfo": "ES/SP_1_2_3.parquet"}},
        {"attributes": {}},
    ]}
    assert m.parse_samplingpoint_index_response(data) == {}


def test_paginate_samplingpoint_index_stops_when_transfer_limit_not_exceeded():
    # Arrange
    pages = {
        0: {"features": [{"attributes": {
            "AirQualityStationEoICode": "ES1838A",
            "PopupInfo": "x/ES/SP_28009001_10_49.parquet",
        }}], "exceededTransferLimit": True},
        m.STATIONS_PAGE_SIZE: {"features": [{"attributes": {
            "AirQualityStationEoICode": "ES2108A",
            "PopupInfo": "x/ES/SP_31201015_10_49.parquet",
        }}]},
    }
    # Act
    index = m.paginate_samplingpoint_index(lambda offset: pages[offset])
    # Assert
    assert index == {
        "ES/SP_28009001_10_49": "ES1838A",
        "ES/SP_31201015_10_49": "ES2108A",
    }


def test_paginate_samplingpoint_index_stops_when_no_progress():
    # Arrange — 서버가 resultOffset 을 무시해 같은 페이지를 되풀이하는 경우
    page = {"features": [{"attributes": {
        "AirQualityStationEoICode": "ES1838A",
        "PopupInfo": "x/ES/SP_28009001_10_49.parquet",
    }}], "exceededTransferLimit": True}
    calls = []

    def fetch(offset):
        calls.append(offset)
        return page

    # Act
    index = m.paginate_samplingpoint_index(fetch)
    # Assert — 상한(12페이지)까지 가지 않고 진행 없음으로 끊는다
    assert len(index) == 1
    assert len(calls) == 2


def test_paginate_samplingpoint_index_keeps_partial_on_page_failure():
    def fetch(offset):
        if offset == 0:
            return {"features": [{"attributes": {
                "AirQualityStationEoICode": "ES1838A",
                "PopupInfo": "x/ES/SP_28009001_10_49.parquet",
            }}], "exceededTransferLimit": True}
        raise OSError("boom")

    assert m.paginate_samplingpoint_index(fetch) == {"ES/SP_28009001_10_49": "ES1838A"}


def test_samplingpoint_index_query_url_asks_for_popup_without_geometry():
    # Arrange / Act
    url = m.build_samplingpoint_index_query_url(offset=2000)
    # Assert — geometry 는 좌표 질의가 이미 가져오므로 여기선 꺼야 한다(응답 ~20MB)
    assert "PopupInfo" in url
    assert "returnGeometry=false" in url
    assert "resultOffset=2000" in url


def _es_rows():
    """ES 실측 포맷 행 — EoI 코드가 Samplingpoint 에 없다."""
    return {
        f"{ES_LIVE_SAMPLINGPOINT}::5": {
            "Samplingpoint": ES_LIVE_SAMPLINGPOINT, "Pollutant": "5",
            "End": "2026-07-29 10:00:00", "Value": 21.0, "Unit": "ug.m-3", "Validity": 1,
        },
    }


def test_assemble_country_readings_recovers_es_rows_with_index():
    # Arrange
    coords = {"ES1580A": (40.4, -3.7)}
    # Act — 인덱스 없이는 0, 인덱스가 있으면 조립된다
    without = m.assemble_country_readings(_es_rows(), coords, "2026-07-29T11:00:00Z")
    with_index = m.assemble_country_readings(
        _es_rows(), coords, "2026-07-29T11:00:00Z",
        sp_index={ES_LIVE_SAMPLINGPOINT: "ES1580A"},
    )
    # Assert
    assert without == []
    assert len(with_index) == 1
    assert with_index[0]["lat"] == 40.4


def test_assemble_country_readings_records_drop_reasons():
    # Arrange — 조인 실패 사유가 커버리지에 드러나야 한다("stations 0" 의 이유)
    drops: dict[str, int] = {}
    # Act
    m.assemble_country_readings(
        _es_rows(), {"ES1580A": (40.4, -3.7)}, "2026-07-29T11:00:00Z", drops=drops,
    )
    # Assert
    assert drops == {"eoi_join": 1}


def test_country_coverage_carries_row_drop_reasons():
    # Act
    record = m.country_coverage("visited", stations=0, drops={"eoi_join": 5})
    # Assert — 예산 부족(files_dropped_budget)과 조인 실패를 사이드카만 보고 구분 가능
    assert record["rows_dropped"] == {"eoi_join": 5}
    assert record["files_dropped_budget"] == 0
    assert record["stations"] == 0


def test_render_coverage_surfaces_drop_reasons():
    # Act
    text = m.render_coverage({
        "ES": m.country_coverage("visited", stations=0, drops={"eoi_join": 5}),
        "DE": m.country_coverage("visited", stations=3),
    })
    # Assert — 국가는 정렬돼 나오므로 [0]=헤더 [1]=DE [2]=ES
    lines = text.split("\n")
    assert "eoi_join=5" in lines[2]
    assert "rows_dropped" not in lines[1]  # 탈락 없는 국가 줄은 깨끗하게 유지
