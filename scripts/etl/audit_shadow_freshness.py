#!/usr/bin/env python3
"""발행된 shadow 슬롯의 `freshness` 헤더만 읽어 원천 노후도 표를 재현한다.

평가 리포트 D1 (2026-09-03): OpenAQ `/parameters/{id}/latest` 는 설계상 "마지막
값" 이지 "현재 값" 이 아니다. 전지구 51.7 %, KR 14 % 의 행이 24h 이상 낡았고 30 %는
1년 이상이었는데, 사이드카는 `datetime_utc` 와 `ingested_at` 을 모두 갖고 있으면서
노후도를 계산하지도 요약하지도 않았다. 그 결과 "피드가 건강하다" 는 판단이 파일
수·지점 수로 충족됐고, 죽은 행도 그 둘은 똑같이 충족시킨다.

`openaq_shadow.ts` 가 이제 `freshness` 요약을 페이로드 헤더에 싣는다. 이 스크립트는
**행을 내려받지 않고** 그 헤더만 읽어 표를 재현한다 — 감사가 매번 수백 MB 를 푸는
일이면 아무도 돌리지 않는다.

헤더만 읽는 방법: 페이로드는 단일 gzip JSON 이고 키 순서상 `freshness` 가 `data`
보다 앞에 온다. 앞부분 바이트만 Range GET 해서 **끊긴 gzip 스트림을 부분 해제**하고,
`"freshness"` 뒤의 균형 잡힌 객체만 떼어낸다. 파일 전체를 받는 폴백은 있지만,
그 경로로 떨어졌다는 사실 자체를 출력한다 (조용한 성능 퇴행 방지).

사용:
    python3 scripts/etl/audit_shadow_freshness.py               # 최근 슬롯 8개
    python3 scripts/etl/audit_shadow_freshness.py --slots 24
    python3 scripts/etl/audit_shadow_freshness.py --feed sensor-community-shadow
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import zlib
from datetime import datetime, timezone

DATASET = "Robeedau/airlens-live"
DEFAULT_FEED = "openaq-shadow"
# 헤더는 압축 전 수백 바이트다. 128KB 면 충분하고, 모자라면 폴백이 잡는다.
HEADER_PROBE_BYTES = 131_072
SLOT_RE = re.compile(r"-(\d{10})\.json(?:\.gz)?$")


def parse_slot(path: str) -> datetime | None:
    """`openaq-2026090311.json.gz` → 2026-09-03T11:00Z. 규격 밖 이름이면 None."""
    match = SLOT_RE.search(path)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def extract_object(text: str, key: str) -> dict | None:
    """`"key": { ... }` 의 균형 잡힌 객체만 떼어낸다.

    부분 해제된 텍스트는 유효한 JSON 이 아니다 (`data` 배열 중간에서 끊긴다).
    그래서 전체 파싱 대신 중괄호 균형만 센다. 닫히기 전에 텍스트가 끝나면 None —
    "헤더를 못 읽었다" 를 빈 객체로 위장시키지 않는다.
    """
    marker = f'"{key}"'
    start = text.find(marker)
    if start < 0:
        return None
    brace = text.find("{", start + len(marker))
    if brace < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(brace, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace : i + 1])
                except ValueError:
                    return None
    return None


def decompress_partial(blob: bytes) -> str:
    """끊긴 gzip 스트림을 가능한 데까지 해제. 꼬리 오류는 정상 경로다."""
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
    try:
        return decompressor.decompress(blob).decode("utf-8", errors="ignore")
    except zlib.error:
        return ""


def fetch_header(url: str, timeout: int = 30) -> tuple[dict | None, bool]:
    """(freshness 헤더, 전체를 받았는지). 헤더가 없으면 (None, ...)."""
    request = urllib.request.Request(url, headers={"Range": f"bytes=0-{HEADER_PROBE_BYTES - 1}"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 — 고정 https URL
        blob = resp.read()
        ranged = resp.status == 206
    header = extract_object(decompress_partial(blob), "freshness")
    if header is not None:
        return header, not ranged

    # 폴백 — 헤더가 프로브 창 밖이거나 서버가 Range 를 무시했다.
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — 고정 https URL
        full = resp.read()
    return extract_object(decompress_partial(full), "freshness"), True


def list_slots(feed: str, timeout: int = 30) -> list[tuple[datetime, str]]:
    url: str | None = f"https://huggingface.co/api/datasets/{DATASET}/tree/main/{feed}"
    found: list[tuple[datetime, str]] = []
    while url:
        request = urllib.request.Request(url, headers={"User-Agent": "airlens-shadow-audit"})
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 — 고정 https URL
            for entry in json.load(resp):
                path = entry.get("path", "")
                slot = parse_slot(path)
                if slot is not None:
                    found.append((slot, path))
            link = resp.headers.get("Link") or ""
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = match.group(1) if match else None
    return sorted(found)


def render_table(rows: list[dict]) -> str:
    """D1 표. 헤더가 없는 슬롯은 숨기지 않고 `헤더 없음` 으로 표시한다 —
    태깅 배포 이전 슬롯과 "노후도가 0" 을 구분하지 못하면 감사가 무의미하다."""
    lines = [
        "| 슬롯 (UTC) | 행 | 나이미상 | ≥24h 비율 | p50 | p90 | ≥1y | 신선 지점 | 전체 지점 | 센티넬 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        slot = row["slot"]
        header = row["header"]
        if header is None:
            lines.append(f"| `{slot}` | — | — | 헤더 없음 | — | — | — | — | — | — |")
            continue
        buckets = header.get("age_buckets_h", {})
        over_year = buckets.get(">8760", 0)
        known = header.get("age_known") or 0
        stale = header.get("stale_fraction_24h")
        lines.append(
            f"| `{slot}` | {header.get('rows', '—')} | {header.get('age_unknown', '—')} "
            f"| {'—' if stale is None else f'{stale * 100:.1f} %'} "
            f"| {header.get('age_p50_h', '—')}h | {header.get('age_p90_h', '—')}h "
            f"| {'—' if not known else f'{over_year / known * 100:.1f} %'} "
            f"| {header.get('n_fresh_locations', '—')} | {header.get('n_locations', '—')} "
            f"| {header.get('sentinel_count', '—')} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", default=DEFAULT_FEED)
    parser.add_argument("--slots", type=int, default=8, help="최근 N개 슬롯")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    slots = list_slots(args.feed, args.timeout)
    if not slots:
        print(f"슬롯 없음 — {args.feed}", file=sys.stderr)
        return 1

    rows = []
    full_downloads = 0
    for slot, path in slots[-args.slots :]:
        url = f"https://huggingface.co/datasets/{DATASET}/resolve/main/{path}"
        header, was_full = fetch_header(url, args.timeout)
        full_downloads += int(was_full)
        rows.append({"slot": slot.strftime("%Y-%m-%dT%HZ"), "header": header})

    print(f"## shadow 원천 노후도 감사 — `{args.feed}` (최근 {len(rows)} 슬롯)\n")
    print(render_table(rows))
    tagged = sum(1 for r in rows if r["header"] is not None)
    print(f"\n헤더 보유 {tagged}/{len(rows)} 슬롯 · 전체 다운로드 폴백 {full_downloads}회")
    if tagged == 0:
        print("\n헤더가 하나도 없다 — 노후도 태깅(P0-2)이 아직 배포되지 않았다.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
