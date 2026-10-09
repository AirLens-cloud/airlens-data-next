#!/usr/bin/env python3
"""AQ 격자 발행물 신선도 라이브 프로브 — 발행된 파일을 되읽어 나이를 잰다.

**왜 필요한가.** `data-collect-hourly.yml` 의 "Verify uploads" 는 HTTP 200 만 본다.
200 은 *파일이 있다* 는 것만 증명한다 — 몇 달 묵은 파일도 200 을 낸다. 수집이
멈추면 upload step 이 건너뛰어지고, verify 는 **직전 성공분**에 200 을 받고,
run 은 green 으로 끝난다. 그 상태가 며칠 갈 수 있다.

wind 격자에는 이 프로브가 이미 있었고(`data-collect-hourly.yml` "Verify wind
freshness"), AQ 격자에는 없었다. `scripts/publish_contracts.py` 가 그 갭을
스스로 기록해 두고 있었다:

    "freshnessSlaSeconds": None,
    "freshnessSlaReason": "data-collect-hourly.yml 에 aq-data 그리드 전용
                           staleness gate 없음(wind-data 만 ... 존재)"

PR #15 가 발행물에 `generatedAt` 을 실으면서(`collect_noaa_aq.py`) 이 프로브가
가능해졌다 — `timestamp` 는 데이터가 유효한 사이클 시각이라 수집이 멈춰도
움직이지 않으므로 건강 신호가 **아니다**. `generatedAt` 만이 파이프라인이
마지막으로 성공한 시각이다.

**왜 워크플로 인라인 heredoc 이 아닌 파일인가.** wind 프로브는 워크플로 안의
heredoc 이라 테스트가 불가능하다 — 프로브 자체가 고장 나면(예: 필드명 오타로
항상 예외를 삼키고 0 을 반환) 아무도 모른 채 green 이 계속 찍힌다. 이 레포는
PR #12 로 CI 테스트 게이트를 갖췄으므로, 판정 로직을 순수 함수로 빼서
`tests/test_check_aq_grid_freshness.py` 가 red/green 을 실증한다.
(네트워크 계층만 `probe()` 에 격리 — 순수 함수는 `evaluate()`.)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import NamedTuple

HF_AQ_BASE = os.environ.get(
    "HF_AQ_BASE",
    "https://huggingface.co/datasets/Robeedau/airlens-live/resolve/main/aq-data",
)

# 두 번째 발행 경로 (2026-09-04 추가). `mac-data-publish.yml` 이 mac GEFS-chem
# 스냅샷을 AQGridResponse 계약으로 변환해 여기 올린다 — #21 이 계약 게이트를
# 걸기 전까지 **무검증**이었고, 신선도는 지금까지도 아무도 재지 않았다.
HF_WEB_V1_BASE = os.environ.get(
    "HF_WEB_V1_BASE",
    "https://huggingface.co/datasets/Robeedau/airlens-live/resolve/main/mac-data/data/web/v1",
)

# ── 격자별 스펙 ─────────────────────────────────────────────────────────────
#
# **왜 슬러그마다 필드가 다른가.** 두 생산자가 나이를 다른 이름에 싣는다:
#
#   - `collect_noaa_aq.py` (pm2_5/pm10) — `generatedAt`.
#     이 파일들의 `timestamp` 는 **데이터 사이클 시각**이라 수집이 멈춰도 움직이지
#     않는다. 건강 신호가 아니다.
#   - `collect_all.py` (o3/no2/co, workflow heredoc) — `timestamp`(epoch ms).
#     여기서는 `int(now.timestamp() * 1000)` = **수집 벽시계**다. 움직인다.
#   - 같은 heredoc 의 pollen — `collected_at`(ISO).
#
# 즉 `timestamp` 라는 **같은 이름이 두 생산자에서 다른 양을 가리킨다.** 한 필드로
# 뭉뚱그리면 PM 격자는 영원히 신선해 보이고(사이클 시각이 계속 갱신되므로) 가스
# 격자는 멀쩡한데 실패한다. 그래서 슬러그별 스펙으로 못박는다.
#
# **왜 형태 검사도 다른가.** 실측(2026-09-04): PM 격자는 조밀(65,160 = 181×360),
# 가스 격자는 **희소**(2,196 / 2,376 — Open-Meteo 가 값을 못 주는 셀은 points 에
# 들어가지 않는다), pollen 은 nLat/nLon 자체가 없고 `count` + `points` 다.
# PM 의 조밀 등식을 그대로 씌웠으면 가스 격자가 매 실행 오탐으로 빨개졌을 것이다.
# 커버리지 하한(예: "≥90% 여야 한다")은 **발명하지 않는다** — 근거가 없다.
# 검사하는 것은 구조적 불변식뿐: 셀 수보다 많은 점은 있을 수 없고, 0 점은 발행이
# 아니며, 스스로 신고한 count 는 실제 길이와 같아야 한다.


class GridSpec(NamedTuple):
    product: str            # 어느 발행 경로의 산출물인가 ("aq-data" | "web-v1")
    slug: str
    age_field: str          # 나이를 재는 필드 이름
    age_kind: str           # "iso" | "epoch_ms"
    shape: str              # "dense" | "sparse" | "count"
    # 격자별 SLA 오버라이드 (시간). None = 제품 기본값. 존재 이유(2026-09-05):
    # AQ 호스트 일일 쿼터 예산 게이트로 가스·pollen 만 12h 케이던스가 됐다 —
    # 제품 하나에 SLA 하나면 3h 케이던스인 PM 격자와 같은 6h 자로 재게 되어
    # 게이트 자체가 매 run 오탐이 된다. 케이던스가 갈리면 자도 갈려야 한다.
    sla_hours: float | None = None


# **키가 slug 단독이면 안 되는 이유** (2026-09-04 web/v1 확장에서 드러남):
# `current-pm25-grid` 라는 **같은 파일명이 두 경로에서 다른 제품**이다.
#
#   aq-data/current-pm25-grid.json   — collect_noaa_aq.py, 조밀 65,160 셀, `generatedAt`
#   mac-data/data/web/v1/…same name  — build_web_aq_grid.py, 다운샘플 37×72, `timestamp`
#
# slug 로만 스펙을 찾으면 web/v1 격자에 조밀 등식이 씌워져 매 실행 오탐이 된다.
# 그래서 키는 (product, slug) 다.
SPECS: tuple[GridSpec, ...] = (
    GridSpec("aq-data", "current-pm25-grid", "generatedAt", "iso", "dense"),
    GridSpec("aq-data", "current-pm10-grid", "generatedAt", "iso", "dense"),
    # 가스·pollen — AQ 호스트 예산 게이트(data-collect-hourly.yml, 2026-09-05)로
    # ~12h 케이던스로만 수집한다(aq_budget_gate.py — 발행물 나이 ≥ 11h). SLA = 2사이클(24h) — 6h 기본값의 근거였던
    # "두 사이클 연속 결손"을 12h 케이던스에 그대로 적용한 값이다.
    GridSpec("aq-data", "current-o3-grid", "timestamp", "epoch_ms", "sparse", sla_hours=24.0),
    GridSpec("aq-data", "current-no2-grid", "timestamp", "epoch_ms", "sparse", sla_hours=24.0),
    GridSpec("aq-data", "current-co-grid", "timestamp", "epoch_ms", "sparse", sla_hours=24.0),
    GridSpec("aq-data", "pollen-grid", "collected_at", "iso", "count", sla_hours=24.0),
    # web/v1 — `timestamp` 는 **원본 스냅샷의 generatedAt 을 보존한 값**이라
    # (build_web_aq_grid.py §3, 없으면 지어내지 않고 raise) 파이프라인 건강 신호다.
    # aq-data 쪽 PM 격자의 `timestamp`(데이터 사이클 시각)와는 다른 양이다 —
    # 이름이 같다고 같은 뜻이 아니라는 것이 이 스펙 표가 존재하는 이유다.
    # 형태는 **희소**: 계약(web-aq-grid.v1)이 "값을 못 얻은 셀은 아예 없다"고
    # 규정한다. 실측 2026-09-04 에는 2664/2664 로 꽉 찼지만, 그 순간의 값으로
    # 조밀 등식을 못박으면 셀 하나가 빠지는 날 오탐이 된다.
    GridSpec("web-v1", "current-pm25-grid", "timestamp", "epoch_ms", "sparse"),
    GridSpec("web-v1", "current-pm10-grid", "timestamp", "epoch_ms", "sparse"),
)
SPEC_BY_KEY = {(s.product, s.slug): s for s in SPECS}


def specs_for(product: str) -> tuple[GridSpec, ...]:
    return tuple(s for s in SPECS if s.product == product)


# wind 와 같은 근거: 수집 cron 이 3h(`5 */3 * * *`) 이므로 6h = 두 사이클 연속 결손.
# 한 사이클 결손(러너 일시 점유 등)으로는 울리지 않고, 두 번 연속이면 고장이다.
DEFAULT_MAX_STALENESS_HOURS = 6.0

# web/v1 은 cron 이 명목상 매시(`25 * * * *`)지만 **명목값을 SLA 로 쓰면 안 된다**.
# 실측(2026-09-04, 최근 성공 run 12건): 실제 간격 2.4~5.5h. 여기에 원본 스냅샷
# 지연(측정 시점 0.85h)이 더해지므로 6h 는 정상 운영에서도 넘긴다 — 그러면 프로브가
# 양치기 소년이 되고, 진짜 고장 때 아무도 안 본다. 실측 최대 간격의 약 2배를 잡는다.
DEFAULT_WEB_V1_MAX_STALENESS_HOURS = 12.0


def parse_generated_at(value: str) -> datetime:
    """`generatedAt` 문자열 → tz-aware UTC datetime. 형식이 아니면 ValueError."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_age_stamp(spec: GridSpec, raw) -> datetime:
    """스펙이 지정한 종류대로 나이 스탬프를 읽는다. 형식이 아니면 ValueError."""
    if spec.age_kind == "epoch_ms":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"epoch ms 가 아니다: {raw!r}")
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"ISO 문자열이 아니다: {raw!r}")
    return parse_generated_at(raw)


def check_shape(spec: GridSpec, payload: dict, slug: str) -> list[str]:
    """스펙별 구조 불변식. 위반 사유 목록을 돌려준다(빈 목록 = 통과)."""
    points = payload.get("points")
    if not isinstance(points, list):
        return [f"::error title=AQ grid shape::{slug} points 가 리스트가 아니다"]
    if not points:
        # 0 점은 "빈 격자를 발행" 이지 "신선한 발행" 이 아니다. 나이만 보면
        # 방금 쓴 빈 파일이 가장 신선해 보인다.
        return [f"::error title=AQ grid shape::{slug} points 가 0개 — 빈 격자를 발행했다"]

    if spec.shape == "count":
        declared = payload.get("count")
        if declared != len(points):
            msg = (
                f"::error title=AQ grid shape::{slug} count={declared!r} 인데 "
                f"points 는 {len(points)}개 — 스스로 신고한 수와 실제가 다르다"
            )
            return [msg]
        return []

    n_lat, n_lon = payload.get("nLat"), payload.get("nLon")
    if not isinstance(n_lat, int) or not isinstance(n_lon, int):
        return [f"::error title=AQ grid shape::{slug} nLat/nLon 이 정수가 아니다"]
    cells = n_lat * n_lon

    if spec.shape == "dense":
        # subsample() 이 조밀 격자를 보장한다. (음수 셀은 value 가 null 이 될 뿐
        # 엔트리가 사라지지 않으므로 이 등식은 여전히 유효하다.)
        if len(points) != cells:
            msg = (
                f"::error title=AQ grid shape::{slug} points {len(points)}개 "
                f"(nLat {n_lat} × nLon {n_lon} = {cells} 기대) — 격자가 깨졌다"
            )
            return [msg]
        return []

    # sparse — 값을 못 받은 셀은 빠진다. 하한은 근거가 없어 걸지 않고,
    # 셀 수를 넘는 점 개수(= 격자 정의와 payload 불일치)만 잡는다.
    if len(points) > cells:
        msg = (
            f"::error title=AQ grid shape::{slug} points {len(points)}개가 "
            f"셀 수 {cells}(nLat {n_lat} × nLon {n_lon})를 초과 — 격자 정의와 불일치"
        )
        return [msg]
    return []


def evaluate(spec: GridSpec, payload: dict, now: datetime, max_hours: float) -> tuple[bool, list[str]]:
    """발행물 하나를 판정한다 — 네트워크 없는 순수 함수.

    첫 인자가 slug 문자열이 아니라 **스펙**인 이유: 같은 slug 가 두 발행 경로에서
    다른 제품이라(§SPECS) 문자열 하나로는 어느 쪽인지 정해지지 않는다.

    반환 `(ok, lines)`. `lines` 는 Actions annotation 문자열 그대로.
    실패 사유를 뭉뚱그리지 않는다: 필드 부재/형식 오류/격자 불일치/노후 를
    각각 다른 title 로 낸다 — 로그 한 줄만 보고 어디를 봐야 하는지 알게.
    """
    lines: list[str] = []
    slug = f"{spec.product}/{spec.slug}"

    raw_gen = payload.get(spec.age_field) if isinstance(payload, dict) else None
    if raw_gen is None:
        lines.append(
            f"::error title=AQ grid freshness::{slug} 에 {spec.age_field} 가 없다 — "
            f"이 격자의 나이를 재는 필드다. 발행 경로가 옛 생산자로 돌아갔는지 확인"
        )
        return False, lines

    try:
        gen_dt = parse_age_stamp(spec, raw_gen)
    except ValueError as e:
        lines.append(
            f"::error title=AQ grid freshness::{slug} {spec.age_field} 형식 오류 ({raw_gen!r}): {e}"
        )
        return False, lines

    ok = True

    shape_errors = check_shape(spec, payload, slug)
    lines.extend(shape_errors)
    if shape_errors:
        ok = False

    age_h = (now - gen_dt).total_seconds() / 3600
    lines.append(f"{slug}: {spec.age_field}={raw_gen} age={age_h:.1f}h")
    if age_h > max_hours:
        lines.append(
            f"::error title=AQ grid stale::{slug} {spec.age_field} 가 {age_h:.1f}h 전 "
            f"(> {max_hours}h SLA) — 이 격자의 수집이 조용히 실패하고 있고 "
            f"웹은 옛 격자를 보고 있다"
        )
        ok = False
    else:
        lines.append(f"::notice title=AQ grid fresh::{slug} {age_h:.1f}h 전 (SLA {max_hours}h 이내)")

    return ok, lines


def sla_for(spec: GridSpec, product_default: float) -> float:
    """격자에 적용할 SLA — 스펙 오버라이드가 있으면 그것, 없으면 제품 기본값.

    순수 함수로 뺀 이유: probe() 인라인이면 "오버라이드가 실제로 적용되는가"를
    네트워크 없이 실증할 수 없다(이 파일의 evaluate()/probe() 분리와 같은 근거).
    """
    return spec.sla_hours if spec.sla_hours is not None else product_default


def fetch(url: str, timeout: int = 60) -> dict | None:
    """발행물을 되읽는다. 404 는 None (baseline 미확립) — 그 외 예외는 그대로 올린다."""
    try:
        raw = urllib.request.urlopen(url, timeout=timeout).read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return json.loads(raw)


def probe(base: str, specs, max_hours: float, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    failed = False
    for spec in specs:
        label = f"{spec.product}/{spec.slug}"
        url = f"{base}/{spec.slug}.json"
        try:
            payload = fetch(url)
        except Exception as e:  # noqa: BLE001 — 어떤 실패도 조용히 넘기지 않는다
            print(f"::error title=AQ grid freshness::{label} probe 실패: {e}", file=sys.stderr)
            failed = True
            continue

        if payload is None:
            # 최초 실행에만 정당한 상태. 실패로 만들면 새 격자를 추가할 때마다
            # 첫 run 이 빨개진다 (wind 프로브와 같은 카브아웃).
            print(f"::warning title=AQ grid freshness::{label} 아직 미발행 (404) — baseline 대기")
            continue

        ok, lines = evaluate(spec, payload, now, sla_for(spec, max_hours))
        for line in lines:
            print(line, file=sys.stderr if line.startswith("::error") else sys.stdout)
        if not ok:
            failed = True

    return 1 if failed else 0


# 제품마다 base URL 과 SLA 가 다르다 — 발행 주체도 주기도 다르기 때문이다.
PRODUCTS = {
    "aq-data": (lambda: HF_AQ_BASE, "MAX_AQ_STALENESS_HOURS", DEFAULT_MAX_STALENESS_HOURS),
    "web-v1": (lambda: HF_WEB_V1_BASE, "MAX_WEB_V1_STALENESS_HOURS", DEFAULT_WEB_V1_MAX_STALENESS_HOURS),
}


def main(argv=None) -> int:
    """`--product <name>` 없이 부르면 전 제품을 본다(로컬·수동 점검용).

    **워크플로에서는 반드시 제품을 지정한다.** 두 제품은 발행 주체가 다르므로,
    한 워크플로가 남의 제품까지 판정하면 #20 에서 끊어낸 결합(느린 제공자가
    무관한 수집기를 인질로 잡던 것)이 프로브 층에서 되살아난다.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    selected = list(PRODUCTS)
    if "--product" in argv:
        name = argv[argv.index("--product") + 1]
        if name not in PRODUCTS:
            print(f"::error title=AQ grid freshness::알 수 없는 --product {name!r} "
                  f"(가능: {', '.join(PRODUCTS)})", file=sys.stderr)
            return 2
        selected = [name]

    rc = 0
    for name in selected:
        base_fn, env_key, default_hours = PRODUCTS[name]
        max_hours = float(os.environ.get(env_key, default_hours))
        specs = specs_for(name)
        # 스펙이 비면 "위반 0건"이 아니라 설정 사고다 — vacuous green 회피.
        if not specs:
            print(f"::error title=AQ grid freshness::{name} 스펙이 비어 있다 — 아무것도 검사하지 않았다",
                  file=sys.stderr)
            return 2
        rc |= probe(base_fn(), specs, max_hours)
    return rc


if __name__ == "__main__":
    sys.exit(main())
