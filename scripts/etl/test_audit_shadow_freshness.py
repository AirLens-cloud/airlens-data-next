"""audit_shadow_freshness.py 단위 테스트 (AAA). 네트워크 없음.

핵심 회귀: 감사는 **끊긴 gzip 스트림**에서 헤더를 떼어낸다. 부분 해제된 텍스트는
유효한 JSON 이 아니므로(`data` 배열 중간에서 끊긴다) 전체 파싱은 실패한다 —
그 실패를 빈 헤더로 위장시키면 "노후도 0" 으로 읽힌다.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import audit_shadow_freshness as m

UTC = timezone.utc

FRESHNESS = {
    "rows": 100,
    "age_known": 90,
    "age_unknown": 10,
    "stale_fraction_24h": 0.517,
    "age_p50_h": 3.2,
    "age_p90_h": 900.0,
    "age_buckets_h": {"<=1": 10, "<=6": 30, "<=24": 5, "<=168": 10, "<=8760": 8, ">8760": 27},
    "n_locations": 60,
    "n_fresh_locations": 45,
    "sentinel_count": 7,
    "n_sentinel_locations": 5,
}


def _payload_gz(rows: int = 5000) -> bytes:
    """실제 페이로드 모양 — freshness 가 data 보다 앞, data 는 크다."""
    payload = {
        "source": "openaq",
        "ingested_at": "2026-09-03T12:00:00Z",
        "records": rows,
        "freshness": FRESHNESS,
        "data": [{"parameter": "pm25", "value": 12.3, "age_h": 1.0} for _ in range(rows)],
    }
    return gzip.compress(json.dumps(payload).encode())


def test_header_is_recovered_from_a_truncated_stream():
    # Arrange — 앞 8KB 만. data 배열 중간에서 끊긴다.
    truncated = _payload_gz()[:8192]

    # Act
    header = m.extract_object(m.decompress_partial(truncated), "freshness")

    # Assert
    assert header == FRESHNESS


def test_a_stream_cut_before_the_header_reports_none_not_an_empty_header():
    # Arrange — 헤더가 나오기 전에 끊긴 경우. {} 를 돌려주면 "노후도 0" 으로 읽힌다.
    text = '{"source": "openaq", "ingested_at": "2026-09-03T12:00:00Z", "recor'

    # Act / Assert
    assert m.extract_object(text, "freshness") is None


def test_an_object_that_never_closes_reports_none():
    # Arrange — 헤더 시작은 보이는데 닫히기 전에 끊긴 경우
    text = '{"freshness": {"rows": 100, "age_buckets_h": {"<=1": 3'

    # Act / Assert
    assert m.extract_object(text, "freshness") is None


def test_braces_inside_strings_do_not_confuse_the_matcher():
    # Arrange
    text = '{"freshness": {"note": "a } brace", "rows": 3}, "data": []}'

    # Act
    header = m.extract_object(text, "freshness")

    # Assert
    assert header == {"note": "a } brace", "rows": 3}


def test_slot_parsing_reads_the_published_filename_shape():
    # Arrange / Act / Assert — gz 유무 둘 다 실제로 존재한다
    assert m.parse_slot("openaq-shadow/openaq-2026090311.json.gz") == datetime(2026, 9, 3, 11, tzinfo=UTC)
    assert m.parse_slot("openaq-shadow/openaq-2026090311.json") == datetime(2026, 9, 3, 11, tzinfo=UTC)


def test_slot_parsing_rejects_names_outside_the_convention():
    # Arrange / Act / Assert — backfill·README 가 슬롯으로 오인되면 감사 표가 오염된다
    assert m.parse_slot("openaq-shadow/README.md") is None
    assert m.parse_slot("openaq-shadow/openaq-2026093199.json.gz") is None


def test_table_reproduces_the_d1_percentages():
    # Arrange — 리포트 D1 의 "51.7 % ≥24h" 와 "30 % ≥1년" 이 헤더만으로 재현돼야 한다
    rows = [{"slot": "2026-09-03T11Z", "header": FRESHNESS}]

    # Act
    table = m.render_table(rows)

    # Assert
    assert "51.7 %" in table
    assert "30.0 %" in table  # 27/90 = 30.0 % (>8760h)
    assert "| 45 | 60 | 7 |" in table


def test_untagged_slot_is_shown_not_hidden():
    # Arrange — 태깅 배포 이전 슬롯. 숨기면 "노후도 0" 과 구분되지 않는다.
    rows = [{"slot": "2026-09-01T08Z", "header": None}]

    # Act
    table = m.render_table(rows)

    # Assert
    assert "헤더 없음" in table
