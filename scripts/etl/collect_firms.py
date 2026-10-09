#!/usr/bin/env python3
"""NASA FIRMS 활성 화재 핫스팟 수집기 (Globe 화재·연기·수송 arc 레이어).

`firms-collect.yml` 의 heredoc 인라인 스크립트를 여기로 옮긴 단일 정본이다. 옮긴
이유: 그 로직은 워크플로 YAML 안에 살아서 **어떤 테스트 스위트도 닿지 않았고**,
그 사각에서 2026-07-30 에 "HTTP 200 + 파싱 0건" 이 발행 데이터 17356건을 0건으로
덮었다. 같은 이름의 죽은 사본(`collect-firms.py`, 호출자 0)도 함께 제거했다 —
두 벌 구현은 진단·가드가 한쪽에만 들어가는 드리프트의 근원이었다.

출력 계약은 기존 `active-fires.json` 그대로 유지 (refTime/source/area/dayRange/
count/fires[]). 프론트(`useFireSources.ts`)는 `fires[]` 만 읽는다.

API: /api/area/csv/[MAP_KEY]/[SOURCE]/[AREA]/[DAY_RANGE]
  - MAP_KEY 는 **URL 경로 세그먼트**이자 유일 인증 수단 (Bearer·헤더 없음).
    32자 영숫자. 그래서 url 을 로그에 찍지 않는다 (5가드 §2).
  - AREA 는 bounding box 또는 'world' (공식 문서 명시).
  - 한도 5000 transactions / 10분.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HOST = "firms.modaps.eosdis.nasa.gov"
AREA = "world"

# day_range=2 — 2026-07-30 발행 이력 실측으로 교체(구 1). day_range 는 "최근
# 24시간" 이 아니라 **현재 UTC 일자** 로 동작한다: 07-25 03:38Z 발행이 4508건,
# 같은 날 08:24Z 가 17356건이었고, 03:3x~04:0x 발행은 07-13/15/16/17/22/23 에
# 아예 0건이었다. cron 이 `15 */6` 이라 00:15Z 실행은 구조적으로 거의 빈
# 스냅샷을 만든다 — "조용한 0건" 은 새 사고가 아니라 이 패턴의 반복이었다.
# 2일치를 받으면 실행 시각과 무관하게 최소 하루가 채워진다.
DAY_RANGE = 2
MAX_ATTEMPTS = 5

# 발행 상한 — 2026-07-30 03:01Z 실행이 day_range=2 로 71145건을 받아 6.7MB 를
# 만들었고 업로드가 HTTP 400 이었다: `wind-data` 버킷 file_size_limit = 5MB
# (실측 98.8 bytes/건 × 71145). 게다가 이 파일은 Storage 에만 있는 게 아니라
# frontend-data-sync 가 레포 정적 자산으로 복사해 **브라우저가 그대로 내려받는다** —
# 화재 레이어 하나에 6.7MB 는 그 자체로 비용이다.
# FRP(fire radiative power = 화재 복사 강도) 내림차순 상위만 발행한다. 소비처가
# 이미 FRP 로 걸러 쓰기 때문에 (useTransportArcs 의 MIN_FRP_FOR_ARC,
# usePollutionSources 의 FRP→PM2.5 환산) 잘리는 꼬리는 이미 가중이 낮은 쪽이다.
# 20000 ≈ 2.0MB — 직전 정상 발행물(17356건/1.7MB)과 같은 급, 5MB 한도에 여유.
# 상한이 걸렸다는 사실은 payload 에 그대로 적는다 (부분을 전체로 위장하지 않는다).
MAX_PUBLISHED = 20000

# 소스 폴백 — 2026-07-30 신설. 그때까지 SNPP 단일 소스에 묶여 있었고, 키·네트워크·
# 요청 형태가 모두 정상인데 응답이 "정상 스키마 CSV + 데이터 0행"(122 bytes) 이었다.
# 위성 한 대의 상류가 조용히 마르면 화재 레이어 전체가 멈춘다. 순서대로 시도해 행이
# 나오는 첫 소스를 쓰고, 어느 소스를 썼는지 출력에 기록한다.
# 목록·순서는 firms-proxy 의 ALLOWED_SOURCES 와 같게 유지한다 (프록시가 거부하는
# 소스를 발행하지 않도록). brightness 컬럼은 VIIRS=bright_ti4 / MODIS=brightness 로
# 갈리는데 아래 파서가 둘 다 본다.
SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "MODIS_NRT")


def key_shape(map_key: str) -> dict[str, object]:
    """키의 길이·문자 구성만 반환. 값·부분문자열·해시는 절대 담지 않는다.

    5가드 §2 는 시크릿 *값* 노출을 금지하지만 `public-repo.md §"Git Safety
    Guardrails"` 는 "길이/존재 인벤토리만 허용" 으로 이 수준을 명시 허용한다.
    """
    return {
        "len": len(map_key),
        "matches_32_lower_hex": bool(re.fullmatch(r"[0-9a-f]{32}", map_key)),
        "digits": sum(c.isdigit() for c in map_key),
        "lower": sum(c.islower() for c in map_key),
        "upper": sum(c.isupper() for c in map_key),
        "non_alnum": sum(not c.isalnum() for c in map_key),
    }


def preflight(host: str, port: int = 443) -> None:
    """주소별 connect 결과를 따로 찍는다 (진단 전용 — 실패해도 abort 하지 않는다).

    `[Errno 101] Network is unreachable` 는 그 자체로 원인을 말하지 않는다.
    urllib 의 create_connection 은 getaddrinfo 순서대로 시도하고 *마지막* 실패만
    올린다. 이 호스트는 A 와 AAAA 를 모두 갖고 GitHub 러너엔 IPv6 경로가 없어서,
    "IPv4 타임아웃 → IPv6 ENETUNREACH" 와 "IPv6 만 시도" 가 로그상 똑같이 errno
    101 로 보인다 — 처방이 정반대다(후자만 강제 IPv4 로 해결된다). 2026-07-30 에
    이 출력으로 전자임이 확정됐다. 호스트명만 다룬다 — MAP_KEY 는 등장하지 않는다.
    """
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError as e:
        print(f"preflight: getaddrinfo({host}) failed: {e}", file=sys.stderr)
        return
    print(f"preflight: {host} → {len(infos)} address(es), in getaddrinfo order:", file=sys.stderr)
    for family, _type, _proto, _canon, sockaddr in infos:
        fam = {socket.AF_INET: "IPv4", socket.AF_INET6: "IPv6"}.get(family, str(family))
        t0 = time.monotonic()
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(10)
        try:
            s.connect(sockaddr)
            print(f"  {fam} {sockaddr[0]} → connect OK ({time.monotonic() - t0:.1f}s)", file=sys.stderr)
        except OSError as e:
            print(f"  {fam} {sockaddr[0]} → {type(e).__name__}: {e} ({time.monotonic() - t0:.1f}s)",
                  file=sys.stderr)
        finally:
            s.close()


class ConfigFault(Exception):
    """4xx — 잘못된/폐기된 키, 잘못된 파라미터. 재시도·폴백으로 낫지 않는다."""


def fetch_csv(map_key: str, source: str, max_attempts: int) -> str | None:
    """CSV 본문, 또는 전송이 끝까지 실패하면 None. 4xx 는 ConfigFault 로 올린다."""
    url = f"https://{HOST}/api/area/csv/{map_key}/{source}/{AREA}/{DAY_RANGE}"
    last_err: object = None
    for attempt in range(max_attempts):
        try:
            return urllib.request.urlopen(url, timeout=60).read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code < 500:
                # 응답 *본문*을 찍는다. FIRMS 는 실제 원인을 거기 담는데
                # ("Invalid MAP_KEY." vs 파라미터 불만) 둘 다 맨 400 으로 온다 —
                # 본문 없는 로그는 진단이 안 된다. 2026-07-29 정지에서 이 모호함
                # 때문에 키 회전 1회를 낭비했다. url 은 찍지 않는다 (§2).
                try:
                    body = e.read().decode("utf-8", "replace").strip()[:300]
                except OSError:
                    body = "(response body unreadable)"
                raise ConfigFault(f"HTTP {e.code}: {e.reason} — body: {body!r}") from e
            # 5xx — 업스트림 일시 결함. FIRMS 는 야간 창(~03:45 UTC)에 502 를
            # 돌려주는데 그것 때문에 이 cron 이 매일 빨간불이었다. 재시도.
            last_err = e
        except OSError as e:  # URLError, socket timeout — 전송 계층 일시 결함
            last_err = e
        if attempt < max_attempts - 1:
            backoff = 5 * (2 ** attempt)
            print(f"{source} attempt {attempt+1}/{max_attempts} failed: {last_err}; "
                  f"retry in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)
    print(f"{source}: 전송 실패 ({last_err})", file=sys.stderr)
    return None


def parse_fires(text: str) -> tuple[list[dict[str, object]], int, list[str]]:
    """(fires, csv 데이터 행 수, 컬럼명). 좌표를 못 읽는 행은 버린다.

    행 수와 컬럼명을 함께 돌려주는 이유: 0건일 때 "응답이 비었나 / 컬럼이 바뀌었나"
    를 갈라야 한다. 그 구분이 없어서 2026-07-30 에 0건이 조용히 발행됐다.
    """
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    fires: list[dict[str, object]] = []
    raw_rows = 0
    for row in reader:
        raw_rows += 1
        try:
            lat = round(float(row.get("latitude", "")), 4)
            lon = round(float(row.get("longitude", "")), 4)
        except (ValueError, TypeError):
            continue
        frp_raw = row.get("frp", "")
        bright_raw = row.get("bright_ti4") or row.get("brightness", "")
        fires.append({
            "lat": lat, "lon": lon,
            "brightness": round(float(bright_raw), 2) if bright_raw else None,
            "frp": round(float(frp_raw), 2) if frp_raw else None,
            "date": row.get("acq_date"),
            "confidence": row.get("confidence"),
        })
    return fires, raw_rows, fieldnames


def collect(map_key: str) -> tuple[list[dict[str, object]], str] | None:
    """(fires, 사용한 source), 또는 전송이 아예 안 되면 None(= soft-skip 대상).

    세 소스를 모두 시도해 **행이 가장 많은** 소스를 쓴다. "첫 번째로 비지 않은
    소스" 가 아닌 이유 = 2026-07-30 02:51Z 실측: 같은 순간에 SNPP 는 0행,
    NOAA20 은 26행이었다. 위성별 granule 처리 시각이 달라 한쪽만 막 채워지기
    시작한 상태이고, 그 26행을 그대로 쓰면 전지구 현황으로 발행된다.
    소스를 합치지는 않는다 — 두 위성이 같은 화재를 보면 중복 계상된다.

    첫 소스에서 전송 자체가 실패하면 소스 문제가 아니라 네트워크/업스트림이므로
    다른 소스를 더 시도하지 않는다 — 같은 실패를 반복하며 job timeout(10분)만
    태운다. 200 을 받은 경우에만 다음 소스로 넘어간다 (그 응답은 즉시 온다).
    """
    diagnostics: list[str] = []
    best: tuple[list[dict[str, object]], str] | None = None
    for idx, source in enumerate(SOURCES):
        print(f"Fetching FIRMS: {source}, area={AREA}, days={DAY_RANGE}...")
        text = fetch_csv(map_key, source, MAX_ATTEMPTS if idx == 0 else 1)
        if text is None:
            if idx == 0:
                return None  # 전송 실패 → soft-skip (이전 데이터 유지)
            diagnostics.append(f"{source}: 전송 실패")
            continue
        fires, raw_rows, fieldnames = parse_fires(text)
        print(f"  {source}: {len(fires)}건 (csv 데이터 행={raw_rows})")
        if fires:
            if best is None or len(fires) > len(best[0]):
                best = (fires, source)
            continue
        diagnostics.append(
            f"{source}: HTTP 200 이지만 0행 ({len(text)} bytes, csv 데이터 행={raw_rows}, "
            f"columns={fieldnames})"
        )
        print(f"  response head: {text[:200]!r}", file=sys.stderr)

    if best is not None:
        fires, source = best
        if source != SOURCES[0]:
            print(f"::notice title=FIRMS source::{source} 가 {SOURCES[0]} 보다 많아 "
                  f"그쪽을 쓴다 ({len(fires)}건). " + " | ".join(diagnostics))
        return best

    # 모든 소스가 0행 — 파일을 쓰지 않는다. 업로드 스텝은 파일 존재를 전제하므로
    # 이전 데이터가 그대로 남고(덮어쓰기 방지), freshness 게이트가 stale 로 울린다.
    # 빈 배열을 받은 소비처가 "데이터 없음" 을 사실처럼 렌더하는 것보다 낫다.
    msg = (f"모든 소스가 0행을 돌려줬다 — 전지구 {DAY_RANGE}일치가 0건일 수는 없다. "
           "이전 데이터를 덮지 않기 위해 업로드 없이 중단한다. " + " | ".join(diagnostics))
    print(msg, file=sys.stderr)
    print(f"::error title=FIRMS empty parse::{msg[:400]}")
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NASA FIRMS 활성 화재 수집")
    ap.add_argument("--out", default="active-fires.json", help="출력 JSON 경로")
    args = ap.parse_args(argv)

    # .strip() 은 미관이 아니라 기능이다: `gh secret set` 이 파일/파이프로 값을 받으면
    # 개행이 남고, 개행이 붙은 키는 .../csv/KEY%0A/... 로 전송돼 FIRMS 가 400
    # "Invalid MAP_KEY." 를 준다 — 진짜 잘못된 키와 로그상 구분되지 않는다.
    map_key = os.environ.get("NASA_FIRMS_MAP_KEY", "").strip()
    if not map_key:
        print("NASA_FIRMS_MAP_KEY not set (or whitespace-only)", file=sys.stderr)
        return 1

    shape = key_shape(map_key)
    print(f"MAP_KEY shape (값 미노출 — 길이/구성만): {shape}", file=sys.stderr)
    # 형태로 확정 가능한 실패는 형태에서 끊는다. MAP_KEY 는 URL 경로 세그먼트라
    # 구두점·공백을 담을 수 없다 — 시크릿에 키가 아닌 텍스트(문장·URL·라벨)가
    # 들어갔을 때 나오는 형태다. 2026-07-30 에 그런 값으로 실행 3회를 낭비했다
    # (len=63/non_alnum=18, len=161/non_alnum=38, len=676 = Earthdata JWT).
    # 범위는 16~80 으로 넉넉히 — 실무상 32자지만 NASA 형식 변경 여지를 남기면서
    # 문장은 확실히 걸러내는 폭.
    if shape["non_alnum"] or not (16 <= int(shape["len"]) <= 80):
        msg = (f"MAP_KEY 형태가 아니다 (len={shape['len']}, non_alnum={shape['non_alnum']}). "
               "MAP_KEY 는 URL 경로 세그먼트라 구두점·공백을 담을 수 없다. "
               "NASA 요청은 보내지 않고 중단한다.")
        print(msg, file=sys.stderr)
        print(f"::error title=FIRMS MAP_KEY malformed::{msg}")
        return 1

    preflight(HOST)

    try:
        result = collect(map_key)
    except ConfigFault as e:
        print(f"FIRMS API {e}", file=sys.stderr)
        print(f"::error title=FIRMS config fault::{str(e)[:200]}")
        return 1

    if result is None:
        # 6시간 주기 best-effort 수집기: 일시적 네트워크/업스트림 blip 으로 cron 을
        # 죽이지 않는다. 이 주기를 건너뛰면 이전 데이터가 Storage 에 남고 다음 실행이
        # 스스로 낫는다. 건너뜀이 SLA 를 넘게 쌓이면 freshness 게이트가 크게 울린다.
        msg = "FIRMS 전송 실패 — 이 주기를 건너뛴다 (이전 데이터 유지)."
        print(f"::warning title=FIRMS soft-skip::{msg}")
        print(msg, file=sys.stderr)
        return 0

    fires, source = result
    detections = len(fires)
    capped = detections > MAX_PUBLISHED
    if capped:
        # FRP 없는 레코드는 0으로 취급 — 가장 약한 쪽으로 밀린다.
        fires = sorted(fires, key=lambda f: f["frp"] or 0.0, reverse=True)[:MAX_PUBLISHED]
        weakest = fires[-1]["frp"]
        print(f"::notice title=FIRMS capped::{detections}건 중 FRP 상위 {len(fires)}건만 "
              f"발행한다 (버킷 5MB 한도 + 브라우저 전송량). 발행 최저 FRP={weakest}")

    payload = {
        "refTime": datetime.now(timezone.utc).isoformat(),
        "source": source, "area": AREA, "dayRange": DAY_RANGE,
        "count": len(fires), "fires": fires,
        # 부분을 전체로 위장하지 않는다 — 잘렸으면 잘린 사실과 기준을 함께 발행한다.
        "totalDetections": detections,
        "capped": capped,
        "minFrpPublished": fires[-1]["frp"] if capped else None,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    size = os.path.getsize(args.out)
    print(f"Done: {len(fires)} fire hotspots from {source} → {args.out} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
