"""collect_openaq_bulk.py 단위 시험 (AAA). 네트워크 호출 0(urlopen 대체) · 시크릿 값 0(가짜 키).

핵심 회귀 대상: 단위 정규화(µg/m³ 계열만 채택, 인식 못 하면 그 파라미터 전체 skip) /
CSV 스키마(organize_data.py `_parse_openaq_multi` 정합, pm25 없는 행 미출력) /
manifest sha256 / 키 부재 graceful skip(exit 0, 파일 미생성) / 429·5xx 재시도 vs
4xx 즉시 전파 / 호출 예산 소진 시 부분 산출.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

import collect_openaq_bulk as m

FAKE_KEY = "a" * 32  # 형태만 채우는 가짜 값 — 실 시크릿 아님


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """재시도·rate-limit backoff 로 테스트가 느려지지 않게."""
    monkeypatch.setattr(m.time, "sleep", lambda _s: None)


def _http_error(code: int, headers: dict | None = None, body: str = "") -> urllib.error.HTTPError:
    hdrs = Message()
    for k, v in (headers or {}).items():
        hdrs.add_header(k, v)
    return urllib.error.HTTPError("https://x", int(code), "Reason", hdrs, io.BytesIO(body.encode()))


# ────────────────────────── pure helpers ──────────────────────────

def test_normalize_unit_accepts_ugm3_variants_and_converts_mgm3():
    # Arrange / Act / Assert — µg/m³ 계열은 1.0, mg/m³ 은 ×1000
    assert m.normalize_unit_to_ugm3("µg/m³") == 1.0
    assert m.normalize_unit_to_ugm3("ug/m3") == 1.0
    assert m.normalize_unit_to_ugm3("UG/M3") == 1.0
    assert m.normalize_unit_to_ugm3("mg/m³") == 1000.0


def test_normalize_unit_rejects_unrecognized_unit():
    # Arrange — ppm 은 몰질량+상태량 없이 환산 불가(임의 추정 금지)
    # Act / Assert
    assert m.normalize_unit_to_ugm3("ppm") is None
    assert m.normalize_unit_to_ugm3("ppb") is None


def test_parse_parameters_response_maps_name_to_id_and_units():
    # Arrange
    data = {"results": [
        {"id": 2, "name": "pm25", "units": "µg/m³", "displayName": "PM2.5"},
        {"id": 7, "name": "no2", "units": "ppm"},
        {"name": "missing_id", "units": "µg/m³"},  # id 없음 — skip
        {"id": 99},  # name 없음 — skip
    ]}
    # Act
    out = m.parse_parameters_response(data)
    # Assert
    assert out == {
        "pm25": {"id": 2, "units": "µg/m³"},
        "no2": {"id": 7, "units": "ppm"},
    }


def test_parse_locations_page_skips_missing_country():
    # Arrange
    data = {"results": [
        {"id": 100, "name": "Seoul A", "country": {"code": "kr"}},
        {"id": 300, "name": "NoCountry", "country": {}},
        {"id": None, "name": "NoId", "country": {"code": "US"}},
    ]}
    # Act
    out, skipped = m.parse_locations_page(data)
    # Assert — country code 는 대문자로 정규화, 결측 2건(country 없음 + id 없음) skip
    assert out == {100: {"name": "Seoul A", "country_code": "KR"}}
    assert skipped == 1  # id=300 만 "country 누락" 사유로 집계(id=None 은 별도 사유)


def test_parse_latest_page_extracts_value_datetime_and_coords():
    # Arrange
    data = {"results": [
        {"locationsId": 100, "sensorsId": 1, "value": 15.5,
         "datetime": {"utc": "2026-09-06T00:00:00Z"},
         "coordinates": {"latitude": 37.5, "longitude": 127.0}},
        {"locationsId": 200, "value": None},  # 값 없음 — skip
        {"sensorsId": 3, "value": 1.0},  # locationsId 없음 — skip
    ]}
    # Act
    out = m.parse_latest_page(data)
    # Assert
    assert out == [{
        "locations_id": 100, "value": 15.5, "datetime_utc": "2026-09-06T00:00:00Z",
        "lat": 37.5, "lon": 127.0,
    }]


# ────────────────────────── fetch_with_retry ──────────────────────────

def test_fetch_with_retry_retries_429_then_succeeds(monkeypatch):
    # Arrange — 첫 호출은 429(Retry-After 헤더 있음), 두번째는 성공
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, headers={"Retry-After": "0"})
        return io.BytesIO(json.dumps({"results": []}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # Act
    data = m.fetch_with_retry("https://x/y", FAKE_KEY)
    # Assert
    assert data == {"results": []}
    assert calls["n"] == 2


def test_fetch_with_retry_retries_5xx_then_succeeds(monkeypatch):
    # Arrange
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(503)
        return io.BytesIO(json.dumps({"results": ["ok"]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # Act
    data = m.fetch_with_retry("https://x/y", FAKE_KEY)
    # Assert
    assert data == {"results": ["ok"]}
    assert calls["n"] == 2


def test_fetch_with_retry_propagates_4xx_immediately(monkeypatch):
    # Arrange — 401(키 오류)은 재시도로 낫지 않으므로 즉시 전파
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(401, body="Invalid API key")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # Act / Assert
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        m.fetch_with_retry("https://x/y", FAKE_KEY)
    assert exc_info.value.code == 401
    assert calls["n"] == 1  # 재시도 없음


def test_fetch_with_retry_raises_after_exhausting_attempts(monkeypatch):
    # Arrange — 항상 503 → MAX_ATTEMPTS 소진 후 RuntimeError
    monkeypatch.setattr(m, "MAX_ATTEMPTS", 2)

    def fake_urlopen(req, timeout=None):
        raise _http_error(503)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # Act / Assert
    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        m.fetch_with_retry("https://x/y", FAKE_KEY)


# ────────────────────────── main() ──────────────────────────

def test_main_missing_api_key_skips_gracefully(monkeypatch, tmp_path, capsys):
    # Arrange
    monkeypatch.delenv("OPENAQ_API_KEY", raising=False)
    # Act
    rc = m.main(["--out", str(tmp_path)])
    # Assert — exit 0, 파일 미생성, 경고 출력
    assert rc == 0
    assert list(tmp_path.iterdir()) == []
    assert "OPENAQ_API_KEY not set" in capsys.readouterr().out


def _dispatch_urlopen(monkeypatch, by_url_substr: dict):
    """URL 부분 문자열 → 응답(dict) 또는 예외 매핑. 매치 없으면 빈 results."""
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append(url)
        for substr, payload in by_url_substr.items():
            if substr in url:
                if isinstance(payload, BaseException):
                    raise payload
                return io.BytesIO(json.dumps(payload).encode())
        return io.BytesIO(json.dumps({"results": []}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


_PARAMETERS_RESPONSE = {"results": [
    {"id": 2, "name": "pm25", "units": "µg/m³"},
    {"id": 1, "name": "pm10", "units": "µg/m³"},
    {"id": 7, "name": "no2", "units": "ppm"},   # 인식 불가 단위 — /latest 호출 자체가 없어야 함
    {"id": 10, "name": "o3", "units": "µg/m³"},
]}

_LOCATIONS_RESPONSE = {"results": [
    {"id": 100, "name": "Seoul A", "country": {"code": "KR"}},
    {"id": 200, "name": "NYC A", "country": {"code": "US"}},
    {"id": 300, "name": "NoCountry", "country": {}},
]}

_PM25_LATEST_RESPONSE = {"results": [
    {"locationsId": 100, "sensorsId": 1, "value": 15.5,
     "datetime": {"utc": "2026-09-06T00:00:00Z"},
     "coordinates": {"latitude": 37.5001, "longitude": 127.0001}},
    {"locationsId": 200, "sensorsId": 2, "value": 8.2,
     "datetime": {"utc": "2026-09-06T00:05:00Z"},
     "coordinates": {"latitude": 40.7001, "longitude": -74.0001}},
    {"locationsId": 300, "sensorsId": 3, "value": 99.9,  # 국가 메타 없음 — 최종 출력 제외
     "datetime": {"utc": "2026-09-06T00:10:00Z"},
     "coordinates": {"latitude": 10.0, "longitude": 20.0}},
    {"locationsId": 400, "sensorsId": 4, "value": 5.0,  # 위치 메타 자체 없음 — 제외
     "datetime": {"utc": "2026-09-06T00:15:00Z"},
     "coordinates": {"latitude": 1.0, "longitude": 1.0}},
]}

_PM10_LATEST_RESPONSE = {"results": [
    {"locationsId": 100, "sensorsId": 11, "value": 30.0,
     "datetime": {"utc": "2026-09-06T00:00:00Z"},
     "coordinates": {"latitude": 37.5, "longitude": 127.0}},
]}


def _happy_path_dispatch(monkeypatch):
    return _dispatch_urlopen(monkeypatch, {
        "/v3/parameters?": _PARAMETERS_RESPONSE,
        "/v3/locations?": _LOCATIONS_RESPONSE,
        "/v3/parameters/2/latest": _PM25_LATEST_RESPONSE,
        "/v3/parameters/1/latest": _PM10_LATEST_RESPONSE,
    })


def test_main_happy_path_writes_per_country_csv_and_manifest(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setenv("OPENAQ_API_KEY", FAKE_KEY)
    calls = _happy_path_dispatch(monkeypatch)

    # Act
    rc = m.main(["--out", str(tmp_path), "--date-tag", "20260906"])

    # Assert — exit 0, no2(단위 ppm) 는 /latest 호출 자체가 없어야 함(예산 낭비 금지)
    assert rc == 0
    assert not any("/parameters/7/latest" in u for u in calls)

    kr_path = tmp_path / "2026-09" / "openaq_KR_multi_20260906.csv"
    us_path = tmp_path / "2026-09" / "openaq_US_multi_20260906.csv"
    assert kr_path.exists()
    assert us_path.exists()

    with open(kr_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    assert list(row.keys()) == m.CSV_FIELDNAMES
    assert row["station_id"] == "100"
    assert row["station_name"] == "Seoul A"
    assert row["lat"] == "37.5001"
    assert row["lon"] == "127.0001"
    assert row["timestamp_utc"] == "2026-09-06T00:00:00Z"
    assert row["pm25"] == "15.5"
    assert row["parameter"] == "pm25"
    assert row["source"] == "openaq"
    assert row["pm10"] == "30.0"  # pm25 latest 와 join
    assert row["no2"] == ""
    assert row["o3"] == ""

    with open(us_path, newline="", encoding="utf-8") as f:
        us_rows = list(csv.DictReader(f))
    assert len(us_rows) == 1
    assert us_rows[0]["pm25"] == "8.2"
    assert us_rows[0]["pm10"] == ""  # US 관측소는 pm10 latest 에 없음 — 빈 값(0 으로 메우지 않음)

    # locationsId=300(국가 메타 없음)·400(위치 메타 전체 없음) 은 어느 파일에도 없어야 함
    assert not any(r["station_id"] == "300" for r in rows + us_rows)
    assert not any(r["station_id"] == "400" for r in rows + us_rows)

    # CSV 는 월별 하위 폴더에만 — prefix 바로 아래에는 manifest 와 월 폴더뿐(HF 폴더당 1만 개 상한)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-09", "manifest.json"]

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert {f["name"] for f in manifest["files"]} == {
        "openaq_KR_multi_20260906.csv", "openaq_US_multi_20260906.csv",
    }
    for entry in manifest["files"]:
        assert entry["path"] == f"2026-09/{entry['name']}"
        digest = hashlib.sha256((tmp_path / entry["path"]).read_bytes()).hexdigest()
        assert entry["sha256"] == digest
        assert entry["bytes"] == (tmp_path / entry["path"]).stat().st_size
        assert entry["rows"] == 1


def test_main_country_filter_narrows_output(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setenv("OPENAQ_API_KEY", FAKE_KEY)
    _happy_path_dispatch(monkeypatch)

    # Act
    rc = m.main(["--out", str(tmp_path), "--date-tag", "20260906", "--countries", "kr"])

    # Assert — 소문자 입력도 대문자로 정규화되어 매칭, US 파일은 생성되지 않음
    assert rc == 0
    assert (tmp_path / "2026-09" / "openaq_KR_multi_20260906.csv").exists()
    assert not (tmp_path / "2026-09" / "openaq_US_multi_20260906.csv").exists()


def test_main_aborts_when_pm25_parameter_id_unresolvable(monkeypatch, tmp_path, capsys):
    # Arrange — API 계약이 바뀌어 pm25 가 파라미터 목록에 없는 경우
    monkeypatch.setenv("OPENAQ_API_KEY", FAKE_KEY)
    _dispatch_urlopen(monkeypatch, {
        "/v3/parameters?": {"results": [{"id": 1, "name": "pm10", "units": "µg/m³"}]},
    })
    # Act
    rc = m.main(["--out", str(tmp_path)])
    # Assert
    assert rc == 1
    assert list(tmp_path.iterdir()) == []
    assert "pm25 parameter id not resolvable" in capsys.readouterr().err


def test_main_aborts_when_zero_pm25_readings_collected(monkeypatch, tmp_path, capsys):
    # Arrange — pm25 파라미터는 해석되지만 /latest 가 빈 결과만 반환
    monkeypatch.setenv("OPENAQ_API_KEY", FAKE_KEY)
    _dispatch_urlopen(monkeypatch, {
        "/v3/parameters?": _PARAMETERS_RESPONSE,
        "/v3/locations?": _LOCATIONS_RESPONSE,
        "/v3/parameters/2/latest": {"results": []},
    })
    # Act
    rc = m.main(["--out", str(tmp_path)])
    # Assert
    assert rc == 1
    assert list(tmp_path.iterdir()) == []
    assert "0 pm25 readings collected" in capsys.readouterr().err


def test_main_partial_output_when_budget_exhausted_mid_run(monkeypatch, tmp_path, capsys):
    """예산이 pm25 fetch 까지만 허용하고 pm10 fetch 전에 소진되는 경우.

    파일은 정상 발행되지만(부분 산출을 감춘 abort 는 하지 않는다) pm10 컬럼은
    비어 있어야 하고 예산 소진 경고가 출력돼야 한다.
    """
    # Arrange — 요청 1)parameters 2)locations 3)pm25/latest 까지만 허용
    monkeypatch.setattr(m, "BUDGET_REQUESTS", 3)
    monkeypatch.setenv("OPENAQ_API_KEY", FAKE_KEY)
    _happy_path_dispatch(monkeypatch)

    # Act
    rc = m.main(["--out", str(tmp_path), "--date-tag", "20260906"])

    # Assert
    assert rc == 0
    out = capsys.readouterr().out
    assert "request budget (3) exhausted" in out
    with open(tmp_path / "2026-09" / "openaq_KR_multi_20260906.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["pm25"] == "15.5"
    assert rows[0]["pm10"] == ""  # pm10 fetch 는 예산 소진으로 스킵됨


def test_main_timestampless_duplicate_sensor_does_not_erase_valid_reading(monkeypatch, tmp_path):
    """리뷰 Major 회귀: 같은 관측소의 타임스탬프 없는 중복 sensor 레코드가
    유효한 시각을 가진 기존 레코드를 덮어써 관측소가 통째로 소실되던 경로."""
    # Arrange — locationsId=100 이 (유효 dt) → (dt 없음) 순서로 두 번 등장
    monkeypatch.setenv("OPENAQ_API_KEY", FAKE_KEY)
    dup_response = {"results": [
        {"locationsId": 100, "sensorsId": 1, "value": 15.5,
         "datetime": {"utc": "2026-09-06T00:00:00Z"},
         "coordinates": {"latitude": 37.5001, "longitude": 127.0001}},
        {"locationsId": 100, "sensorsId": 99, "value": 77.7,
         "datetime": {},  # utc 없음 — 절대 기존 값을 덮어쓰면 안 된다
         "coordinates": {"latitude": 37.5001, "longitude": 127.0001}},
    ]}
    _dispatch_urlopen(monkeypatch, {
        "/v3/parameters?": _PARAMETERS_RESPONSE,
        "/v3/locations?": _LOCATIONS_RESPONSE,
        "/v3/parameters/2/latest": dup_response,
    })

    # Act
    rc = m.main(["--out", str(tmp_path), "--date-tag", "20260906"])

    # Assert — 관측소가 살아있고, 값·시각 모두 유효 레코드의 것
    assert rc == 0
    with open(tmp_path / "2026-09" / "openaq_KR_multi_20260906.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["pm25"] == "15.5"
    assert rows[0]["timestamp_utc"] == "2026-09-06T00:00:00Z"


def test_main_isolates_record_with_malformed_coordinates(monkeypatch, tmp_path):
    """리뷰 Major 회귀: 좌표가 숫자가 아닌 레코드 하나가 조립 단계 round() 에서
    전체 런을 크래시시키던 경로 — 그 레코드만 격리하고 나머지는 발행한다."""
    # Arrange — US 레코드의 좌표가 문자열/배열 (신뢰 경계 밖 비정상 입력)
    monkeypatch.setenv("OPENAQ_API_KEY", FAKE_KEY)
    bad_coords_response = {"results": [
        {"locationsId": 100, "sensorsId": 1, "value": 15.5,
         "datetime": {"utc": "2026-09-06T00:00:00Z"},
         "coordinates": {"latitude": 37.5001, "longitude": 127.0001}},
        {"locationsId": 200, "sensorsId": 2, "value": 8.2,
         "datetime": {"utc": "2026-09-06T00:05:00Z"},
         "coordinates": {"latitude": "not-a-number", "longitude": [1, 2]}},
    ]}
    _dispatch_urlopen(monkeypatch, {
        "/v3/parameters?": _PARAMETERS_RESPONSE,
        "/v3/locations?": _LOCATIONS_RESPONSE,
        "/v3/parameters/2/latest": bad_coords_response,
    })

    # Act
    rc = m.main(["--out", str(tmp_path), "--date-tag", "20260906"])

    # Assert — 크래시 없이 성공, 정상 레코드(KR)만 발행, 비정상(US)은 파일 자체 없음
    assert rc == 0
    assert (tmp_path / "2026-09" / "openaq_KR_multi_20260906.csv").exists()
    assert not (tmp_path / "2026-09" / "openaq_US_multi_20260906.csv").exists()


# ── 서버 보고 잔여 할당(x-ratelimit-*) 반영 ──────────────────────────────────
# 이 키는 소비자가 둘이다(e2-sidecar openaq_shadow, 매시 :15 에 ~120 req/50분당).
# 분당 페이싱만으로는 겹칠 때 50+55=105/분 으로 60/분 상한을 넘는다. OpenAQ 는
# 반복 초과를 "일시적 또는 영구적 차단"으로 다루므로, 서버 장부를 함께 본다.

def _msg(pairs: dict):
    """urllib 이 실제로 돌려주는 헤더 타입(email.message.Message, 대소문자 무시)."""
    import email.message
    msg = email.message.Message()
    for k, v in pairs.items():
        msg[k] = str(v)
    return msg


def test_reset_wait_seconds_reads_plain_seconds():
    # Arrange/Act/Assert — 실측 확정값(run 34004248132: reset=60) = 초 잔여
    assert m.reset_wait_seconds(60) == 60.0


def test_reset_wait_seconds_warns_and_falls_back_when_out_of_range(capsys):
    # Arrange — 에폭(10자리)처럼 초로 설명 안 되는 값이 오면 형식 변경 신호다
    epoch_like = 1_800_000_042
    # Act
    out = m.reset_wait_seconds(epoch_like)
    # Assert — 조용히 재해석하지 않고 경고 + 보수적 폴백
    assert out == m.RATE_RESET_FALLBACK_SECONDS
    assert "x-ratelimit-reset" in capsys.readouterr().err


def test_reset_wait_seconds_falls_back_when_absent():
    # Arrange/Act/Assert — 헤더 없거나 0 이하면 보수적 폴백(창 하나 분량)
    assert m.reset_wait_seconds(None) == m.RATE_RESET_FALLBACK_SECONDS
    assert m.reset_wait_seconds(0) == m.RATE_RESET_FALLBACK_SECONDS


def test_observe_does_not_wait_while_quota_is_healthy(monkeypatch):
    # Arrange
    slept = []
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))
    lim = m.RateLimiter(55, reserve=5)
    # Act — 잔여가 예비분보다 넉넉하다
    lim.observe(_msg({"x-ratelimit-limit": 60, "x-ratelimit-used": 10,
                      "x-ratelimit-remaining": 50, "x-ratelimit-reset": 30}))
    # Assert
    assert slept == []
    assert lim.last_remaining == 50
    assert lim.reset_waits == 0


def test_observe_waits_for_reset_when_quota_nearly_gone(monkeypatch):
    # Arrange
    slept = []
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))
    lim = m.RateLimiter(55, reserve=5)
    # Act — 잔여가 예비분 이하 = 다른 소비자가 창을 먹고 있다
    lim.observe(_msg({"x-ratelimit-remaining": 3, "x-ratelimit-reset": 17}))
    # Assert — 창이 리셋될 때까지 쉰다
    assert slept == [17.0]
    assert lim.reset_waits == 1


def test_observe_caps_the_wait(monkeypatch):
    # Arrange — 시간 창(2,000/h)이 15분 뒤 열리더라도 한 실행을 인질로 잡지 않는다
    slept = []
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))
    lim = m.RateLimiter(55, reserve=5)
    # Act
    lim.observe(_msg({"x-ratelimit-remaining": 0, "x-ratelimit-reset": 900}))
    # Assert
    assert slept == [m.RATE_RESET_MAX_WAIT_SECONDS]


def test_observe_is_noop_without_headers(monkeypatch):
    # Arrange — 헤더가 없으면(테스트 더블·계약 변경) 기존 분당 페이싱만 남는다
    slept = []
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))
    lim = m.RateLimiter(55)
    # Act
    lim.observe(None)
    lim.observe(_msg({"content-type": "application/json"}))
    # Assert — 죽지도, 자지도 않는다
    assert slept == []
    assert lim.last_remaining is None


def test_fetch_with_retry_feeds_429_headers_to_limiter(monkeypatch):
    # Arrange — 429 응답도 잔여/리셋을 싣고 온다. 그 정보를 버리지 않는다.
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, headers={"Retry-After": "0",
                                            "x-ratelimit-remaining": "0",
                                            "x-ratelimit-reset": "11"})
        return io.BytesIO(json.dumps({"results": []}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    lim = m.RateLimiter(55, reserve=5)
    # Act
    data = m.fetch_with_retry("https://x/y", FAKE_KEY, lim)
    # Assert
    assert data == {"results": []}
    assert lim.last_remaining == 0
    assert lim.reset_waits == 1
