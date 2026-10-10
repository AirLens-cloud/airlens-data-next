#!/usr/bin/env python3
"""AirKorea 근실시간 관측소 수집기 — mac 무료 파이프라인용 point-reading 어댑터 (W5-b).

설계 배경(비공개 내부 노트 2026-07-16 — 동작 정의는 코드·테스트·계약):
§"무료 글로벌 소스 판정" — "AirKorea | 대한민국 관측소, 통상 1시간 | PM2.5, PM10, O3, NO2,
SO2, CO, CAI | 공공 API 무료 할당량 ... | 한국 관측 보정". §"스케줄" — 지역 관측소 1시간.

`mac_aq_adapter.py` 의 §docstring 이 명시하듯, 기존 `collect_airkorea.py` 는 ML 학습용
*bulk* 수집기(월/연 단위 CSV, `Data/3-raw-sources/`)이고 완전히 다른 모드다. 이 파일은
"지금 이 관측소의 최신 값 1개"를 받아 `build_point_reading()` 계약으로 조립하는 근실시간
경로다 — `collect_airkorea.py` 를 import 하지 않고 새 코드 경로로 작성한다.

두 API 호출:
  1. `MsrstnInfoInqireSvc/getMsrstnList` — 관측소 좌표 메타데이터. dmX/dmY 문서 필드명
     ("경도"/"위도")은 신뢰하지 않는다 — 실제 응답이 문서와 필드명-지리적 의미가
     반대라는 보고가 있는 알려진 문서 오류라, 각 값이 한국 위도/경도 범위 중 어디
     속하는지로 lat/lon 을 판별한다(`resolve_lat_lon`, TS 자매 구현
     `apps/web/supabase/functions/airkorea-collector/stationMeta.ts` 의
     `resolveLatLon` 미러). realtime 엔드포인트는 좌표를 안 주므로 join 해야 하는데,
     측정소명 단독 join은 전국 동명 구(예: '중구'가 서울/부산/대구/인천/대전/울산
     6곳에 실재)의 좌표를 서로 교차오염시킨다 — `addr` 필드에서 시도명을 파생해
     (시도+측정소명) 복합키로 join 한다(`resolve_sido_from_addr` + `coord_map_key`).
     시도 판별 실패 항목은 스킵 — bare name 으로 잘못 넣지 않는다.
  2. `ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty` — 시도별 현재 시간 관측값(17회 호출).

가스(O3/NO2/SO2/CO)는 AirKorea 원자료가 ppm 이라 표준상태(25°C, 1atm, 몰부피 24.45 L/mol)
가정으로 µg/m³ 변환한다(EPA/WHO 관례 공식, mac_aq_adapter 에는 이 변환이 없어 여기서
자체 구현 — PM2.5/PM10 은 이미 µg/m³ 라 변환 불필요).

정직성 (형제 수집기와 동일 원칙):
  - 좌표를 못 찾은 관측소, pollutant 값이 전부 "-"(결측)인 관측소는 그 관측소만 skip —
    좌표를 추정하거나 0 으로 채우지 않는다.
  - 전 관측소가 실패하면(모든 sido 호출 실패 또는 join 후 0건) exit 1 — last-good 유지.
  - 각 소스(AirKorea/EEA)는 완전히 독립된 프로세스 — 한쪽 실패가 다른 쪽에 영향 없음.
  - `AIRKOREA_API_KEY` 값은 로그에 출력하지 않는다(§2 secret 가드) — `os.environ` 직접
    참조(GitHub Actions 주입 관례), `secrets/*` 파일을 코드에서 열지 않는다.

검증 게이트(§"첫 실행 전 재확인 대상"): `addr` 필드가 축약형("서울")/정식 명칭
("서울특별시") 중 어느 쪽으로 오는지 라이브 응답으로 확인 못 함 — `SIDO_ALIASES` 가
양쪽 다 정규화하므로 어느 쪽이 오든 안전하나, 표에 없는 신규 표기(행정구역 개편 등)가
나오면 그 항목은 스킵된다(자체 방어 — fabricate 하지 않음).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mac_aq_adapter as adapter  # noqa: E402

BASE_URL = "http://apis.data.go.kr/B552584"
STATION_LIST_ENDPOINT = f"{BASE_URL}/MsrstnInfoInqireSvc/getMsrstnList"
REALTIME_ENDPOINT = f"{BASE_URL}/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty"

OUTPUT_PATH = os.environ.get("MAC_AIRKOREA_OUTPUT_PATH", "airkorea-observations-snapshot.json")
REQUEST_DELAY = float(os.environ.get("MAC_AIRKOREA_REQUEST_DELAY", "1.0"))

SIDO_LIST = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산",
    "경기", "강원", "충북", "충남", "전북", "전남",
    "경북", "경남", "제주", "세종",
]

ATTRIBUTION = "Korea Environment Corporation (AirKorea) — data.go.kr public API"
EXPECTED_POLLUTANT_COUNT = 6  # pm25, pm10, o3, no2, so2, co (AirKorea 는 pm1 미제공)
FRESHNESS_CUTOFF_HOURS = 1  # design SOT §"스케줄": 지역 관측소 1시간 주기

# pollutant_key -> (AirKorea item field, 이미 µg/m3 인가, 몰질량 g/mol — ppm 변환용)
POLLUTANT_FIELDS: dict[str, tuple[str, bool, float | None]] = {
    "pm25": ("pm25Value", True, None),
    "pm10": ("pm10Value", True, None),
    "o3": ("o3Value", False, 48.00),
    "no2": ("no2Value", False, 46.0055),
    "so2": ("so2Value", False, 64.066),
    "co": ("coValue", False, 28.01),
}

_STANDARD_MOLAR_VOLUME_L_MOL = 24.45  # 25°C, 1 atm (EPA/WHO ppm<->µg/m3 변환 관례)

# 한국 본토 + 제주 + 부속 도서 커버 범위(여유 포함) — TS 자매 구현
# `apps/web/supabase/functions/airkorea-collector/stationMeta.ts` 의
# KOREA_LAT_RANGE/KOREA_LON_RANGE 와 동일 값.
_KOREA_LAT_RANGE = (32.0, 40.0)
_KOREA_LON_RANGE = (123.0, 133.0)

# 광역시/도 표기 별칭. getCtprvnRltmMesureDnsty 의 sidoName 파라미터(SIDO_LIST)는
# 축약형("서울"/"경기")을 쓰는데, getMsrstnList 의 addr 필드가 축약형/정식 명칭
# ("서울특별시"/"경기도") 중 어느 쪽으로 오는지 라이브 API 호출로 확인 못 했다
# (문서·웹 소스가 서로 다른 표기를 보고) — 추측 대신 양쪽 다 축약형으로 정규화해
# 어느 쪽이 와도 안전하게 처리한다. 표에 없는 접두 토큰은 None(미해결) — 잘못된
# 시도로 매칭시키지 않는다. 2023/2024 행정구역 개편 별칭(강원특별자치도/
# 전북특별자치도) 포함. TS 자매 구현 `stationMeta.ts` 의 SIDO_ALIASES 와 동일.
SIDO_ALIASES: dict[str, str] = {
    "서울": "서울", "서울특별시": "서울",
    "부산": "부산", "부산광역시": "부산",
    "대구": "대구", "대구광역시": "대구",
    "인천": "인천", "인천광역시": "인천",
    "광주": "광주", "광주광역시": "광주",
    "대전": "대전", "대전광역시": "대전",
    "울산": "울산", "울산광역시": "울산",
    "세종": "세종", "세종특별자치시": "세종",
    "경기": "경기", "경기도": "경기",
    "강원": "강원", "강원도": "강원", "강원특별자치도": "강원",
    "충북": "충북", "충청북도": "충북",
    "충남": "충남", "충청남도": "충남",
    "전북": "전북", "전라북도": "전북", "전북특별자치도": "전북",
    "전남": "전남", "전라남도": "전남",
    "경북": "경북", "경상북도": "경북",
    "경남": "경남", "경상남도": "경남",
    "제주": "제주", "제주도": "제주", "제주특별자치도": "제주",
}


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def ppm_to_ugm3(ppm: float, molar_mass_g_mol: float,
                 molar_volume_l_mol: float = _STANDARD_MOLAR_VOLUME_L_MOL) -> float:
    """ppm(부피비) → µg/m³. 표준상태(25°C, 1atm) 가정 — EPA/WHO 관례 공식.

    µg/m³ = ppm × MW × 1000 / molar_volume. 예: O3 1ppm(MW=48) → ≈1963 µg/m³
    (공인 환산값과 일치).
    """
    if molar_mass_g_mol <= 0 or molar_volume_l_mol <= 0:
        raise ValueError("molar_mass_g_mol / molar_volume_l_mol must be > 0")
    return ppm * molar_mass_g_mol * 1000.0 / molar_volume_l_mol


def parse_airkorea_value(raw) -> float | None:
    """AirKorea item 값 파싱. "-"/빈문자/None = 결측(None) — 0 으로 메우지 않는다."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _in_range(v: float, lo: float, hi: float) -> bool:
    return lo <= v <= hi


def resolve_lat_lon(a: float | None, b: float | None) -> tuple[float, float] | None:
    """dmX/dmY 문서 필드명을 신뢰하지 않고 값 범위로 lat/lon 판별.

    공공데이터포털 문서는 dmX="경도"/dmY="위도"라 하나, 다수 개발자가 실제 응답의
    필드명-지리적 의미가 반대라고 보고한 알려진 문서 오류다(TS 자매 구현
    `apps/web/supabase/functions/airkorea-collector/stationMeta.ts` 의 resolveLatLon
    참조). 필드명을 신뢰하는 대신 각 값이 한국 위도 범위([32,40])와 경도 범위
    ([123,133]) 중 어디 속하는지로 lat/lon 을 판별한다. 모호하거나(둘 다 같은
    범위에 속하거나 어느 쪽도 범위 밖) None 반환 — 잘못된 좌표를 지어내지 않는다.
    """
    if a is None or b is None:
        return None
    a_is_lat = _in_range(a, *_KOREA_LAT_RANGE)
    b_is_lat = _in_range(b, *_KOREA_LAT_RANGE)
    a_is_lon = _in_range(a, *_KOREA_LON_RANGE)
    b_is_lon = _in_range(b, *_KOREA_LON_RANGE)
    if a_is_lat and b_is_lon:
        return (a, b)
    if b_is_lat and a_is_lon:
        return (b, a)
    return None


def resolve_sido_from_addr(addr: str | None) -> str | None:
    """addr 첫 토큰(공백 split)에서 시도명을 정규화. 별칭 표에 없으면 None —

    없는 시도로 지어내지 않는다.
    """
    if not addr:
        return None
    tokens = addr.strip().split()
    if not tokens:
        return None
    return SIDO_ALIASES.get(tokens[0])


def coord_map_key(sido: str, station_name: str) -> str:
    """좌표 사전 조회 키 — 시도+측정소명 복합키.

    측정소명 단독으로 조회하면 전국 동명 구(예: '중구'는 서울/부산/대구/인천/
    대전/울산 6곳에 실재, '남구'/'서구'/'북구'/'강서구' 등도 여러 시도에 중복)가
    먼저 처리된 다른 시도의 좌표를 잘못 받는다 — 범위 검증(resolve_lat_lon)으로도
    못 거른다(둘 다 한국 영토 내 유효 좌표라서). 시도+측정소명 복합키로 고정해
    이 충돌을 막는다.
    """
    return f"{sido}|{station_name}"


def parse_station_list_response(data: dict) -> dict[str, tuple[float, float]]:
    """`getMsrstnList` 응답 → {sido|stationName: (lat, lon)} 복합키 매핑.

    시도 판별 실패(addr 미해석) 또는 좌표 판별 실패(resolve_lat_lon 모호) 항목은
    그 관측소만 skip — 좌표를 추정하거나 엉뚱한 시도의 좌표로 fabricate 하지
    않는다. 같은 복합키가 중복되면 먼저 처리된 항목을 유지한다.
    """
    coords: dict[str, tuple[float, float]] = {}
    items = data.get("response", {}).get("body", {}).get("items", [])
    for item in items:
        name = (item.get("stationName") or "").strip()
        if not name:
            continue
        sido = resolve_sido_from_addr(item.get("addr"))
        if not sido:
            continue
        key = coord_map_key(sido, name)
        if key in coords:
            continue
        try:
            a = float(item.get("dmX"))
            b = float(item.get("dmY"))
        except (TypeError, ValueError):
            continue
        coord = resolve_lat_lon(a, b)
        if coord:
            coords[key] = coord
    return coords


def parse_datetime_kst_to_iso_utc(data_time: str) -> str:
    """AirKorea `dataTime`("YYYY-MM-DD HH:MM", KST) → ISO8601 UTC("...Z")."""
    dt_kst = datetime.strptime(data_time.strip(), "%Y-%m-%d %H:%M")
    dt_utc = dt_kst.replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_pollutants_for_item(item: dict) -> dict:
    """station item(raw dict) → adapter.pollutant_value() 로 조립된 pollutants dict.

    결측(None) 필드는 그냥 제외(부분 pollutants 는 정상 — 관측소마다 보고 항목이 다를 수
    있다). 전부 결측이면 빈 dict 반환 — 호출자가 그 관측소를 skip 한다.
    """
    pollutants: dict = {}
    for key, (field, already_ugm3, molar_mass) in POLLUTANT_FIELDS.items():
        raw = parse_airkorea_value(item.get(field))
        if raw is None:
            continue
        if already_ugm3:
            value_ugm3 = raw
            conversion = "direct ug/m3 (AirKorea original unit)"
        else:
            value_ugm3 = ppm_to_ugm3(raw, molar_mass)
            conversion = f"ppm x {molar_mass:g} x 1000 / {_STANDARD_MOLAR_VOLUME_L_MOL:g} (STP 25C/1atm)"
        pollutants[key] = adapter.pollutant_value(value_ugm3, field, conversion)
    return pollutants


def assemble_station_reading(
    sido: str,
    station_name: str,
    coords: dict[str, tuple[float, float]],
    item: dict,
    generated_at: str,
) -> dict | None:
    """(시도+측정소명) 복합키 좌표 join + pollutants 조립 + envelope.

    좌표 없음/pollutants 전부 결측 = None(skip). `sido` 는 realtime 조회에 쓰인
    `SIDO_LIST` 원소 그대로 전달 — `coord_map_key` 가 station coord map 구축 시
    쓴 `resolve_sido_from_addr` 정규화 결과(`SIDO_ALIASES` 축약형)와 동일 값역이라
    직접 매칭된다.
    """
    key = coord_map_key(sido, station_name)
    if key not in coords:
        return None
    lat, lon = coords[key]

    pollutants = build_pollutants_for_item(item)
    if not pollutants:
        return None

    data_time = item.get("dataTime", "").strip()
    if not data_time:
        return None
    try:
        observed_at = parse_datetime_kst_to_iso_utc(data_time)
    except ValueError:
        return None

    observed_dt = datetime.strptime(observed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    generated_dt = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (generated_dt - observed_dt).total_seconds() / 3600)
    expires_at = (observed_dt + timedelta(hours=FRESHNESS_CUTOFF_HOURS * 2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    quality = adapter.estimate_quality(
        pollutant_count=len(pollutants),
        expected_count=EXPECTED_POLLUTANT_COUNT,
        age_hours=age_hours,
    )
    envelope = adapter.build_envelope(
        kind="observation",
        source="AirKorea",
        source_version=data_time,
        generated_at=generated_at,
        observed_at=observed_at,
        valid_at=observed_at,
        expires_at=expires_at,
        resolution_km=1.0,  # 관측소 point — 격자 아님(대표 반경 근사)
        attribution=ATTRIBUTION,
        quality=quality,
    )
    reading = adapter.build_point_reading(envelope, lat=lat, lon=lon, pollutants=pollutants)
    errors = adapter.validate_point_reading(reading)
    if errors:
        raise ValueError(f"assembled reading failed validation for {sido}|{station_name}: {errors}")
    return reading


# ────────────────────────────── IO (네트워크, 미테스트 — 형제 관례) ──────────────────────────────

def load_api_key() -> str:
    """`AIRKOREA_API_KEY` 환경변수 참조(GitHub Actions 주입 관례) — secrets/ 파일 직접 열지 않음."""
    key = os.environ.get("AIRKOREA_API_KEY")
    if not key:
        raise RuntimeError("AIRKOREA_API_KEY not set in environment")
    return key


def api_call(endpoint: str, params: dict[str, str]) -> dict | None:
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{endpoint}?{query}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") != "00":
            print(f"  API error: {header.get('resultMsg')}", file=sys.stderr)
            return None
        return data
    except Exception as e:  # noqa: BLE001
        print(f"  Request failed ({endpoint}): {e}", file=sys.stderr)
        return None


def fetch_station_coords(api_key: str) -> dict[str, tuple[float, float]]:
    """`getMsrstnList` 전체 페이지 순회 — 관측소 좌표 메타데이터(정적, 자주 안 바뀜)."""
    coords: dict[str, tuple[float, float]] = {}
    page = 1
    total = None
    while True:
        params = {
            "serviceKey": api_key,
            "returnType": "json",
            "numOfRows": "100",
            "pageNo": str(page),
            "ver": "1.0",
        }
        data = api_call(STATION_LIST_ENDPOINT, params)
        if not data:
            break
        body = data.get("response", {}).get("body", {})
        if total is None:
            total = body.get("totalCount", 0)
        page_coords = parse_station_list_response(data)
        if not page_coords and not body.get("items"):
            break
        coords.update(page_coords)
        if len(coords) >= (total or 0) or not body.get("items"):
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    return coords


def fetch_sido_realtime_items(api_key: str, sido: str) -> list[dict]:
    """시도 1곳의 현재 시간 관측값(전 관측소). 실패 시 빈 리스트(그 시도만 skip)."""
    params = {
        "serviceKey": api_key,
        "returnType": "json",
        "numOfRows": "100",
        "pageNo": "1",
        "sidoName": sido,
        "ver": "1.0",
    }
    data = api_call(REALTIME_ENDPOINT, params)
    if not data:
        return []
    return data.get("response", {}).get("body", {}).get("items", [])


def main() -> int:
    try:
        api_key = load_api_key()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("  Fetching station coordinates...")
    coords = fetch_station_coords(api_key)
    if not coords:
        print("ERROR: station coordinate lookup failed (0 stations) — abort", file=sys.stderr)
        return 1
    print(f"    {len(coords)} station coordinates loaded")

    readings: list[dict] = []
    for sido in SIDO_LIST:
        items = fetch_sido_realtime_items(api_key, sido)
        for item in items:
            name = item.get("stationName", "")
            try:
                reading = assemble_station_reading(sido, name, coords, item, generated_at)
            except ValueError as e:
                print(f"  WARN {sido}|{name}: {e}", file=sys.stderr)
                continue
            if reading is not None:
                readings.append(reading)
        time.sleep(REQUEST_DELAY)

    if not readings:
        print("ERROR: 0 valid station readings assembled — abort (last-good kept)", file=sys.stderr)
        return 1

    tmp_path = f"{OUTPUT_PATH}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(readings, f, separators=(",", ":"))
    os.replace(tmp_path, OUTPUT_PATH)
    print(f"Done: AirKorea {len(readings)} station readings -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
