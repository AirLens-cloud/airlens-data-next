"""`aq_budget_gate` 단위 테스트 (AAA).

고정하는 것: 게이트가 **run 이 도착한 시각**이 아니라 **발행물 나이**로 판정한다.
2026-09-22~27 실측 사고 — schedule 이벤트가 00Z·12Z 창을 계속 빗나가 가스·pollen 이
141h 노후 — 가 첫 테스트다.
"""
from __future__ import annotations

import math
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aq_budget_gate import (
    GAS_SLUGS,
    MIN_RECOLLECT_AGE_HOURS,
    aq_host_budget_run,
    decide,
    published_gas_age_hours,
)

NOW = datetime(2026, 9, 27, 21, 15, tzinfo=timezone.utc)


def _gas_payload(age_hours: float) -> dict:
    ts = NOW - timedelta(hours=age_hours)
    return {"timestamp": int(ts.timestamp() * 1000), "points": [{"lat": 0, "lon": 0, "value": 1.0}]}


def _fetcher(by_slug: dict):
    """slug → payload | None(404) | Exception 인스턴스(읽기 실패)."""
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        slug = url.rsplit("/", 1)[-1].removesuffix(".json")
        value = by_slug[slug]
        if isinstance(value, Exception):
            raise value
        return value

    fetch.calls = calls
    return fetch


def test_stale_publication_is_collected_even_outside_the_old_clock_window():
    # Arrange — 21Z run(옛 게이트 창 밖), 발행물 141h 노후 = 09-27 실측 상태
    fetch = _fetcher({slug: _gas_payload(141.3) for slug in GAS_SLUGS})
    # Act
    due, reason = aq_host_budget_run(NOW, force=False, fetcher=fetch)
    # Assert
    assert due, reason


def test_fresh_publication_is_skipped_even_inside_the_old_clock_window():
    # Arrange — 00Z 창 안이지만 3h 전에 이미 수집됨 → 예산 보호가 이긴다
    at_midnight = NOW.replace(hour=0, minute=40)
    fetch = _fetcher({slug: {"timestamp": int((at_midnight - timedelta(hours=3)).timestamp() * 1000)}
                      for slug in GAS_SLUGS})
    # Act
    due, _ = aq_host_budget_run(at_midnight, force=False, fetcher=fetch)
    # Assert
    assert not due


def test_threshold_boundary():
    # Arrange / Act / Assert — 경계 포함(≥), 바로 아래는 skip
    assert decide(MIN_RECOLLECT_AGE_HOURS, NOW, force=False)[0]
    assert not decide(MIN_RECOLLECT_AGE_HOURS - 0.01, NOW, force=False)[0]


def test_threshold_keeps_daily_budget_under_quota():
    # Arrange — 좌표 가중: 가스 2,376 + pollen 580, forecast-collect 75×4. 일 한도 ~10k
    per_collection, forecast, daily_limit = 2376 + 580, 75 * 4, 10_000
    # Act — 24h 안에 들어갈 수 있는 최대 수집 횟수
    max_per_day = math.floor(24 / MIN_RECOLLECT_AGE_HOURS) + 1
    # Assert
    assert max_per_day * per_collection + forecast < daily_limit


def test_newest_gas_grid_decides_so_one_empty_variable_cannot_loop_the_budget():
    # Arrange — CO 만 늘 비어 파일이 오래됨. 가장 오래된 것을 기준으로 하면 매 run 수집한다
    fetch = _fetcher({"current-o3-grid": _gas_payload(2.0),
                      "current-no2-grid": _gas_payload(2.0),
                      "current-co-grid": _gas_payload(300.0)})
    # Act
    age = published_gas_age_hours(NOW, fetcher=fetch)
    # Assert
    assert age == 2.0


def test_force_skips_the_read_entirely():
    # Arrange
    fetch = _fetcher({})
    # Act
    due, _ = aq_host_budget_run(NOW, force=True, fetcher=fetch)
    # Assert
    assert due and fetch.calls == []


def test_all_unpublished_bootstraps_collection():
    # Arrange
    fetch = _fetcher({slug: None for slug in GAS_SLUGS})
    # Act
    due, _ = aq_host_budget_run(NOW, force=False, fetcher=fetch)
    # Assert
    assert published_gas_age_hours(NOW, fetcher=fetch) == math.inf
    assert due


def test_read_failure_falls_back_to_the_clock_window():
    # Arrange — HF 장애. "못 읽었으니 수집" 이면 장애 내내 매 run 예산을 태운다
    boom = urllib.error.URLError("hf down")
    fetch = _fetcher({slug: boom for slug in GAS_SLUGS})
    # Act
    outside, _ = aq_host_budget_run(NOW, force=False, fetcher=fetch)            # 21Z
    inside, _ = aq_host_budget_run(NOW.replace(hour=12), force=False, fetcher=fetch)
    # Assert
    assert published_gas_age_hours(NOW, fetcher=fetch) is None
    assert (outside, inside) == (False, True)


def test_one_unreadable_grid_does_not_block_the_decision():
    # Arrange — 한 격자는 형식 오류, 나머지는 정상
    fetch = _fetcher({"current-o3-grid": {"timestamp": "not-ms"},
                      "current-no2-grid": _gas_payload(20.0),
                      "current-co-grid": _gas_payload(20.0)})
    # Act
    due, _ = aq_host_budget_run(NOW, force=False, fetcher=fetch)
    # Assert
    assert due
