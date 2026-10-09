"""probe_mac_snapshot.py 단위 테스트 (AAA). 네트워크 없음 — index 문서 fixture.

핵심 회귀: `available: true` + `servedFrom: "baseline"` 은 겉보기엔 정상인데
**수집기가 죽은 채 지난 파일로 버티는 중**이다. 이걸 정상으로 읽으면 만성 실패가
그대로 묻힌다 — CAMS 가 며칠간 그 상태였다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import probe_mac_snapshot as m

UTC = timezone.utc
NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
MAX_AGE = timedelta(hours=3)


def _entry(**kw) -> dict:
    base = {
        "available": True,
        "servedFrom": "fresh",
        "lastAttemptAt": "2026-07-28T11:30:00Z",
        "expiresAt": "2026-07-28T18:00:00Z",
    }
    base.update(kw)
    return base


def test_healthy_source_has_no_problems():
    # Arrange / Act / Assert
    assert m.classify_source(_entry(), NOW, MAX_AGE) == []


def test_baseline_served_is_flagged_even_though_available_is_true():
    # Arrange — 이 프로브의 존재 이유. available 만 보면 정상으로 읽힌다.
    entry = _entry(servedFrom="baseline", unavailableReason="failure")

    # Act
    problems = m.classify_source(entry, NOW, MAX_AGE)

    # Assert
    assert any("degraded" in p for p in problems)
    assert any("failure" in p for p in problems)


def test_absent_source_reports_the_recorded_reason():
    # Arrange / Act
    problems = m.classify_source(
        {"available": False, "unavailableReason": "skipped"}, NOW, MAX_AGE,
    )

    # Assert
    assert problems == ["absent (skipped)"]


def test_absent_source_without_reason_still_reported():
    # Arrange / Act / Assert
    assert m.classify_source({"available": False}, NOW, MAX_AGE) == ["absent"]


def test_expired_snapshot_is_stale():
    # Arrange / Act
    problems = m.classify_source(_entry(expiresAt="2026-07-28T11:00:00Z"), NOW, MAX_AGE)

    # Assert
    assert any("stale" in p and "expiresAt" in p for p in problems)


def test_old_last_attempt_is_stale_even_when_the_file_has_not_expired_yet():
    # Arrange — 발행이 4시간째 멈췄는데 파일은 아직 유효기간이 남은 경우. 파일만
    # 보면 정상이라 발행 파이프라인이 멈춘 걸 못 잡는다.
    entry = _entry(lastAttemptAt="2026-07-28T08:00:00Z", expiresAt="2026-07-28T18:00:00Z")

    # Act
    problems = m.classify_source(entry, NOW, MAX_AGE)

    # Assert
    assert any("마지막 시도" in p for p in problems)


def test_unparseable_timestamps_do_not_crash_the_probe():
    # Arrange — 감시 도구가 이상한 값에 죽으면 감시가 사라진다
    entry = _entry(expiresAt="not-a-date", lastAttemptAt="")

    # Act / Assert
    assert m.classify_source(entry, NOW, MAX_AGE) == []


def test_report_names_every_unhealthy_source():
    # Arrange
    findings = {"cams": ["absent (failure)"], "gefs-chem": [], "eea-utd": ["degraded (x)"]}

    # Act
    out = m.render_report(findings)

    # Assert
    assert "cams" in out and "eea-utd" in out
    assert "2/3" in out


def test_report_says_healthy_when_nothing_is_wrong():
    # Arrange / Act
    out = m.render_report({"cams": [], "gefs-chem": []})

    # Assert
    assert "✅" in out and "모두 정상" in out
