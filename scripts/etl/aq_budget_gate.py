"""AQ 호스트(가스·pollen) 일일 예산 게이트 — 실행 시각이 아니라 발행물 나이로 판정한다.

**왜 바꿨나 (2026-09-27 실측).** 직전 게이트는 `now.hour % 12 < 3` — "00Z·12Z 슬롯의
run 만 수집" 이었다. 이 게이트는 cron `5 */3 * * *` 이 **정시에 배달된다**는 전제에
서 있다. 실제로는 org 의 schedule 이벤트가 슬롯의 35~45% 만, 1.5~4h 늦게 생성됐다
(04/11/16-17/20-21 UTC 에 몰림). `created_at == run_started_at` 이므로 러너 지연이
아니라 이벤트 자체가 늦게 온 것이다. 그 결과 09-22 00:00Z 이후 게이트 창(00~02Z,
12~14Z)에 떨어진 run 이 0 회 → 가스 3종·pollen 이 141h 노후, Hourly run 30 회 중
23 회가 신선도 프로브로 빨갛게 끝났다. 수동 `force_aq` 가 유일한 복구 수단이었다.

벽시계 창에 게이트를 거는 한, 이벤트가 창을 빗나가면 수집이 통째로 사라진다.
그래서 "지금 몇 시인가" 대신 **"마지막으로 발행한 지 얼마나 됐나"** 를 묻는다 —
어떤 시각에 run 이 오든, 발행물이 충분히 늙었으면 그 run 이 수집한다.

**예산 상한 (왜 11h 인가).** 한 번 수집 = 가스 2,376 + pollen 580 ≈ 2,956 좌표
(Open-Meteo 무료 티어는 좌표 가중 미터링, 일 한도 ~10k). 11h 간격이면 하루 최대
3 회 = 8.9k + forecast-collect 300 ≈ 9.2k — 한도 아래. 실제 배달률(하루 3~4 run)
에서는 대개 2 회다. 12h 로 잡으면 수집 시각 편차(스크립트 시작 시각)로 매번 한
사이클(3h)씩 밀려 실효 케이던스가 15h 가 된다.

**왜 나이는 가스 격자만 보나.** pollen 을 따로 게이트하면, 비시즌에 CAMS 가 전 셀
null 을 돌려주는 경우(0 점 → 파일 미기록 → 나이 불변) 매 run 580 좌표를 태운다.
가스와 한 몸으로 묶어 두면 pollen 은 추가 예산을 쓰지 않는다.

**실패 루프가 예산을 태우지 않는 이유.** 나이는 *기록이 성공했을 때만* 줄어든다.
스로틀로 실패한 run 은 `fetch_chunks` 가 연속 3 청크에서 접으므로 거의 비용이 없고,
0 점이면 파일을 쓰지 않으니 다음 run 이 다시 시도한다 — 비용은 성공에만 든다.

**읽기 실패 시.** 발행물을 못 읽으면(HF 장애 등) 옛 벽시계 게이트로 되돌아간다.
"못 읽었으니 수집" 으로 하면 HF 장애 동안 매 run 이 예산을 태우고, "못 읽었으니 skip"
으로 하면 HF 가 살아나도 업로드 실패와 구분이 안 된다. 옛 동작은 최소한 알려진 값이다.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime

from check_aq_grid_freshness import HF_AQ_BASE, SPEC_BY_KEY, fetch, parse_age_stamp

MIN_RECOLLECT_AGE_HOURS = 11.0

# 세 가스 격자는 같은 요청 묶음에서 한꺼번에 나온다.
# ponytail: pollen 은 이 나이에 묶여 있어, 가스 성공 run 에서 pollen 만 스로틀로
#   굶으면 다음 가스 사이클까지 밀린다 — SLA 24h 프로브가 잡는다. 반복되면 pollen
#   전용 나이 게이트로 분리.
GAS_SLUGS = ("current-o3-grid", "current-no2-grid", "current-co-grid")


def decide(gas_age_hours: float | None, now: datetime, force: bool) -> tuple[bool, str]:
    """수집 여부와 그 이유 — 네트워크 없는 순수 함수.

    `gas_age_hours`: 가장 최근에 발행된 가스 격자의 나이. `math.inf` = 미발행(404),
    `None` = 읽기 실패.
    """
    if force:
        return True, "force_aq 수동 복구"
    if gas_age_hours is None:
        due = now.hour % 12 < 3
        return due, f"발행물 읽기 실패 → 벽시계 폴백(hour={now.hour}, 00Z·12Z 창={'안' if due else '밖'})"
    if gas_age_hours >= MIN_RECOLLECT_AGE_HOURS:
        return True, f"가스 발행물 {gas_age_hours:.1f}h 전 ≥ {MIN_RECOLLECT_AGE_HOURS}h"
    return False, f"가스 발행물 {gas_age_hours:.1f}h 전 < {MIN_RECOLLECT_AGE_HOURS}h — 직전 발행분 유지"


def published_gas_age_hours(now: datetime, *, base: str = HF_AQ_BASE, fetcher=fetch) -> float | None:
    """발행된 가스 격자 중 **가장 최근 것**의 나이(시간).

    가장 최근 것을 보는 이유: 나이가 재는 것은 "마지막으로 가스 수집이 성공한 때" 다.
    변수 하나가 늘 비어 파일이 안 써지면, 가장 오래된 것을 기준으로 할 경우 매 run
    수집하게 된다(예산 루프). 전부 404 면 `math.inf`(부트스트랩 = 수집), 하나도
    못 읽었으면 `None`.
    """
    ages: list[float] = []
    unpublished = 0
    for slug in GAS_SLUGS:
        spec = SPEC_BY_KEY[("aq-data", slug)]
        try:
            payload = fetcher(f"{base}/{slug}.json")
            if payload is None:
                unpublished += 1
                continue
            stamp = parse_age_stamp(spec, payload.get(spec.age_field))
        except Exception as e:  # noqa: BLE001 — 한 격자 읽기 실패는 나머지로 판정
            print(f"::warning title=AQ budget gate::{slug} 나이 읽기 실패: {e}", file=sys.stderr)
            continue
        ages.append((now - stamp).total_seconds() / 3600)
    if ages:
        return min(ages)
    if unpublished == len(GAS_SLUGS):
        return math.inf
    return None


def aq_host_budget_run(now: datetime, force: bool, *, fetcher=fetch) -> tuple[bool, str]:
    """워크플로 heredoc 진입점. 강제 복구면 발행물을 읽지도 않는다."""
    if force:
        return decide(None, now, force=True)
    return decide(published_gas_age_hours(now, fetcher=fetcher), now, force=False)
