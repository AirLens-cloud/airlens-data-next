#!/usr/bin/env python3
"""OpenAQ v3 벌크 스냅샷 수집기 — 모노레포 insights 파이프라인 입력용 (클라우드화, 2a).

목적: AirLens 모노레포의 `Data/3-raw-sources/air_quality/openaq/` 가 152일간
방치돼 있었다(로컬 수동 실행 의존). 이 수집을 GitHub hosted 러너에서 정기 실행해
HF (`Robeedau/airlens-live` dataset, `insights-data/openaq-bulk/`) 에 발행하면
모노레포 쪽 pull 스텝(별도 구현)이 로컬로 내려받는다.

기존 근실시간 수집기(`collect_openaq_v3.py`, 모노레포 히스토리 c5607eb4)는 국가별
`/v3/locations?iso=XX` 순회 + 관측소별 `/v3/locations/{id}/latest` 개별 호출이라
관측소 수만큼 요청이 든다(9시간급). 본 수집기는 **관측소별 순회를 하지 않는다**:

  1. `GET /v3/parameters` — 파라미터 이름(pm25/pm10/no2/o3) → id 해석
     (하드코딩 금지, docs.openaq.org 확인: 응답은
     `results[].{id,name,units,displayName,description}`).
  2. `GET /v3/parameters/{id}/latest?limit=1000&page=N` — 파라미터별 **글로벌**
     최신값 벌크 페이지네이션 (`results[].{locationsId,sensorsId,value,
     datetime.utc,coordinates.{latitude,longitude}}` — api.openaq.org/openapi.json
     실측, unit 필드는 여기 없고 파라미터 단위 전체에 공통이라 §1 에서 온다).
  3. `GET /v3/locations?limit=1000&page=N` — 글로벌 1회 순회로 관측소 메타
     (`results[].{id,name,country.{code,name},coordinates}`) 를 모아 국가/이름
     join. pm25 latest 자체가 좌표를 갖고 있어(§2) 여기선 이름·국가코드만 쓴다.

정직성(형제 수집기 정합, `collect_firms.py` / 모노레포 `collect_openaq_v3.py` 와
동일 에토스): 값이 없으면 0 으로 메우지 않는다. 단위를 인식 못 하면(ppm/ppb 등)
그 파라미터 전체를 skip(임의 배율 추정 금지) — µg/m³ 계열만 직접 채택, mg/m³ 은
×1000. pm25 는 필수 파라미터라 하나도 못 모으면 abort(exit 1, last-good 유지).

**키 부재 = graceful skip**: `OPENAQ_API_KEY` 가 없으면 파일을 쓰지 않고 exit 0
(::warning::) — 워크플로가 키 없이도 머지되고, 키 등록이 payoff.

**호출 예산(no silent cap)**: `OPENAQ_BUDGET_REQUESTS`(기본 500) 를 넘으면 그
단계에서 WARN 후 중단하고 이미 모은 부분 데이터로 계속 진행한다(부분 산출을
전체로 위장하지 않는다 — 로그에 정직하게 남긴다). `OPENAQ_RATE_PER_MIN`(기본 55,
60/분 상한 아래 여유) 로 페이싱, 429 는 Retry-After(있으면) 또는 지수 백오프로
재시도.

신규 pip dep 0(stdlib urllib only).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# 소비자 allowlist(`^openaq_[A-Z]{2}_(full|multi)_[0-9]{8}\.csv$`) 와 정합 — 이 형태가 아닌 국가코드
# (OpenAQ 의 미상 국가 "-99" 등)는 한 건만 섞여도 소비자 sync 가 전체 실패한다.
ISO2_RE = re.compile(r"[A-Z]{2}")

API_BASE = "https://api.openaq.org/v3"

PAGE_SIZE = int(os.environ.get("OPENAQ_PAGE_SIZE", "1000"))  # v3 max
MAX_PAGES_PER_RESOURCE = int(os.environ.get("OPENAQ_MAX_PAGES_PER_RESOURCE", "50"))
RATE_PER_MIN = float(os.environ.get("OPENAQ_RATE_PER_MIN", "55"))  # 60 상한 아래 여유
BUDGET_REQUESTS = int(os.environ.get("OPENAQ_BUDGET_REQUESTS", "500"))
HTTP_TIMEOUT_SECONDS = float(os.environ.get("OPENAQ_HTTP_TIMEOUT_SECONDS", "30"))
MAX_ATTEMPTS = int(os.environ.get("OPENAQ_MAX_ATTEMPTS", "5"))
# 이 키의 소비자는 하나가 아니다 — e2-sidecar `openaq_shadow` 가 매시 :15 에
# ~120 req(1,200ms 간격 = 50/분)를 같은 키로 쓴다. 분당 페이싱만으로는 남의
# 소비를 알 수 없어(둘이 겹치면 50+55=105/분 > 60/분 상한), 서버가 응답에
# 실어 보내는 잔여 할당(`x-ratelimit-*`)을 정본으로 함께 본다.
# 반복 초과는 "일시적 또는 영구적 차단"이다(docs.openaq.org rate-limits).
RATE_RESERVE_REQUESTS = int(os.environ.get("OPENAQ_RATE_RESERVE", "5"))
RATE_RESET_MAX_WAIT_SECONDS = float(os.environ.get("OPENAQ_RATE_RESET_MAX_WAIT", "120"))
RATE_RESET_FALLBACK_SECONDS = float(os.environ.get("OPENAQ_RATE_RESET_FALLBACK", "60"))

# pm25 는 필수(0건이면 abort). pm10/no2/o3 는 있으면 좋은 보강 컬럼(best-effort).
PARAM_ORDER = ["pm25", "pm10", "no2", "o3"]
REQUIRED_PARAMS = {"pm25"}

# organize_data.py `_parse_openaq_multi` 가 소비하는 스키마 그대로 — 순서 변경 금지.
CSV_FIELDNAMES = [
    "station_id", "station_name", "lat", "lon", "timestamp_utc",
    "pm25", "parameter", "source", "pm10", "no2", "o3",
]

# 단위 정규화 — µg/m³ 표기 변형 + mg/m³ 만. ppm/ppb 는 몰질량+상태량이 있어야
# 변환 가능 → 인식 못 함(임의 추정 금지, 모노레포 collect_openaq_v3.py 와 동일 표).
_UNIT_TO_UGM3_FACTOR = {
    "µg/m³": 1.0, "ug/m3": 1.0, "ug/m³": 1.0, "µg/m3": 1.0, "ug.m-3": 1.0,
    "mg/m³": 1000.0, "mg/m3": 1000.0, "mg.m-3": 1000.0,
}


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def build_parameters_url(page: int = 1, page_size: int = 200) -> str:
    params = urllib.parse.urlencode({"limit": page_size, "page": page})
    return f"{API_BASE}/parameters?{params}"


def build_locations_url(page: int, page_size: int = PAGE_SIZE) -> str:
    params = urllib.parse.urlencode({"limit": page_size, "page": page})
    return f"{API_BASE}/locations?{params}"


def build_latest_url(parameter_id: int, page: int, page_size: int = PAGE_SIZE) -> str:
    params = urllib.parse.urlencode({"limit": page_size, "page": page})
    return f"{API_BASE}/parameters/{parameter_id}/latest?{params}"


def normalize_unit_to_ugm3(unit: str) -> float | None:
    """단위 문자열 → µg/m³ 배율. 인식 못 하면 None(그 파라미터 전체 skip)."""
    return _UNIT_TO_UGM3_FACTOR.get(unit.strip().lower().replace("μ", "µ"))


def parse_parameters_response(data: dict) -> dict[str, dict]:
    """`/v3/parameters` 응답 → {name.lower(): {"id": int, "units": str}}.

    id/name 누락 항목은 skip(전체 run crash 금지, 관측소 단위 격리와 동일 정신).
    """
    out: dict[str, dict] = {}
    for p in data.get("results", []) or []:
        pid = p.get("id")
        name = p.get("name")
        if pid is None or not name:
            continue
        try:
            out[str(name).lower()] = {"id": int(pid), "units": str(p.get("units", ""))}
        except (TypeError, ValueError):
            continue
    return out


def parse_locations_page(data: dict) -> tuple[dict[int, dict], int]:
    """`/v3/locations` 페이지 → ({loc_id: {"name":, "country_code":}}, country 누락 skip 수).

    좌표는 latest 결과 자체가 갖고 있어(§2) 여기선 이름·국가코드만 필요.
    """
    out: dict[int, dict] = {}
    skipped_no_country = 0
    for loc in data.get("results", []) or []:
        lid = loc.get("id")
        if lid is None:
            continue
        try:
            lid_int = int(lid)
        except (TypeError, ValueError):
            continue
        country = loc.get("country") or {}
        code = country.get("code")
        if not code:
            skipped_no_country += 1
            continue
        out[lid_int] = {"name": loc.get("name") or "", "country_code": str(code).upper()}
    return out, skipped_no_country


def parse_latest_page(data: dict) -> list[dict]:
    """`/v3/parameters/{id}/latest` 페이지 → [{locations_id, value, datetime_utc, lat, lon}].

    잘못된 레코드는 그 레코드만 skip(격리) — 페이지 전체를 버리지 않는다.
    """
    out: list[dict] = []
    for r in data.get("results", []) or []:
        lid = r.get("locationsId")
        raw_value = r.get("value")
        if lid is None or raw_value is None:
            continue
        try:
            lid_int = int(lid)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        coords = r.get("coordinates") or {}
        # 신뢰 경계: 좌표도 숫자 캐스팅으로 검증 — 비정상 타입(문자열/배열)이
        # 조립 단계 round() 까지 흘러가 전체 런을 죽이지 않도록 이 레코드에서
        # 격리한다(None 이면 조립 단계 skipped_no_coords 로 정직 집계).
        try:
            lat = float(coords["latitude"])
            lon = float(coords["longitude"])
        except (KeyError, TypeError, ValueError):
            lat = None
            lon = None
        dt = (r.get("datetime") or {}).get("utc")
        out.append({
            "locations_id": lid_int, "value": value,
            "datetime_utc": dt, "lat": lat, "lon": lon,
        })
    return out


def _header_int(headers, name: str) -> int | None:
    """헤더 값을 int 로. `email.message.Message`(대소문자 무시)와 dict 둘 다 받는다."""
    if headers is None:
        return None
    raw = None
    for key in (name, name.lower(), name.upper(), name.title()):
        try:
            raw = headers.get(key)
        except AttributeError:
            return None
        if raw is not None:
            break
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def reset_wait_seconds(raw: int | None) -> float:
    """`x-ratelimit-reset` → 지금부터 기다릴 초.

    **초-잔여로 확정**(2026-09-06 실측, run 34004248132 응답 헤더:
    `limit=60 used=1 remaining=59 reset=60` — 에폭이었다면 10자리다). 문서는
    "timestamp indicating when the rate limit period will reset" 이라고만 적고
    단위를 명시하지 않아 직전 커밋은 에폭 해석을 함께 들고 있었는데, 실측이
    나온 이상 돌지 않는 분기를 남겨두지 않는다.

    형식이 바뀌면 조용히 재해석하지 않고 경고한다 — 상한 클램프
    (`RATE_RESET_MAX_WAIT_SECONDS`)가 피해를 막고, 로그가 재확정 근거가 된다.
    """
    if raw is None or raw <= 0:
        return RATE_RESET_FALLBACK_SECONDS
    if raw > 3600:  # 60/분·2,000/시간 창 어느 쪽으로도 설명 안 되는 값
        print(f"::warning::x-ratelimit-reset={raw} 이 초 단위로 설명되지 않는다 "
              f"(형식 변경?) — 폴백 {RATE_RESET_FALLBACK_SECONDS:.0f}s 적용",
              file=sys.stderr)
        return RATE_RESET_FALLBACK_SECONDS
    return float(raw)


class RateLimiter:
    """분당 호출 페이싱 + 서버 보고 잔여 할당(`x-ratelimit-*`) 반영."""

    def __init__(self, per_min: float, reserve: int = RATE_RESERVE_REQUESTS):
        self.min_interval = 60.0 / per_min if per_min > 0 else 0.0
        self._next_at = 0.0
        self.reserve = reserve
        self.last_remaining: int | None = None
        self.reset_waits = 0
        self._logged_headers = False

    def observe(self, headers) -> None:
        """응답 헤더의 잔여 할당을 보고, 바닥나기 전에 창이 리셋될 때까지 쉰다.

        헤더가 없으면(구 API·테스트 더블) 아무것도 하지 않는다 — 기존 분당
        페이싱만으로 동작한다. 즉 이 기능은 페이싱을 *대체*하지 않고 덧댄다.
        """
        remaining = _header_int(headers, "x-ratelimit-remaining")
        if remaining is None:
            if not self._logged_headers:
                self._logged_headers = True
                print("  rate-limit 헤더 없음 — 분당 페이싱만으로 동작", file=sys.stderr)
            return
        if not self._logged_headers:
            self._logged_headers = True
            print(f"  rate-limit 헤더: limit={_header_int(headers, 'x-ratelimit-limit')} "
                  f"used={_header_int(headers, 'x-ratelimit-used')} remaining={remaining} "
                  f"reset={_header_int(headers, 'x-ratelimit-reset')} "
                  f"(reset 원시값 — 초/에폭 판별 근거)")
        self.last_remaining = remaining
        if remaining > self.reserve:
            return
        wait_s = min(reset_wait_seconds(_header_int(headers, "x-ratelimit-reset")),
                     RATE_RESET_MAX_WAIT_SECONDS)
        self.reset_waits += 1
        print(f"::warning::rate limit 잔여 {remaining} (예비 {self.reserve} 이하) — "
              f"{wait_s:.0f}s 대기 후 재개 (같은 키를 쓰는 다른 소비자 존재)")
        if wait_s > 0:
            time.sleep(wait_s)

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        if now < self._next_at:
            time.sleep(self._next_at - now)
            now = time.monotonic()
        self._next_at = now + self.min_interval


class RequestBudget:
    """`OPENAQ_BUDGET_REQUESTS` 소진 여부(no silent cap — 소진 시 호출부가 WARN)."""

    def __init__(self, limit: int):
        self.limit = limit
        self.spent = 0

    def exhausted(self) -> bool:
        return self.spent >= self.limit

    def spend(self) -> None:
        self.spent += 1


# ────────────────────────── HTTP (부수효과 — 테스트에선 urlopen 대체) ──────────────────────────

def _http_get_json(url: str, api_key: str, limiter: "RateLimiter | None" = None) -> dict:
    req = urllib.request.Request(url, headers={
        "X-API-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "AirLens-openaq-bulk/1.0 (+https://airlens.cloud)",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        headers = getattr(resp, "headers", None)  # 테스트 더블(BytesIO)엔 없다
    if limiter is not None:
        limiter.observe(headers)  # 연결을 닫은 뒤에 (대기가 소켓을 붙잡지 않게)
    return payload


def fetch_with_retry(url: str, api_key: str, limiter: "RateLimiter | None" = None) -> dict:
    """429/5xx 는 재시도, 4xx(키 오류 등)는 즉시 전파(재시도로 낫지 않음).

    429 는 `Retry-After` 헤더가 있으면 그 초만큼, 없으면 15s*시도수 대기.
    5xx/네트워크 오류는 지수 백오프(5s*2^n).
    """
    last_err: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _http_get_json(url, api_key, limiter)
        except urllib.error.HTTPError as e:
            if limiter is not None:
                limiter.observe(e.headers)  # 429 응답도 잔여/리셋을 싣고 온다
            if e.code == 429:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    wait_s = float(retry_after) if retry_after else 15.0 * (attempt + 1)
                except ValueError:
                    wait_s = 15.0 * (attempt + 1)
                last_err = e
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(wait_s)
                    continue
            elif e.code >= 500:
                last_err = e
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(5.0 * (2 ** attempt))
                    continue
            else:
                raise  # 4xx(키 오류·잘못된 파라미터 등) — 재시도 무의미, 즉시 전파
        except OSError as e:  # URLError, socket timeout — 전송 계층 일시 결함
            last_err = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(5.0 * (2 ** attempt))
                continue
    raise RuntimeError(f"request failed after {MAX_ATTEMPTS} attempts: {last_err}") from last_err


# ────────────────────────── main ──────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=".", help="출력 디렉터리 (기본 cwd)")
    ap.add_argument(
        "--countries", default=None,
        help="쉼표구분 ISO country code 필터 (기본 전체 — 수집된 전 국가 중 행 ≥1)",
    )
    ap.add_argument(
        "--date-tag", default=None,
        help="파일명 타임스탬프 오버라이드(YYYYMMDD, 기본 오늘 UTC — 재현성 테스트용)",
    )
    args = ap.parse_args(argv)

    api_key = os.environ.get("OPENAQ_API_KEY", "").strip()
    if not api_key:
        print("::warning::OPENAQ_API_KEY not set — OpenAQ bulk collection skipped this run "
              "(register a free key at openaq.org, then: gh secret set OPENAQ_API_KEY)")
        return 0  # graceful skip — 파일 쓰지 않음

    countries_filter: set[str] | None = None
    if args.countries:
        countries_filter = {c.strip().upper() for c in args.countries.split(",") if c.strip()}

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    budget = RequestBudget(BUDGET_REQUESTS)
    limiter = RateLimiter(RATE_PER_MIN)

    # 1) 파라미터 이름 → id/단위 해석 (하드코딩 금지).
    limiter.wait()
    budget.spend()
    try:
        params_data = fetch_with_retry(build_parameters_url(), api_key, limiter)
    except Exception as e:  # noqa: BLE001 — 전송 실패는 abort(파라미터 해석 없이 진행 불가)
        print(f"ERROR: /v3/parameters fetch failed: {e}", file=sys.stderr)
        return 1
    param_map = parse_parameters_response(params_data)
    if len(params_data.get("results", []) or []) >= 200:
        # 단일 페이지(200)만 읽는다 — 꽉 찼으면 뒤쪽 파라미터가 잘렸을 수 있음을
        # 자인한다(no silent cap). pm25/pm10/no2/o3 는 초반 id 라 실무 영향 낮음.
        print("::warning::/v3/parameters page full (200) — later parameters may be "
              "truncated (no silent cap)")
    if "pm25" not in param_map:
        print("ERROR: pm25 parameter id not resolvable from /v3/parameters — abort "
              "(API contract changed?)", file=sys.stderr)
        return 1

    # 2) 관측소 메타(이름·국가코드) — 글로벌 1회 순회.
    locations_meta: dict[int, dict] = {}
    skipped_no_country = 0
    for page in range(1, MAX_PAGES_PER_RESOURCE + 1):
        if budget.exhausted():
            print(f"::warning::request budget ({BUDGET_REQUESTS}) exhausted during "
                  f"/v3/locations listing — partial metadata ({len(locations_meta)} stations so far)")
            break
        limiter.wait()
        budget.spend()
        try:
            data = fetch_with_retry(build_locations_url(page), api_key, limiter)
        except Exception as e:  # noqa: BLE001 — 페이지 단위 격리
            print(f"WARN: /v3/locations page {page} failed: {e} "
                  f"(keeping {len(locations_meta)} so far)", file=sys.stderr)
            break
        page_meta, page_skipped = parse_locations_page(data)
        locations_meta.update(page_meta)
        skipped_no_country += page_skipped
        results = data.get("results", [])
        if len(results) < PAGE_SIZE:
            break
        if page == MAX_PAGES_PER_RESOURCE:
            print(f"::warning::hit MAX_PAGES_PER_RESOURCE={MAX_PAGES_PER_RESOURCE} for "
                  f"/v3/locations — remaining locations not fetched (no silent cap)")
    print(f"  locations: {len(locations_meta)} stations with country code "
          f"(skipped_no_country={skipped_no_country})")

    # 3) 파라미터별 최신값 벌크 (pm25 필수, pm10/no2/o3 best-effort).
    per_param_readings: dict[str, dict[int, dict]] = {}
    for pname in PARAM_ORDER:
        info = param_map.get(pname)
        if info is None:
            print(f"::warning::parameter '{pname}' not found in /v3/parameters — skipped")
            continue
        factor = normalize_unit_to_ugm3(info["units"])
        if factor is None:
            print(f"::warning::parameter '{pname}' unit '{info['units']}' not recognized "
                  f"(only ug/m3, mg/m3 supported) — skipped (no silent scale guess)")
            continue

        readings: dict[int, dict] = {}
        for page in range(1, MAX_PAGES_PER_RESOURCE + 1):
            if budget.exhausted():
                print(f"::warning::request budget ({BUDGET_REQUESTS}) exhausted during "
                      f"'{pname}' /latest — partial ({len(readings)} stations so far)")
                break
            limiter.wait()
            budget.spend()
            try:
                data = fetch_with_retry(build_latest_url(info["id"], page), api_key, limiter)
            except Exception as e:  # noqa: BLE001 — 페이지 단위 격리
                print(f"WARN: '{pname}' /latest page {page} failed: {e} "
                      f"(keeping {len(readings)} so far)", file=sys.stderr)
                break
            for r in parse_latest_page(data):
                lid = r["locations_id"]
                dt = r["datetime_utc"]
                prev = readings.get(lid)
                # 한 관측소가 같은 파라미터를 여러 sensor 로 보고할 때(멀티 계측기)
                # 최신 시각 채택 — 페이지 순서로 조용히 덮어쓰지 않는다.
                # 타임스탬프 없는 새 레코드는 기존 레코드를 절대 덮어쓰지 않는다
                # (유효한 시각을 가진 값이 시각 없는 값으로 교체되면 조립 단계의
                # no-timestamp skip 이 그 관측소를 통째로 지워버린다).
                if prev is not None:
                    if not dt:
                        continue
                    if prev["datetime_utc"] and dt <= prev["datetime_utc"]:
                        continue
                readings[lid] = {
                    "value": r["value"] * factor, "datetime_utc": dt,
                    "lat": r["lat"], "lon": r["lon"],
                }
            results = data.get("results", [])
            if len(results) < PAGE_SIZE:
                break
            if page == MAX_PAGES_PER_RESOURCE:
                print(f"::warning::hit MAX_PAGES_PER_RESOURCE={MAX_PAGES_PER_RESOURCE} for "
                      f"'{pname}' /latest — remaining stations not fetched (no silent cap)")
        per_param_readings[pname] = readings
        print(f"  {pname}: {len(readings)} stations")

    pm25_readings = per_param_readings.get("pm25", {})
    if not pm25_readings:
        print("ERROR: 0 pm25 readings collected — abort (keep last-good, no empty publish).",
              file=sys.stderr)
        return 1

    # 4) 조립 + 국가별 그룹핑. pm25 없는 관측소는 애초에 순회 대상이 아니므로
    #    pm25 null 행은 절대 생기지 않는다(_parse_openaq_multi 의 dropna 정합).
    by_country: dict[str, list[dict]] = defaultdict(list)
    skipped_no_location_meta = 0
    skipped_no_timestamp = 0
    skipped_no_coords = 0
    skipped_country_filtered = 0
    dropped_non_iso_rows = 0
    dropped_non_iso_codes: set[str] = set()
    for lid, pm25_r in pm25_readings.items():
        meta = locations_meta.get(lid)
        if meta is None:
            skipped_no_location_meta += 1
            continue
        cc = meta["country_code"]
        if not ISO2_RE.fullmatch(cc):
            dropped_non_iso_rows += 1
            dropped_non_iso_codes.add(cc)
            continue
        if countries_filter and cc not in countries_filter:
            skipped_country_filtered += 1
            continue
        lat, lon = pm25_r["lat"], pm25_r["lon"]
        if lat is None or lon is None:
            skipped_no_coords += 1
            continue
        if not pm25_r["datetime_utc"]:
            # organize_data.py `_parse_openaq_multi` 가 timestamp_utc 빈 값을 dropna
            # 하므로, 여기서 안 쓰는 것이 "조용히 버려지는 행"보다 정직하다.
            skipped_no_timestamp += 1
            continue
        row = {
            "station_id": str(lid),
            "station_name": meta["name"],
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "timestamp_utc": pm25_r["datetime_utc"],
            "pm25": round(pm25_r["value"], 2),
            "parameter": "pm25",
            "source": "openaq",
            "pm10": "", "no2": "", "o3": "",
        }
        for extra in ("pm10", "no2", "o3"):
            extra_r = per_param_readings.get(extra, {}).get(lid)
            if extra_r is not None:
                row[extra] = round(extra_r["value"], 2)
        by_country[cc].append(row)

    if dropped_non_iso_rows:
        print(f"dropped {dropped_non_iso_rows} row(s) with non-ISO country codes: "
              f"{sorted(dropped_non_iso_codes)}")

    if not by_country:
        print("ERROR: 0 rows assembled after country grouping — abort.", file=sys.stderr)
        return 1

    # 5) 국가별 CSV + manifest 발행.
    #    CSV 는 월별 하위 폴더(YYYY-MM/)에 쓴다. HF 는 폴더당 파일 1만 개가 상한인데
    #    하루 ~149개국이라 평면 폴더는 2026-11 에 찬다(2026-10-10 실측 5,082개).
    #    manifest.json 은 prefix 바로 아래에 두고, 항목의 `path`(prefix 기준 상대
    #    경로)로 위치를 알린다. `name` 은 그대로라 소비자의 파일명 allowlist 가 유지된다.
    date_tag = args.date_tag or datetime.now(timezone.utc).strftime("%Y%m%d")
    month_dir = f"{date_tag[:4]}-{date_tag[4:6]}"
    (out_dir / month_dir).mkdir(parents=True, exist_ok=True)
    manifest_files = []
    for cc, rows in sorted(by_country.items()):
        fname = f"openaq_{cc}_multi_{date_tag}.csv"
        rel_path = f"{month_dir}/{fname}"
        fpath = out_dir / rel_path
        with open(fpath, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            w.writeheader()
            w.writerows(rows)
        digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
        manifest_files.append({
            "name": fname, "path": rel_path, "sha256": digest,
            "bytes": fpath.stat().st_size, "rows": len(rows),
        })

    manifest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": manifest_files,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    total_rows = sum(m["rows"] for m in manifest_files)
    print(f"Done: OpenAQ bulk {total_rows} rows across {len(manifest_files)} countries "
          f"(requests={budget.spent}/{budget.limit}, "
          f"skipped_no_country={skipped_no_country}, "
          f"skipped_no_location_meta={skipped_no_location_meta}, "
          f"skipped_no_coords={skipped_no_coords}, "
          f"skipped_no_timestamp={skipped_no_timestamp}, "
          f"skipped_country_filtered={skipped_country_filtered}) -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
