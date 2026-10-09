#!/usr/bin/env python3
"""발행된 mac 스냅샷 `index.json` 을 실제로 GET 해 상태를 판정한다.

**성공한 run 이 아니라 발행물이 판정 대상이다.** `mac-data-publish.yml` 은 소스마다
`continue-on-error: true` 라 한 소스가 죽어도 초록불로 끝난다 — 그래서 "run 이
성공했다" 는 "데이터가 살아 있다" 를 전혀 뜻하지 않는다. CAMS 는 그 틈에서 며칠을
죽어 있었다.

판정 3종:
- **absent** — `available: false`
- **stale** — `expiresAt` 이 이미 지났거나, `lastAttemptAt` 이 허용 나이를 넘음
- **degraded** — `available: true` 지만 `servedFrom == "baseline"` (수집기는 죽었고
  지난 파일로 버티는 중). available 만 보면 정상으로 읽히는 바로 그 상태다.

`ADVISORY=true` 면 판정을 **보고만 하고 exit 0** — 게이트를 켠 첫날부터 red 를
쏟아내 경보 피로를 만들지 않기 위한 단계적 도입이다(수정 착지 후 승격).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone


def parse_iso8601(value: str | None) -> datetime | None:
    """`...Z` 형태를 aware datetime 으로. 파싱 불가면 None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_source(entry: dict, now: datetime, max_age: timedelta) -> list[str]:
    """소스 하나의 문제 목록. 정상이면 빈 리스트."""
    problems: list[str] = []

    if not entry.get("available"):
        reason = entry.get("unavailableReason")
        problems.append(f"absent ({reason})" if reason else "absent")
        return problems  # 없는 소스에 신선도를 따지는 건 의미 없다

    expires_at = parse_iso8601(entry.get("expiresAt"))
    if expires_at is not None and expires_at <= now:
        problems.append(f"stale (expiresAt {entry['expiresAt']} 이미 지남)")

    last_attempt = parse_iso8601(entry.get("lastAttemptAt"))
    if last_attempt is not None and now - last_attempt > max_age:
        age_h = (now - last_attempt).total_seconds() / 3600
        problems.append(f"stale (마지막 시도 {age_h:.1f}h 전 > 허용 {max_age.total_seconds()/3600:.0f}h)")

    if entry.get("servedFrom") == "baseline":
        # available:true 라 겉보기엔 정상이다. 이 줄이 없으면 만성 실패가 묻힌다.
        reason = entry.get("unavailableReason") or "unknown"
        problems.append(f"degraded (수집 {reason} — 지난 발행분으로 버티는 중)")

    return problems


def classify_index(index: dict, now: datetime, max_age: timedelta) -> dict[str, list[str]]:
    """소스별 문제 목록. 값이 빈 리스트인 소스는 정상."""
    return {
        name: classify_source(entry, now, max_age)
        for name, entry in index.get("sources", {}).items()
    }


def render_report(findings: dict[str, list[str]]) -> str:
    lines = ["## mac snapshot healthcheck", ""]
    unhealthy = {n: p for n, p in findings.items() if p}
    if not unhealthy:
        lines.append(f"✅ {len(findings)}개 소스 모두 정상")
    else:
        lines.append(f"⚠️ {len(unhealthy)}/{len(findings)} 소스에 문제")
        lines.append("")
        lines.append("| source | problems |")
        lines.append("|---|---|")
        for name, problems in sorted(unhealthy.items()):
            lines.append(f"| `{name}` | {'<br>'.join(problems)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    url = os.environ["MAC_SNAPSHOT_INDEX_URL"]
    max_age = timedelta(hours=float(os.environ.get("MAC_SNAPSHOT_MAX_AGE_HOURS", "3")))
    advisory = os.environ.get("ADVISORY", "true").lower() != "false"

    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 — 고정 https URL
        index = json.loads(resp.read())

    findings = classify_index(index, datetime.now(timezone.utc), max_age)
    report = render_report(findings)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(report)

    unhealthy = sum(1 for p in findings.values() if p)
    if not unhealthy:
        return 0
    if advisory:
        # 켠 첫날부터 매시간 red 를 쏟으면 아무도 안 본다. 보고는 하되 통과시킨다.
        print("ADVISORY 모드 — 문제를 보고만 하고 통과한다 (수정 착지 후 승격할 것)", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
