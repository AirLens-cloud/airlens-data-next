"""`openmeteo_chunks` 단위 테스트 (AAA).

고정하는 것 두 가지 — 둘 다 2026-09-04 실측 사고에서 나왔다:
- 실패한 청크가 **커버리지에 남는다** (감쇠가 완전본으로 위장하지 못하게).
- 스로틀에는 **재시도하지 않고**, 연속되면 남은 청크를 포기한다 (예산을 뒤
  블록에 넘겨, pollen 이 구조적으로 굶는 것을 끝낸다).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openmeteo_chunks import (  # noqa: E402
    GIVE_UP_AFTER_CONSECUTIVE_THROTTLES,
    coverage_note,
    fetch_chunks,
)


class _Throttled(Exception):
    """`urllib.error.HTTPError` 처럼 `.code` 를 들고 있는 최소 대역."""

    def __init__(self, code: int = 429) -> None:
        super().__init__(f"HTTP Error {code}")
        self.code = code


def _urls(n: int) -> list[str]:
    return [f"https://example.invalid/chunk/{i}" for i in range(n)]


def _ok(url: str) -> bytes:
    return json.dumps({"url": url}).encode()


def test_all_chunks_ok_reports_full_coverage():
    # Arrange
    urls = _urls(4)

    # Act
    payloads, coverage = fetch_chunks("T", urls, opener=_ok, sleep=lambda _s: None, log=lambda _m: None)

    # Assert
    assert len(payloads) == 4
    assert coverage == {"nChunksTotal": 4, "nChunksOk": 4, "nChunksFailed": 0, "throttled": False}


def test_a_failing_chunk_is_counted_not_silently_dropped():
    # Arrange — 두 번째 청크만 계속 죽는다(스로틀 아님 → 재시도 대상).
    def opener(url: str) -> bytes:
        if url.endswith("/1"):
            raise OSError("connection reset")
        return _ok(url)

    # Act
    payloads, coverage = fetch_chunks("T", _urls(3), opener=opener, sleep=lambda _s: None, log=lambda _m: None)

    # Assert — 나머지는 계속 수집하되, 빠진 한 청크가 발행물에 드러난다.
    assert len(payloads) == 2
    assert coverage["nChunksOk"] == 2
    assert coverage["nChunksFailed"] == 1
    assert coverage["throttled"] is False


def test_throttled_chunk_is_not_retried():
    # Arrange — 429 는 재시도가 완화가 아니라 가중이다.
    calls: list[str] = []

    def opener(url: str) -> bytes:
        calls.append(url)
        raise _Throttled(429)

    # Act
    _payloads, _coverage = fetch_chunks("T", _urls(1), opener=opener, sleep=lambda _s: None, log=lambda _m: None)

    # Assert — attempts=3 이어도 호출은 1회뿐.
    assert calls == ["https://example.invalid/chunk/0"]


def test_sustained_throttle_gives_up_the_rest_of_the_block():
    # Arrange — 전량 429. 예산을 뒤 블록에 넘기는 것이 이 정책의 목적이다.
    calls: list[str] = []

    def opener(url: str) -> bytes:
        calls.append(url)
        raise _Throttled(429)

    # Act
    payloads, coverage = fetch_chunks("T", _urls(20), opener=opener, sleep=lambda _s: None, log=lambda _m: None)

    # Assert — 연속 임계값에서 멈추고, 시도조차 안 한 청크도 실패로 남는다.
    assert payloads == []
    assert len(calls) == GIVE_UP_AFTER_CONSECUTIVE_THROTTLES
    assert coverage["throttled"] is True
    assert coverage["nChunksFailed"] == 20
    assert coverage["nChunksOk"] == 0


def test_isolated_throttles_do_not_give_up():
    # Arrange — 성공이 사이에 끼면 스로틀은 "이어진" 것이 아니다.
    # (단발 429 하나에 격자 전체를 버리면 이 변경이 새 사고가 된다.)
    def opener(url: str) -> bytes:
        if url.endswith(("/1", "/3", "/5")):
            raise _Throttled(429)
        return _ok(url)

    # Act
    payloads, coverage = fetch_chunks("T", _urls(7), opener=opener, sleep=lambda _s: None, log=lambda _m: None)

    # Assert — 끝까지 돌면서 실패 3개만 공시한다.
    assert len(payloads) == 4
    assert coverage["nChunksFailed"] == 3
    assert coverage["throttled"] is False


class _MinutelyThrottled(Exception):
    """본문이 분당 한도를 명시하는 429 대역 (2026-09-05 force run 실측 본문)."""

    def __init__(self) -> None:
        super().__init__("HTTP Error 429")
        self.code = 429
        self._body = b'{"error":true,"reason":"Minutely API request limit exceeded. Please try again in one minute."}'

    def read(self) -> bytes:
        return self._body


def test_minutely_throttle_waits_and_retries_same_chunk():
    # Arrange — 첫 시도는 분당 한도 429, 두 번째는 성공 (한도가 1분 뒤 풀린 상황).
    calls: list[str] = []
    sleeps: list[float] = []

    def opener(url: str) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            raise _MinutelyThrottled()
        return _ok(url)

    # Act
    payloads, coverage = fetch_chunks("T", _urls(1), opener=opener, sleep=sleeps.append, log=lambda _m: None)

    # Assert — 같은 청크를 61s 대기 후 재시도해 성공, 실패 0.
    assert len(payloads) == 1
    assert calls == ["https://example.invalid/chunk/0"] * 2
    assert 61 in sleeps
    assert coverage == {"nChunksTotal": 1, "nChunksOk": 1, "nChunksFailed": 0, "throttled": False}


def test_minutely_wait_budget_exhaustion_falls_back_to_give_up():
    # Arrange — 계속 분당 한도라고 우기는 429 (사실상 안 풀리는 상황).
    def opener(url: str) -> bytes:
        raise _MinutelyThrottled()

    # Act
    payloads, coverage = fetch_chunks("T", _urls(20), opener=opener, sleep=lambda _s: None, log=lambda _m: None)

    # Assert — 대기 예산 소진 후엔 기존 포기 정책으로 수렴 (예산 무한 소모 방지).
    assert payloads == []
    assert coverage["throttled"] is True
    assert coverage["nChunksOk"] == 0
    assert coverage["nChunksFailed"] == 20


def test_non_minutely_throttle_body_is_not_waited_on():
    # Arrange — Hourly/Daily 소진은 기다려도 안 풀린다: 기존 무재시도 유지.
    calls: list[str] = []

    class _DailyThrottled(Exception):
        def __init__(self) -> None:
            super().__init__("HTTP Error 429")
            self.code = 429

        def read(self) -> bytes:
            return b'{"error":true,"reason":"Daily API request limit exceeded."}'

    def opener(url: str) -> bytes:
        calls.append(url)
        raise _DailyThrottled()

    # Act
    _payloads, coverage = fetch_chunks("T", _urls(1), opener=opener, sleep=lambda _s: None, log=lambda _m: None)

    # Assert — 호출 1회뿐, 실패로 공시.
    assert len(calls) == 1
    assert coverage["nChunksFailed"] == 1


def test_503_counts_as_throttle():
    # Arrange / Act — 503 도 "빨리 다시 오라"는 뜻이 아니다.
    calls: list[str] = []

    def opener(url: str) -> bytes:
        calls.append(url)
        raise _Throttled(503)

    _payloads, coverage = fetch_chunks("T", _urls(1), opener=opener, sleep=lambda _s: None, log=lambda _m: None)

    # Assert
    assert len(calls) == 1
    assert coverage["nChunksFailed"] == 1


def test_non_throttle_failure_still_retries():
    # Arrange — 스로틀이 아닌 실패까지 1회로 줄이면 일시적 오류에 취약해진다.
    calls: list[str] = []

    def opener(url: str) -> bytes:
        calls.append(url)
        raise OSError("timed out")

    # Act
    fetch_chunks("T", _urls(1), attempts=3, opener=opener, sleep=lambda _s: None, log=lambda _m: None)

    # Assert
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        ({"nChunksTotal": 4, "nChunksOk": 4, "nChunksFailed": 0, "throttled": False}, None),
        ({"nChunksTotal": 4, "nChunksOk": 3, "nChunksFailed": 1, "throttled": False}, "3/4"),
        ({"nChunksTotal": 4, "nChunksOk": 1, "nChunksFailed": 3, "throttled": True}, "스로틀로 조기 중단"),
    ],
)
def test_coverage_note_speaks_only_when_something_is_missing(coverage, expected):
    # Act
    note = coverage_note("Gas grid", coverage)

    # Assert — 정상까지 경고하면 경고가 배경 소음이 된다.
    if expected is None:
        assert note is None
    else:
        assert note is not None
        assert note.startswith("::warning title=Gas grid partial::")
        assert expected in note
