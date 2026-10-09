"""collect_firms.py 계약 시험.

2026-07-30 사고가 요구한 커버리지: 수집 로직이 워크플로 YAML heredoc 안에 살아서
어떤 스위트도 닿지 않았고, "HTTP 200 + 파싱 0건" 이 발행 데이터 17356건을 0건으로
덮었다. 이제 로직이 이 모듈에 있으므로 그 경로를 직접 시험한다.

네트워크 호출 0 (urlopen 대체) · 시크릿 읽기 0 (형태 게이트만 통과하는 가짜 키).
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

import collect_firms as cf

FAKE_KEY = "a" * 32  # 형태 게이트만 통과하는 가짜 값 — 실 시크릿 아님
HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,confidence,frp"
TWO_ROWS = "\n1.5,2.5,300.1,0.4,0.4,2026-07-30,0212,n,12.3\n-3.25,44.0,310.0,0.4,0.4,2026-07-30,0213,h,45.6"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """재시도 backoff 로 테스트가 느려지지 않게."""
    monkeypatch.setattr(cf.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cf, "preflight", lambda *_a, **_k: None)


def _responses(monkeypatch, script):
    """source 이름 → 응답 매핑. 값은 본문(str) 또는 raise 할 예외.

    MAP_KEY 는 URL 경로에 있으므로 source 는 그 뒤 세그먼트에서 찾는다.
    """
    calls: list[str] = []

    def fake_urlopen(url, timeout=None):
        source = url.split(f"/{FAKE_KEY}/")[1].split("/")[0]
        calls.append(source)
        # 명시하지 않은 소스는 "헤더만"(0행) — 세 소스를 모두 시도하는 계약이라
        # 테스트가 관심 있는 소스만 적어도 되게 한다.
        outcome = script.get(source, HEADER)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _http_error(code: str | int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", int(code), "Reason", {}, io.BytesIO(body.encode()))


def test_shape_gate_rejects_non_key_text(monkeypatch, capsys):
    """문장·JWT 가 시크릿에 들어가면 NASA 로 보내지 않고 멈춘다."""
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", "MAP_KEY: 여기에 붙여넣으세요 (676자 JWT 같은 것)")
    assert cf.main([]) == 1
    assert "MAP_KEY malformed" in capsys.readouterr().out


def test_missing_key_fails(monkeypatch):
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", "   ")
    assert cf.main([]) == 1


def test_key_shape_never_contains_the_value():
    """진단 dict 에 값·부분문자열이 새지 않는다 (5가드 §2)."""
    shape = cf.key_shape("0123456789abcdef0123456789abcdef")
    assert shape == {"len": 32, "matches_32_lower_hex": True, "digits": 20,
                     "lower": 12, "upper": 0, "non_alnum": 0}
    assert "0123" not in json.dumps(shape)


def test_happy_path_writes_payload(monkeypatch, tmp_path):
    _responses(monkeypatch, {cf.SOURCES[0]: HEADER + TWO_ROWS})
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    out = tmp_path / "active-fires.json"
    assert cf.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == 2
    assert data["source"] == cf.SOURCES[0]
    assert data["fires"][1]["frp"] == 45.6
    assert data["fires"][0]["brightness"] == 300.1


def test_empty_parse_writes_nothing_and_fails(monkeypatch, tmp_path, capsys):
    """사고 재현: 모든 소스가 헤더만 → 파일 미생성 + exit 1 (덮어쓰기 방지)."""
    _responses(monkeypatch, dict.fromkeys(cf.SOURCES, HEADER))
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    out = tmp_path / "active-fires.json"
    with pytest.raises(SystemExit) as exc:
        cf.main(["--out", str(out)])
    assert exc.value.code == 1
    assert not out.exists()
    captured = capsys.readouterr()
    assert "FIRMS empty parse" in captured.out
    # 진단은 "비었나 / 컬럼이 바뀌었나" 를 가를 수 있어야 한다
    assert "columns=" in captured.out and "bytes" in captured.out
    assert "response head:" in captured.err


def test_source_fallback_when_first_source_is_dry(monkeypatch, tmp_path, capsys):
    """SNPP 가 0행이면 다음 소스로 데이터를 얻고, 쓴 소스를 기록한다."""
    calls = _responses(monkeypatch, {
        cf.SOURCES[0]: HEADER,               # 상류가 조용히 마름
        cf.SOURCES[1]: HEADER + TWO_ROWS,    # 살아 있음
        cf.SOURCES[2]: HEADER,
    })
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    out = tmp_path / "active-fires.json"
    assert cf.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source"] == cf.SOURCES[1]
    assert data["count"] == 2
    assert calls == list(cf.SOURCES)  # 세 소스를 모두 재 본다
    assert "FIRMS source" in capsys.readouterr().out


def test_picks_source_with_most_rows_not_first_nonempty(monkeypatch, tmp_path):
    """부분만 채워진 첫 소스를 그대로 발행하지 않는다 (02:51Z 26건 실측 형태)."""
    one_row = HEADER + "\n9.0,9.0,300.0,0.4,0.4,2026-07-30,0212,n,1.0"
    _responses(monkeypatch, {
        cf.SOURCES[0]: one_row,              # 갓 채워지기 시작 — 비어 있지 않다
        cf.SOURCES[1]: HEADER + TWO_ROWS,    # 더 많다
    })
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    out = tmp_path / "active-fires.json"
    assert cf.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert (data["source"], data["count"]) == (cf.SOURCES[1], 2)


def test_cap_publishes_strongest_and_says_so(monkeypatch, tmp_path, capsys):
    """상한을 넘으면 FRP 상위만 발행하고, 잘렸다는 사실·기준을 payload 에 적는다."""
    rows = "".join(
        f"\n{i}.0,2.0,300.0,0.4,0.4,2026-07-30,0212,n,{frp}"
        for i, frp in enumerate([1.0, 99.0, 50.0])
    )
    _responses(monkeypatch, {cf.SOURCES[0]: HEADER + rows})
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    monkeypatch.setattr(cf, "MAX_PUBLISHED", 2)
    out = tmp_path / "active-fires.json"
    assert cf.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [f["frp"] for f in data["fires"]] == [99.0, 50.0]  # 약한 1.0 이 잘린다
    assert (data["count"], data["totalDetections"], data["capped"]) == (2, 3, True)
    assert data["minFrpPublished"] == 50.0
    assert "FIRMS capped" in capsys.readouterr().out


def test_no_cap_reports_full_set_honestly(monkeypatch, tmp_path):
    """상한 아래면 capped=False 이고 count == totalDetections 다."""
    _responses(monkeypatch, {cf.SOURCES[0]: HEADER + TWO_ROWS})
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    out = tmp_path / "active-fires.json"
    assert cf.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert (data["count"], data["totalDetections"]) == (2, 2)
    assert data["capped"] is False and data["minFrpPublished"] is None


def test_cap_keeps_payload_under_the_bucket_limit():
    """20000 × 실측 98.8 bytes/건 ≈ 2.0MB — wind-data 버킷 5MB 한도 아래."""
    assert cf.MAX_PUBLISHED * 100 < 5 * 1024 * 1024


def test_day_range_covers_more_than_the_current_utc_day(tmp_path):
    """day_range=1 은 현재 UTC 일자라 00:15Z cron 이 구조적으로 빈 스냅샷을 만든다."""
    assert cf.DAY_RANGE >= 2


def test_transport_failure_on_first_source_soft_skips(monkeypatch, tmp_path, capsys):
    """네트워크가 죽으면 소스를 더 시도하지 않는다 — job timeout 을 태우지 않는다."""
    calls = _responses(monkeypatch, dict.fromkeys(cf.SOURCES, OSError("[Errno 101] Network is unreachable")))
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    out = tmp_path / "active-fires.json"
    assert cf.main(["--out", str(out)]) == 0  # soft-skip: cron 을 죽이지 않는다
    assert not out.exists()                   # 이전 데이터 유지
    assert "soft-skip" in capsys.readouterr().out
    assert set(calls) == {cf.SOURCES[0]}
    assert len(calls) == cf.MAX_ATTEMPTS


def test_4xx_fails_fast_without_trying_other_sources(monkeypatch, tmp_path, capsys):
    """키 결함은 모든 소스에 같이 적용된다 — 폴백으로 가리지 않는다."""
    calls = _responses(monkeypatch, dict.fromkeys(cf.SOURCES, _http_error(400, "Invalid MAP_KEY.")))
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    assert cf.main(["--out", str(tmp_path / "o.json")]) == 1
    captured = capsys.readouterr()
    assert "config fault" in captured.out
    assert "Invalid MAP_KEY." in captured.err  # NASA 본문이 보여야 진단이 된다
    assert calls == [cf.SOURCES[0]]


def test_5xx_retries_then_soft_skips(monkeypatch, tmp_path, capsys):
    """FIRMS 야간 창의 502 는 cron 을 빨갛게 만들지 않는다."""
    calls = _responses(monkeypatch, dict.fromkeys(cf.SOURCES, _http_error(502)))
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_KEY)
    assert cf.main(["--out", str(tmp_path / "o.json")]) == 0
    assert len(calls) == cf.MAX_ATTEMPTS
    assert "soft-skip" in capsys.readouterr().out


def test_unparseable_rows_are_dropped_not_counted():
    """좌표를 못 읽는 행은 버린다 — 그 침묵이 0건 사고의 통로였으므로 수를 노출한다."""
    text = HEADER + "\n,,,,,,,,\nnot-a-number,2.5,300,0.4,0.4,2026-07-30,0212,n,1.0" + TWO_ROWS
    fires, raw_rows, fieldnames = cf.parse_fires(text)
    assert len(fires) == 2
    assert raw_rows == 4          # 버린 2행도 세어 보고한다
    assert "latitude" in fieldnames


def test_modis_brightness_column_is_accepted():
    """MODIS 는 brightness, VIIRS 는 bright_ti4 — 폴백이 컬럼 차이에 걸리지 않는다."""
    text = "latitude,longitude,brightness,acq_date,confidence,frp\n1.0,2.0,305.5,2026-07-30,50,7.7"
    fires, _rows, _cols = cf.parse_fires(text)
    assert fires[0]["brightness"] == 305.5
