"""check_aq_grid_freshness.py 단위 테스트 (AAA). 네트워크 제외 — evaluate() 는 순수 함수.

이 파일의 존재 이유: wind 프로브는 워크플로 안 heredoc 이라 **고장 나도 아무도 모른다**.
프로브가 예외를 삼키고 항상 0 을 반환하게 되는 순간 green 이 계속 찍히고, 그 상태는
"수집이 멈췄는데 green" 과 겉모습이 같다. 그래서 red 를 실증한다 — 각 실패 사유마다
실제로 ok=False 가 나오는지, 그리고 정상값은 통과하는지 둘 다.
"""
from datetime import datetime, timedelta, timezone

import check_aq_grid_freshness as m

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

def AQ(slug: str) -> m.GridSpec:
    """aq-data 쪽 스펙 조회. 키가 (product, slug) 라 slug 단독으로는 못 찾는다."""
    return m.SPEC_BY_KEY[("aq-data", slug)]


def AQ_SPECS(*slugs) -> tuple:
    return tuple(AQ(s) for s in slugs)



def _payload(generated_at="2026-09-04T11:00:00Z", n_lat=2, n_lon=3, n_points=6):
    return {
        "schemaVersion": "1.0",
        "variable": "pm2_5",
        "generatedAt": generated_at,
        "timestamp": 1788480000000,
        "nLat": n_lat,
        "nLon": n_lon,
        "points": [{"lat": 0.0, "lon": float(i), "value": 5.0} for i in range(n_points)],
    }


def test_fresh_grid_passes():
    # Arrange — 1시간 전 발행 (SLA 6h 이내)
    payload = _payload()
    # Act
    ok, lines = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is True
    assert any("AQ grid fresh" in ln for ln in lines)
    assert not any(ln.startswith("::error") for ln in lines)


def test_stale_grid_fails_loud():
    # Arrange — 7시간 전 = 두 사이클(3h) 연속 결손 초과
    payload = _payload(generated_at=(NOW - timedelta(hours=7)).isoformat().replace("+00:00", "Z"))
    # Act
    ok, lines = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert — 조용히 넘어가면 옛 격자가 계속 발행된다
    assert ok is False
    assert any(ln.startswith("::error title=AQ grid stale") for ln in lines)


def test_boundary_at_exactly_the_sla_is_not_stale():
    # Arrange — 정확히 SLA 경계. 여기서 red 를 내면 정상 주기에서 산발적으로 빨개진다.
    payload = _payload(generated_at=(NOW - timedelta(hours=6)).isoformat().replace("+00:00", "Z"))
    # Act
    ok, _ = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is True


def test_missing_generated_at_fails():
    # Arrange — 계약의 required 필드가 빠졌다 = 발행 경로가 옛 생산자로 돌아간 신호.
    # 여기서 예외를 삼키고 통과시키면 프로브가 아무것도 지키지 않게 된다.
    payload = _payload()
    del payload["generatedAt"]
    # Act
    ok, lines = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is False
    assert any("generatedAt" in ln and "없다" in ln for ln in lines)


def test_malformed_generated_at_fails():
    # Arrange
    payload = _payload(generated_at="not-a-timestamp")
    # Act
    ok, lines = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is False
    assert any("형식 오류" in ln for ln in lines)


def test_broken_grid_shape_fails_even_when_fresh():
    # Arrange — 신선하지만 격자가 깨졌다 (nLat*nLon != len(points)).
    # 신선도만 보고 통과시키면 "방금 만든 쓰레기" 를 놓친다.
    payload = _payload(n_lat=2, n_lon=3, n_points=5)
    # Act
    ok, lines = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is False
    assert any(ln.startswith("::error title=AQ grid shape") for ln in lines)


def test_null_valued_cells_do_not_break_the_shape_check():
    # Arrange — 음수 결측 처리(nullify_impossible_negatives)는 엔트리를 지우지 않고
    # value 만 null 로 만든다. 셀 수 검사가 그것을 깨진 격자로 오판하면 안 된다.
    payload = _payload()
    payload["points"][0]["value"] = None
    # Act
    ok, _ = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is True


def test_naive_timestamp_is_read_as_utc():
    # Arrange — tz 표기가 빠진 값. 로컬 시간으로 읽으면 나이가 9시간 틀어진다(KST).
    payload = _payload(generated_at="2026-09-04T11:00:00")
    # Act
    ok, lines = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is True
    assert any("age=1.0h" in ln for ln in lines)


def test_probe_treats_404_as_baseline_pending_not_failure(monkeypatch):
    # Arrange — 최초 발행 전에는 404 가 정상이다. 실패로 만들면 새 격자를 추가할
    # 때마다 첫 run 이 빨개진다 (wind 프로브와 같은 카브아웃).
    monkeypatch.setattr(m, "fetch", lambda url, timeout=60: None)
    # Act
    rc = m.probe("https://example.invalid", AQ_SPECS("current-pm25-grid"), 6.0, now=NOW)
    # Assert
    assert rc == 0


def test_probe_fails_when_fetch_raises(monkeypatch):
    # Arrange — 네트워크/파싱 실패를 조용히 넘기면 프로브가 있으나 마나가 된다
    def boom(url, timeout=60):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(m, "fetch", boom)
    # Act
    rc = m.probe("https://example.invalid", AQ_SPECS("current-pm25-grid"), 6.0, now=NOW)
    # Assert
    assert rc == 1


def test_probe_exit_code_reflects_a_single_stale_slug(monkeypatch):
    # Arrange — 하나만 노후해도 run 은 빨개야 한다 (pm10 만 멈추는 사고 실재)
    stale = _payload(generated_at=(NOW - timedelta(hours=30)).isoformat().replace("+00:00", "Z"))
    fresh = _payload()
    monkeypatch.setattr(
        m, "fetch", lambda url, timeout=60: stale if "pm10" in url else fresh
    )
    # Act
    rc = m.probe("https://example.invalid", AQ_SPECS("current-pm25-grid", "current-pm10-grid"), 6.0, now=NOW)
    # Assert
    assert rc == 1


def test_probe_green_when_all_slugs_fresh(monkeypatch):
    # Arrange/Act — 정상 경로가 실제로 0 을 내는지. 이게 없으면 위 red 테스트들이
    # "항상 1 을 반환하는 프로브" 로도 전부 통과한다.
    monkeypatch.setattr(m, "fetch", lambda url, timeout=60: _payload())
    rc = m.probe("https://example.invalid", AQ_SPECS("current-pm25-grid", "current-pm10-grid"), 6.0, now=NOW)
    # Assert
    assert rc == 0


# ── 슬러그별 스펙 (o3/no2/co/pollen 확장, 2026-09-04) ───────────────────────
#
# 이 격자들은 "generatedAt 이 없어 나이를 잴 수 없다" 고 기록돼 있었으나 실측 결과
# 각자 다른 이름으로 수집 시각을 싣고 있었다 — 가스 격자는 `timestamp`(epoch ms,
# collect_all.py 가 `int(now.timestamp()*1000)` 로 쓴다), pollen 은 `collected_at`.
# 필드 이름이 다르다고 부재로 단정한 오판이었다.

def _gas_payload(ts_ms=None, n_lat=33, n_lon=72, n_points=2196):
    if ts_ms is None:
        ts_ms = int((NOW - timedelta(hours=1)).timestamp() * 1000)
    return {
        "variable": "ozone",
        "timestamp": ts_ms,
        "nLat": n_lat,
        "nLon": n_lon,
        "points": [{"lat": 0.0, "lon": float(i), "value": 5.0} for i in range(n_points)],
    }


def _pollen_payload(collected_at="2026-09-04T11:00:00Z", count=None, n_points=461):
    return {
        "refTime": collected_at,
        "collected_at": collected_at,
        "count": n_points if count is None else count,
        "points": [{"lat": 34.0, "lon": float(i), "grass": 0.0} for i in range(n_points)],
    }


def test_gas_grid_age_comes_from_its_own_timestamp_field():
    # Arrange — 가스 격자에는 generatedAt 이 없다. 그것으로 나이를 재려 하면 못 잰다.
    payload = _gas_payload()
    # Act
    ok, lines = m.evaluate(AQ("current-o3-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is True
    assert any("age=1.0h" in ln for ln in lines)


def test_pm_grid_never_reads_age_from_timestamp():
    # Arrange — 같은 이름, 다른 양. PM 격자의 timestamp 는 **데이터 사이클 시각**이라
    # 수집이 멈춰도 갱신된다. 한 필드로 뭉뚱그렸다면 이 payload 가 통과했을 것이다:
    # timestamp 는 방금이지만 generatedAt 은 이틀 전이다.
    payload = _payload(generated_at="2026-09-02T12:00:00Z")
    payload["timestamp"] = int(NOW.timestamp() * 1000)
    # Act
    ok, lines = m.evaluate(AQ("current-pm25-grid"), payload, NOW, 6.0)
    # Assert — 신선한 timestamp 에 속지 않는다.
    assert ok is False
    assert any(ln.startswith("::error title=AQ grid stale") for ln in lines)


def test_sparse_gas_grid_is_not_a_broken_grid():
    # Arrange — 실측(2026-09-04) 2196 / 2376. Open-Meteo 가 값을 못 주는 셀은
    # points 에 들어가지 않는다. PM 의 조밀 등식을 씌웠다면 매 실행 오탐이 났다.
    payload = _gas_payload(n_points=2196)
    # Act
    ok, _ = m.evaluate(AQ("current-o3-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is True


def test_gas_grid_with_more_points_than_cells_fails():
    # Arrange — 희소는 허용하되 셀 수 초과는 격자 정의와 payload 의 불일치다.
    payload = _gas_payload(n_points=2377)
    # Act
    ok, lines = m.evaluate(AQ("current-o3-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is False
    assert any("초과" in ln for ln in lines)


def test_empty_points_is_not_a_fresh_publish():
    # Arrange — 방금 쓴 빈 파일이 나이만 보면 가장 신선해 보인다.
    payload = _gas_payload(n_points=0)
    # Act
    ok, lines = m.evaluate(AQ("current-o3-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is False
    assert any("0개" in ln for ln in lines)


def test_epoch_seconds_mistaken_for_millis_is_loud():
    # Arrange — 초 단위를 ms 로 읽으면 1970년대로 떨어진다. 조용히 통과하면 안 된다.
    payload = _gas_payload(ts_ms=int(NOW.timestamp()))
    # Act
    ok, lines = m.evaluate(AQ("current-o3-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is False
    assert any(ln.startswith("::error title=AQ grid stale") for ln in lines)


def test_pollen_uses_collected_at_and_its_declared_count():
    # Arrange — pollen 은 nLat/nLon 자체가 없다(count + points). 격자 검사를
    # 그대로 씌웠다면 "계약 형태가 아니다" 로 매번 실패했다.
    payload = _pollen_payload()
    # Act
    ok, lines = m.evaluate(AQ("pollen-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is True
    assert any("age=1.0h" in ln for ln in lines)


def test_pollen_count_mismatch_fails():
    # Arrange — 스스로 신고한 수와 실제 길이가 다르면 발행물이 자기 자신과 어긋난 것.
    payload = _pollen_payload(count=999)
    # Act
    ok, lines = m.evaluate(AQ("pollen-grid"), payload, NOW, 6.0)
    # Assert
    assert ok is False
    assert any("신고한 수와 실제가 다르다" in ln for ln in lines)


def test_every_uploaded_grid_has_a_spec():
    # Arrange — 워크플로가 업로드하는 목록과 프로브가 보는 목록이 갈라지면, 새 격자가
    # 추가돼도 게이트 밖에 남는다(이번 갭이 정확히 그렇게 생겼다).
    from pathlib import Path
    import re

    wf = Path(__file__).resolve().parents[2] / ".github/workflows/data-collect-hourly.yml"
    line = next(ln for ln in wf.read_text().splitlines() if "for FILE in current-pm25-grid" in ln)
    uploaded = re.search(r"for FILE in (.+?); do", line).group(1).split()
    probed = {s.slug for s in m.specs_for("aq-data")}
    # Act / Assert
    assert set(uploaded) == probed, f"업로드 {uploaded} vs 프로브 {sorted(probed)}"


def test_every_web_v1_grid_written_has_a_spec():
    # Arrange — web/v1 은 파일 목록이 아니라 디렉터리째 올라가므로, 업로드 줄과
    # 대조할 수 없다. 대신 **생산자가 실제로 쓰는 파일명**과 대조한다.
    # (같은 갭이 여기서 다시 생기는 것을 막는다 — 이 경로는 신선도 프로브가
    # 아예 없던 자리다.)
    from pathlib import Path
    import re

    builder = (Path(__file__).resolve().parent / "build_web_aq_grid.py").read_text()
    written = set(re.findall(r"current-(?:pm25|pm10)-grid", builder))
    probed = {s.slug for s in m.specs_for("web-v1")}

    # Assert — 추출이 깨지면 vacuous pass 이므로 비어 있지 않은 것부터 확인한다.
    assert written, "build_web_aq_grid.py 에서 격자 파일명을 못 찾았다 — 추출이 깨졌다"
    assert written == probed, f"생산 {sorted(written)} vs 프로브 {sorted(probed)}"


def test_same_slug_in_two_products_gets_different_specs():
    # Arrange / Act — 이 프로브가 (product, slug) 키를 쓰는 이유 그 자체.
    aq = m.SPEC_BY_KEY[("aq-data", "current-pm25-grid")]
    web = m.SPEC_BY_KEY[("web-v1", "current-pm25-grid")]

    # Assert — 같은 파일명, 다른 제품: 나이 필드도 형태도 다르다.
    assert (aq.age_field, aq.shape) == ("generatedAt", "dense")
    assert (web.age_field, web.shape) == ("timestamp", "sparse")


def test_web_v1_downsampled_grid_is_not_a_broken_grid():
    # Arrange — aq-data 의 조밀 등식을 씌웠다면 이 payload 는 매번 빨개진다.
    # 실측 2026-09-04: web/v1 은 37×72 격자에 points 2664.
    payload = {
        "variable": "pm2_5",
        "timestamp": 1788516367000,   # 2026-09-04T10:06:07Z
        "nLat": 37, "nLon": 72,
        "points": [{"lat": 0.0, "lon": float(i), "value": 1.0} for i in range(2000)],
    }
    now = datetime(2026, 9, 4, 11, 0, 0, tzinfo=timezone.utc)

    # Act
    ok, lines = m.evaluate(m.SPEC_BY_KEY[("web-v1", "current-pm25-grid")], payload, now, 12.0)

    # Assert
    assert ok is True, lines


def test_web_v1_stale_timestamp_fails_loud():
    # Arrange — 변환 파이프라인이 멈추면 timestamp 가 얼어붙는다(원본 generatedAt 보존).
    payload = {
        "variable": "pm2_5",
        "timestamp": 1788400000000,   # 2026-09-03T01:46:40Z
        "nLat": 37, "nLon": 72,
        "points": [{"lat": 0.0, "lon": 0.0, "value": 1.0}],
    }
    now = datetime(2026, 9, 4, 11, 0, 0, tzinfo=timezone.utc)

    # Act
    ok, lines = m.evaluate(m.SPEC_BY_KEY[("web-v1", "current-pm25-grid")], payload, now, 12.0)

    # Assert
    assert ok is False
    assert any("AQ grid stale" in ln for ln in lines)


def test_each_workflow_pins_the_product_it_probes():
    # Arrange — 두 제품은 발행 주체가 다르다. 제품을 안 지정하면 한 워크플로가
    # 남의 파이프라인 고장으로 빨개진다(#20 에서 끊어낸 결합의 프로브 층 재현).
    from pathlib import Path

    wf_dir = Path(__file__).resolve().parents[2] / ".github/workflows"
    expected = {
        "data-collect-hourly.yml": "--product aq-data",
        "mac-data-publish.yml": "--product web-v1",
    }

    # Act / Assert
    for filename, flag in expected.items():
        invocations = [
            ln for ln in (wf_dir / filename).read_text().splitlines()
            if "python3 scripts/etl/check_aq_grid_freshness.py" in ln
        ]
        assert invocations, f"{filename} 에 프로브 호출이 없다 — 추출이 깨졌다(vacuous pass 방지)"
        for line in invocations:
            assert flag in line, f"{filename} 의 프로브가 제품을 지정하지 않았다: {line.strip()}"


def test_a_product_with_no_specs_fails_instead_of_reporting_clean(monkeypatch):
    # Arrange — 스펙이 사라진 제품을 "위반 0건"으로 읽으면 vacuous green 이다.
    # (검사한 게 없는 것과 문제가 없는 것은 다르다.)
    monkeypatch.setitem(m.PRODUCTS, "ghost", (lambda: "https://example.invalid", "X_HOURS", 6.0))

    # Act
    rc = m.main(["--product", "ghost"])

    # Assert
    assert rc == 2


def test_unknown_product_is_a_loud_configuration_error():
    # Arrange / Act — 오타난 --product 가 "검사할 게 없었다 → green" 이 되면 안 된다.
    rc = m.main(["--product", "aq-daat"])

    # Assert
    assert rc == 2


# ── 격자별 SLA 오버라이드 (AQ 호스트 예산 게이트, 2026-09-05) ────────────────
#
# 가스·pollen 이 12h 케이던스가 되면서 제품 기본 6h 자로는 매 run 오탐이 된다.
# 오버라이드가 스펙에 *있는* 것과 probe 가 그걸 *쓰는* 것은 다른 사실이다 —
# 둘 다 실증한다.

def test_gas_and_pollen_specs_carry_the_24h_sla_override():
    # Arrange / Act / Assert — 12h 케이던스 × 2사이클 = 24h.
    for slug in ("current-o3-grid", "current-no2-grid", "current-co-grid", "pollen-grid"):
        assert AQ(slug).sla_hours == 24.0, f"{slug} SLA 오버라이드가 없다"
    # PM 격자는 3h 케이던스 그대로 — 오버라이드가 번지면 진짜 고장을 24h 동안 놓친다.
    for slug in ("current-pm25-grid", "current-pm10-grid"):
        assert AQ(slug).sla_hours is None, f"{slug} 에 오버라이드가 번졌다"


def test_sla_for_prefers_the_spec_override_over_the_product_default():
    # Arrange
    gas = AQ("current-o3-grid")
    pm = AQ("current-pm25-grid")
    # Act / Assert
    assert m.sla_for(gas, 6.0) == 24.0
    assert m.sla_for(pm, 6.0) == 6.0


def test_probe_judges_a_13h_old_gas_grid_fresh_under_its_own_sla(monkeypatch):
    # Arrange — 13h 는 6h 기본값으론 stale, 24h 오버라이드론 fresh.
    # 이 테스트가 red 면 probe 가 오버라이드를 무시하고 제품 기본값으로 재는 것이다
    # (= 예산 게이트가 매 run 오탐을 만든다).
    ts_ms = int((NOW - timedelta(hours=13)).timestamp() * 1000)
    monkeypatch.setattr(m, "fetch", lambda url, timeout=60: _gas_payload(ts_ms=ts_ms))
    # Act
    rc = m.probe("https://example.invalid", AQ_SPECS("current-o3-grid"), 6.0, now=NOW)
    # Assert
    assert rc == 0


def test_probe_still_fails_a_gas_grid_older_than_its_own_sla(monkeypatch):
    # Arrange — 25h > 24h 오버라이드. 오버라이드가 "무한 면제"로 굳으면 안 된다.
    ts_ms = int((NOW - timedelta(hours=25)).timestamp() * 1000)
    monkeypatch.setattr(m, "fetch", lambda url, timeout=60: _gas_payload(ts_ms=ts_ms))
    # Act
    rc = m.probe("https://example.invalid", AQ_SPECS("current-o3-grid"), 6.0, now=NOW)
    # Assert
    assert rc == 1
