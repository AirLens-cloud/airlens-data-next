#!/usr/bin/env python3
"""EEA Up-To-Date(UTD/E2a) 근실시간 관측소 수집기 — mac 무료 파이프라인용 (W5-b).

설계 배경(비공개 내부 노트 2026-07-16 — 동작 정의는 코드·테스트·계약):
§"무료 글로벌 소스 판정" — "EEA Up-To-Date | 유럽 관측소, near-real-time | 국가별 관측
오염물질 | EEA 소유 자료는 CC BY | 유럽 관측 보정".

**EEA 단일 진입점** (2026-07-24, W2-a): 종전엔 `collect_eea.py` 가 나란히 있었으나
docstring 만 "E1a verified" 라 주장하고 실제로는 dataset=1(=UTD, 아래 enum)을 보내는
문서-동작 불일치였고 어떤 워크플로도 호출하지 않았다 → 삭제. 아카이브(E1a/Airbase)가
필요하면 이 파일에 `MAC_EEA_DATASET` 로 지정한다(창·예산 동반 조정 필요 — 아래 enum 주석).

API 구조(swagger 확인):
  - `POST /ParquetFile/urls` — {countries, cities, pollutants(문자열 리스트), dataset,
    dateTimeStart, dateTimeEnd, aggregationType} → parquet 파일 URL 리스트.
  - parquet 컬럼(EEA "How to use Air Quality Downloads" 공식 문서 확인):
    Samplingpoint(국가코드 + 국가별 자유 포맷 — "no vocabulary"),
    Pollutant(수치 코드, 아래 POLLUTANT_CODE_TO_KEY 의 EEA 어휘),
    Start/End/Value/Unit/Validity/Verification.
  - **Samplingpoint 가 EoI 코드를 담는다는 보장이 없다**(종전 docstring 의 "EoI 코드
    포함" 서술은 DE/FR/IT/NL/PL 만 본 가정이었다). 2026-07-29 실측: DE 는
    `DE/SPO.DE_DEBB049_PM2_dataGroup1` 로 코드를 담지만 ES 는 `ES/SP_39075006_10_49`
    처럼 국가 내부 숫자 ID 만 쓴다. substring 매칭만 쓰던 동안 ES 관측소 743 개가 전량
    조용히 탈락해 **발행물의 ES 가 0** 이었고, 커버리지엔 `visited, stations 0` 으로만
    찍혀 예산 부족과 구분되지 않았다. 폴백 인덱스는 ArcGIS `PopupInfo` 안의 다운로드
    링크에서 만든다 — `fetch_samplingpoint_index()`, 조인은 `resolve_station_eoi()`.
  - 관측소 좌표는 측정 parquet 에 없다 — 별도 station 메타데이터가 필요.
    `air.discomap.eea.europa.eu/.../AirQualityDownloadServiceEUMonitoringStations/MapServer/0`
    (ArcGIS REST, 필드 확인됨: AirQualityStation/Country/CountryCode/
    AirQualityStationEoICode/AQStationName, point geometry) 를 사용한다. 이 엔드포인트는
    응답을 2,000건에서 자르므로 `resultOffset` 페이지네이션이 필수다 — 아래
    `STATIONS_PAGE_SIZE` 주석.

**검증 게이트 해소분/잔여분**: 어느 dataset 값이 UTD 인지는 2026-07-23 DE 프로브로
확정됐다(아래 enum — `1`. 종전 docstring 의 `dataset=2` 서술은 그 실측 이전 가정이었다).
parquet 컬럼명·Samplingpoint 포맷·Unit 문자열(µg/m³ 표기 변형)은 여전히 공개 문서 기준
가정이다 — Unit 이 알려진 표기(µg/m³ 계열, mg/m³ 계열)와 일치하지 않으면 그 행은 조용히
스케일하지 않고 skip(정직성 원칙).

정직성 (형제 수집기와 동일 원칙):
  - Samplingpoint 에서 알려진 station EoI 코드를 못 찾으면(좌표 join 실패) 그 행 skip.
  - Unit 을 인식 못 하면(§검증 게이트) 그 행 skip — 임의로 배율 추정하지 않는다.
  - 국가 전체가 실패(parquet URL 0건, 다운로드 전부 실패)해도 다른 국가는 계속 처리—
    country 단위 독립 실패. 전체 국가가 실패하면 exit 1(last-good 유지).
  - AirKorea 수집기(`collect_mac_airkorea.py`)와 완전히 독립된 프로세스.
  - 신규 pip dep 0 — `pandas`/`pyarrow` 는 이미 `models/pyproject.toml` 선언 의존성.

스코프 결정: EEA 30개국 전체가 아니라, 시간당 job 비용을 낮추기 위해 기본값은 관측망 밀도가 높은
서유럽 6개국(DE/FR/IT/ES/PL/NL)으로 좁힌다 — `MAC_EEA_COUNTRIES` 환경변수(콤마 구분)로
확장 가능. 실제 시간당 스케줄/전체 30개국 커버는 W2(publish workflow) 스코프.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mac_aq_adapter as adapter  # noqa: E402

DOWNLOAD_API_BASE = "https://eeadmz1-downloads-api-appservice.azurewebsites.net"
STATIONS_QUERY_URL = (
    "https://air.discomap.eea.europa.eu/arcgis/rest/services/AirQuality/"
    "AirQualityDownloadServiceEUMonitoringStations/MapServer/0/query"
)

# ArcGIS MapServer 는 서버 maxRecordCount 로 응답을 자른다. 2026-07-24 실측:
# returnCountOnly=true → 7,020 관측소인데 기본 질의는 2,000 건 + exceededTransferLimit:true.
# 페이지네이션 없이 쓰면 관측소의 71% 가 좌표 조인에서 조용히 탈락하고, 그 관측소의 측정값은
# assemble 단계에서 통째로 버려진다(조용한 절단). resultOffset/resultRecordCount 로 끝까지 넘긴다.
STATIONS_PAGE_SIZE = int(os.environ.get("MAC_EEA_STATIONS_PAGE_SIZE", "2000"))
# 무한 루프 백스톱. 7,020 / 2,000 = 4 페이지라 여유 있게. 상한에 걸리면 WARN(no silent caps).
STATIONS_MAX_PAGES = int(os.environ.get("MAC_EEA_STATIONS_MAX_PAGES", "12"))

OUTPUT_PATH = os.environ.get("MAC_EEA_OUTPUT_PATH", "eea-utd-observations-snapshot.json")
# 국가별 커버리지 사이드카. 발행물의 `sources["eea-utd"].coverage` 로 실린다.
# 로그의 WARN 은 run 이 지나가면 사라진다 — 예산 부족과 네트워크 실패는 원인도 대응도
# 다른 사건인데, 사라지는 로그로는 어느 쪽이 며칠째 반복되는지 알 수 없다.
COVERAGE_PATH = os.environ.get("MAC_EEA_COVERAGE_PATH", "")

# EEA 30개국 중 관측망 밀도 높은 서유럽 6개국 기본값(스코프 결정).
_DEFAULT_COUNTRIES = ["DE", "FR", "IT", "ES", "PL", "NL"]
COUNTRIES = [
    c.strip().upper()
    for c in os.environ.get("MAC_EEA_COUNTRIES", ",".join(_DEFAULT_COUNTRIES)).split(",")
    if c.strip()
]

LOOKBACK_HOURS = float(os.environ.get("MAC_EEA_LOOKBACK_HOURS", "6"))  # 근실시간 좁은 창

# UTD 는 samplingpoint 당 parquet 1개 — 6개국이면 수천 파일. 순차 urlretrieve(무타임아웃)는
# 2026-07-23 첫 두 run 을 job timeout-minutes:20 에서 죽였다(EEA 단계 단독 17m49s).
# 병렬 + per-file 타임아웃 + 벽시계 예산으로 묶는다. 예산 초과분은 WARN 으로 드러낸다
# (no silent caps) — 부분 수집이어도 country 단위 독립 원칙은 그대로.
DOWNLOAD_WORKERS = int(os.environ.get("MAC_EEA_DOWNLOAD_WORKERS", "12"))
DOWNLOAD_TIMEOUT_SECONDS = float(os.environ.get("MAC_EEA_DOWNLOAD_TIMEOUT_SECONDS", "60"))
# 기본값은 워크플로(`mac-data-publish.yml` 의 `MAC_EEA_BUDGET_SECONDS`)와 **같아야** 한다.
# 480 이던 시절, 워크플로만 600 으로 두 번 튜닝돼(480→360→600) 로컬 실행과 CI 가 다른
# 예산으로 돌았다 — 로컬에서 재현한 커버리지가 CI 의 커버리지가 아니었다는 뜻이다.
# 동치는 `test_collect_mac_eea_utd.py` 가 워크플로 YAML 을 직접 파싱해 강제한다.
BUDGET_SECONDS = float(os.environ.get("MAC_EEA_BUDGET_SECONDS", "600"))
# 예산 만료 후 in-flight 다운로드를 기다려 주는 상한. urlopen 의 timeout 은 소켓 op 단위라
# 드립피딩 응답은 op 마다 리셋된다 — CI(run 29989225749/29990523694)에서 드레인이 240s+ 를
# 초과해 shell 백스톱(exit 124)까지 갔다. grace 넘기면 미완 future 는 유기(abandon)하고
# 결과 write 로 진행한다.
DRAIN_GRACE_SECONDS = float(os.environ.get("MAC_EEA_DRAIN_GRACE_SECONDS", "45"))
# 메타데이터 POST 의 벽시계 상한. urlopen(timeout=120) 은 소켓 op 단위 — CI(run
# 29992352098)에서 첫 국가의 ParquetFile/urls 응답이 드립피딩으로 600s 내내 갇혀
# budget deadline(호출 사이에서만 검사)이 영영 안 왔다. future.result 벽시계로 끊는다.
API_WALL_TIMEOUT_SECONDS = float(os.environ.get("MAC_EEA_API_WALL_TIMEOUT_SECONDS", "120"))
FRESHNESS_CUTOFF_HOURS = 2  # design SOT §"스케줄": 지역 관측소 1시간 주기 + 여유

# 2026-07-23 실측 enum (DE 프로브, 반환 blob 컨테이너·End 범위로 확정):
#   1 = UTD/E2a 근실시간 (올해분, End max = now) ← 기본값
#   2 = E1a 검증 아카이브 (airquality-p-e1a, 2013→작년말) — 종전 오설정. 12년×105k행
#       파일을 받아 OOM + "0 fresh rows" 의 근본원인이었다
#   3 = Airbase 역사 (airquality-p-airbase, 1999→2012)
#
# 아카이브(2/3) 는 근실시간 창(LOOKBACK_HOURS)·신선도 컷오프와 전제가 달라 그대로
# 돌리면 위 OOM 경로로 되돌아간다. 창·예산을 함께 조정할 때만 사용할 것.
EEA_DATASET = int(os.environ.get("MAC_EEA_DATASET", "1"))

ATTRIBUTION = "European Environment Agency (EEA) Up-To-Date air quality data — CC BY 4.0"

# EEA 어휘 pollutant 수치 코드.
POLLUTANT_CODE_TO_KEY: dict[str, str] = {
    "6001": "pm25",
    "5": "pm10",
    "8": "no2",
    "7": "o3",
    "1": "so2",
    "10": "co",
}
POLLUTANT_KEY_TO_EEA_NAME: dict[str, str] = {
    "pm25": "PM2.5", "pm10": "PM10", "no2": "NO2", "o3": "O3", "so2": "SO2", "co": "CO",
}
EXPECTED_POLLUTANT_COUNT = len(POLLUTANT_CODE_TO_KEY)

# Unit 문자열 정규화 — 인식 못 하면 skip(정직성, 임의 배율 추정 금지).
_UNIT_TO_UGM3_FACTOR = {
    "ug.m-3": 1.0, "ug/m3": 1.0, "µg/m³": 1.0, "ug/m³": 1.0,
    "mg.m-3": 1000.0, "mg/m3": 1000.0, "mg/m³": 1000.0,
}


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def rotate_countries(countries: list[str], utc_hour: int) -> list[str]:
    """국가 순회 시작점을 UTC 시로 결정론적으로 회전.

    고정 순서면 예산이 앞쪽 국가에서 소진될 때 뒤쪽 국가는 *영구히* 굶는다 — 2026-07-27
    run 30229441547 에서 ES/PL/NL 셋 다 "budget exhausted before country started" 로
    매시간 통째 누락됐고, 다음 run 도 같은 순서라 누적되지도 않았다. 시작점을 시간마다
    옮기면 예산이 몇 국가밖에 못 커버해도 6개국이 시간에 걸쳐 모두 돈다.

    난수가 아니라 UTC 시를 쓰는 이유: 같은 시각의 재실행(재시도·workflow_dispatch)이
    같은 순서를 내야 로그 대조가 가능하다(재현성).
    """
    if not countries:
        return []
    offset = utc_hour % len(countries)
    return countries[offset:] + countries[:offset]


def build_request_body(countries: list[str], pollutant_names: list[str],
                        start_iso: str, end_iso: str, dataset: int = EEA_DATASET) -> dict:
    """`ParquetFile/urls` POST body(swagger 확인된 `ParquetDownloadDataDTO` shape)."""
    return {
        "countries": countries,
        "cities": [],
        "pollutants": pollutant_names,
        "dataset": dataset,
        "dateTimeStart": start_iso,
        "dateTimeEnd": end_iso,
        "aggregationType": "hour",
    }


def normalize_unit_to_ugm3(value: float, unit: str) -> float | None:
    """Unit 문자열 → µg/m³ 배율 적용. 인식 못 하면 None(그 행 skip, 임의 추정 금지)."""
    factor = _UNIT_TO_UGM3_FACTOR.get(unit.strip().lower())
    if factor is None:
        return None
    return value * factor


def extract_station_eoi_code(samplingpoint: str, known_eoi_codes: set[str]) -> str | None:
    """Samplingpoint 문자열 안에서 알려진 station EoI 코드를 찾는다(포맷이 국가별 자유— 정확한
    구분자를 가정하지 않고 substring 매칭. 여러 매치 시 가장 긴(더 구체적인) 코드 채택).

    **이 경로만으로는 부족한 국가가 있다** — ES 의 Samplingpoint 는 `ES/SP_39075006_10_49`
    처럼 EoI 코드(`ES1580A` 꼴)를 아예 담지 않고 국가 내부 숫자 ID 만 쓴다. 그런 국가는
    `resolve_station_eoi()` 의 인덱스 폴백이 받는다.
    """
    if not samplingpoint:
        return None
    sp_upper = samplingpoint.upper()
    matches = [code for code in known_eoi_codes if code and code.upper() in sp_upper]
    if not matches:
        return None
    return max(matches, key=len)


def resolve_station_eoi(
    samplingpoint: str,
    known_eoi_codes: set[str],
    sp_index: dict[str, str] | None = None,
) -> str | None:
    """Samplingpoint → EoI 코드. substring 매칭 우선, 실패 시 samplingpoint 인덱스 폴백.

    순서가 중요하다 — 2026-07-29 6개국 실측에서 두 경로의 커버리지가 서로를 완전히
    덮지 못했다: ES 는 substring 0/6·인덱스 6/6 인 반면, PL 은 substring 7/7·인덱스 6/7
    이었다(카탈로그에 아직 안 실린 신규 관측소). 어느 하나만 쓰면 반대쪽에서 조용히 잃는다.
    """
    code = extract_station_eoi_code(samplingpoint, known_eoi_codes)
    if code is not None:
        return code
    if not sp_index or not samplingpoint:
        return None
    return sp_index.get(samplingpoint) or sp_index.get(samplingpoint.upper())


def parse_stations_geojson_response(data: dict) -> dict[str, tuple[float, float]]:
    """ArcGIS `query?f=json` 응답 → {AirQualityStationEoICode: (lat, lon)}.

    feature.geometry = {"x": lon, "y": lat}(query 시 outSR=4326 요청 전제). 코드/좌표
    누락 feature 는 skip.
    """
    coords: dict[str, tuple[float, float]] = {}
    for feature in data.get("features", []):
        attrs = feature.get("attributes", {})
        geom = feature.get("geometry", {})
        eoi_code = attrs.get("AirQualityStationEoICode")
        lon = geom.get("x")
        lat = geom.get("y")
        if not eoi_code or lon is None or lat is None:
            continue
        try:
            coords[str(eoi_code)] = (float(lat), float(lon))
        except (TypeError, ValueError):
            continue
    return coords


def parse_flexible_iso(s: str) -> datetime:
    """parquet(pandas) `End` 컬럼 문자열(공백/T 구분자, tz 유무 혼재 가능) → aware datetime.

    다운로드된 parquet 의 실제 표기(공백 구분 `"2026-07-23 04:00:00"` 등)를 우리 envelope
    표준 표기(`...Z`)로 정규화하기 위한 관문 — pure, 테스트 대상.

    tz 오프셋이 있는 문자열(예: `"...+02:00"`, pandas 가 tz-aware parquet 컬럼을 문자열화한
    경우)도 그대로 보존해서 반환한다 — **호출자가 반드시 `format_utc_z()` 로 UTC 정규화 후
    포맷해야 한다.** naive `strftime` + 리터럴 `"Z"` 로 그대로 찍으면 로컬 시각(예: CEST
    `+02:00`)을 UTC 라고 잘못 라벨링해 `validAt` 이 실제보다 미래로 새는 버그가 된다
    (2026-07-25 PR7 — 라이브 사례: generatedAt 07:35Z 인데 validAt 이 몇 시간 뒤로 찍힘).
    """
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_utc_z(dt: datetime) -> str:
    """aware datetime → UTC `"...Z"` 표기. envelope 의 `validAt`/`observedAt`/`expiresAt` 은
    전부 UTC 로 명시하기로 한 계약(design SOT) — `parse_flexible_iso()` 가 돌려주는 datetime
    은 tz 오프셋이 UTC 가 아닐 수 있으므로(위 docstring 참조), 포맷 직전 반드시
    `astimezone(timezone.utc)` 로 정규화한다. pure — 테스트 대상.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def latest_rows_by_samplingpoint(rows: list[dict]) -> dict[str, dict]:
    """(Samplingpoint, Pollutant) 조합별 가장 최근(End 최대) 유효(Validity>=1) 행만 남긴다.

    `rows` 는 parquet 를 dict-of-columns 아닌 record 리스트로 미리 변환한 것(테스트에서
    실제 parquet 없이 직접 구성 가능) — 각 dict 는 Samplingpoint/Pollutant/End/Value/Unit/
    Validity 키를 갖는다.
    """
    latest: dict[str, dict] = {}
    latest_end_dt: dict[str, datetime] = {}
    for row in rows:
        validity = row.get("Validity")
        if validity is not None and validity < 1:
            continue  # 무효 플래그 — skip(0 으로 메우지 않고 그냥 제외)
        key = f"{row.get('Samplingpoint')}::{row.get('Pollutant')}"
        end_dt = parse_flexible_iso(str(row.get("End")))
        if key not in latest or end_dt > latest_end_dt[key]:
            latest[key] = row
            latest_end_dt[key] = end_dt
    return latest


def assemble_country_readings(
    latest_rows: dict[str, dict],
    station_coords: dict[str, tuple[float, float]],
    generated_at: str,
    sp_index: dict[str, str] | None = None,
    drops: dict[str, int] | None = None,
) -> list[dict]:
    """join 된 최신 행들 → point reading 리스트. 좌표/코드/단위 인식 실패 행은 개별 skip.

    관측소 단위 격리(AirKorea `main()` 의 per-station try/except 패턴과 정합):
    한 관측소의 조립 결과가 `validate_point_reading` 을 통과 못 해도 그 관측소만
    skip 하고 stderr 경고 후 나머지 관측소는 계속 처리한다 — 국가 전체를 버리지
    않는다(이전 버전은 여기서 raise 해 호출자가 국가 단위로 전부 폐기했다).

    `drops` 를 주면 관문별 탈락 수를 그 dict 에 누적한다. 커버리지 사이드카가 "stations 0"
    의 *이유* 를 싣기 위한 것 — 2026-07-29 이전엔 ES 가 `visited, stations 0` 으로만 찍혀
    예산 부족과 조인 실패가 구분되지 않았다(실제로는 조인 실패 100%).
    """
    known_codes = set(station_coords.keys())
    # station별로 여러 pollutant 를 모아 하나의 point reading 으로 합친다.
    by_station: dict[str, dict] = {}
    def _drop(reason: str) -> None:
        if drops is not None:
            drops[reason] = drops.get(reason, 0) + 1

    for row in latest_rows.values():
        pollutant_key = POLLUTANT_CODE_TO_KEY.get(str(row.get("Pollutant")))
        if pollutant_key is None:
            _drop("pollutant_code")
            continue
        eoi_code = resolve_station_eoi(
            str(row.get("Samplingpoint", "")), known_codes, sp_index,
        )
        if eoi_code is None or eoi_code not in station_coords:
            _drop("eoi_join")
            continue
        value_ugm3 = normalize_unit_to_ugm3(float(row["Value"]), str(row.get("Unit", "")))
        if value_ugm3 is None:
            _drop("unit")
            continue
        end_dt = parse_flexible_iso(str(row["End"]))
        entry = by_station.setdefault(eoi_code, {"pollutants": {}, "latest_end_dt": end_dt})
        entry["pollutants"][pollutant_key] = adapter.pollutant_value(
            value_ugm3, f"EEA Pollutant={row.get('Pollutant')}",
            f"unit={row.get('Unit')} -> ug/m3",
        )
        if end_dt > entry["latest_end_dt"]:
            entry["latest_end_dt"] = end_dt

    readings: list[dict] = []
    generated_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    for eoi_code, entry in by_station.items():
        if not entry["pollutants"]:
            continue
        lat, lon = station_coords[eoi_code]
        observed_dt = entry["latest_end_dt"]
        observed_at = format_utc_z(observed_dt)
        age_hours = max(0.0, (generated_dt - observed_dt).total_seconds() / 3600)
        expires_at = format_utc_z(observed_dt + timedelta(hours=FRESHNESS_CUTOFF_HOURS * 2))
        quality = adapter.estimate_quality(
            pollutant_count=len(entry["pollutants"]),
            expected_count=EXPECTED_POLLUTANT_COUNT,
            age_hours=age_hours,
        )
        envelope = adapter.build_envelope(
            kind="observation",
            source="EEA-UTD",
            source_version=f"E2a-UTD-{eoi_code}",
            generated_at=generated_at,
            observed_at=observed_at,
            valid_at=observed_at,
            expires_at=expires_at,
            resolution_km=1.0,
            attribution=ATTRIBUTION,
            quality=quality,
        )
        reading = adapter.build_point_reading(
            envelope, lat=lat, lon=lon, pollutants=entry["pollutants"]
        )
        errors = adapter.validate_point_reading(reading)
        if errors:
            print(f"  WARN {eoi_code}: assembled reading failed validation — skip station "
                  f"({errors})", file=sys.stderr)
            continue
        readings.append(reading)
    return readings


# ────────────────────────────── IO (네트워크, 미테스트 — 형제 관례) ──────────────────────────────

def api_post_json(endpoint: str, body: dict) -> bytes | None:
    url = f"{DOWNLOAD_API_BASE}/{endpoint}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )

    def _do() -> bytes:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()

    # 벽시계 상한 — 드립피딩이 urlopen 의 per-op timeout 을 계속 리셋해도 여기서 끊긴다.
    # 초과 시 스레드는 유기(__main__ 의 os._exit 가 join 을 회피).
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(_do).result(timeout=API_WALL_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        print(f"  EEA API error: {endpoint} exceeded wall-clock cap "
              f"({API_WALL_TIMEOUT_SECONDS:.0f}s) — abandoned", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  EEA API error: {e}", file=sys.stderr)
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def fetch_parquet_urls(country: str, start_iso: str, end_iso: str) -> list[str]:
    body = build_request_body([country], list(POLLUTANT_KEY_TO_EEA_NAME.values()),
                               start_iso, end_iso)
    resp = api_post_json("ParquetFile/urls", body)
    if not resp:
        return []
    text = resp.decode("utf-8-sig")
    return [ln.strip() for ln in text.strip().split("\n") if ln.strip().startswith("http")]


def build_stations_query_url(offset: int = 0, page_size: int = STATIONS_PAGE_SIZE) -> str:
    """관측소 메타데이터 질의 URL 1페이지분. pure — 테스트 대상."""
    params = urllib.parse.urlencode({
        "where": "1=1",
        "outFields": "AirQualityStationEoICode",
        "outSR": "4326",
        "f": "json",
        "resultOffset": offset,
        "resultRecordCount": page_size,
    })
    return f"{STATIONS_QUERY_URL}?{params}"


def paginate_station_coords(fetch_page) -> dict[str, tuple[float, float]]:
    """페이지 응답을 주는 콜러블로 전 페이지를 합친다 — 네트워크 없이 테스트 가능.

    정상 종료는 서버의 `exceededTransferLimit` 부재(=남은 게 없음). 두 가지 비정상도
    끊는다: (a) 서버가 resultOffset 을 무시해 같은 페이지를 되풀이하면 좌표 수가 늘지
    않는다 — 안 끊으면 상한까지 같은 2,000건을 반복 요청한다. (b) 중간 페이지 실패는
    그때까지 모은 좌표를 살려서 반환한다 — 전부 버리면 첫 페이지 실패와 구분이 안 된다.
    어느 경로든 WARN 으로 드러낸다(no silent caps).
    """
    coords: dict[str, tuple[float, float]] = {}
    offset = 0
    for _ in range(STATIONS_MAX_PAGES):
        try:
            data = fetch_page(offset)
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: EEA station page at offset {offset} failed: {e} — "
                  f"{len(coords)} coords collected so far", file=sys.stderr)
            return coords
        before = len(coords)
        coords.update(parse_stations_geojson_response(data))
        if not data.get("exceededTransferLimit"):
            return coords
        if len(coords) == before:
            print(f"  WARN: EEA station pagination made no progress at offset {offset} "
                  f"({len(coords)} coords) — server may ignore resultOffset", file=sys.stderr)
            return coords
        offset += STATIONS_PAGE_SIZE
    print(f"  WARN: EEA station pagination hit page cap ({STATIONS_MAX_PAGES}) with "
          f"{len(coords)} coords — more stations remain", file=sys.stderr)
    return coords


def fetch_station_coords() -> dict[str, tuple[float, float]]:
    def fetch_page(offset: int) -> dict:
        url = build_stations_query_url(offset)
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return paginate_station_coords(fetch_page)


def build_samplingpoint_index_query_url(
    offset: int = 0, page_size: int = STATIONS_PAGE_SIZE,
) -> str:
    """samplingpoint→EoI 인덱스 1페이지분 질의 URL. pure — 테스트 대상.

    같은 ArcGIS 레이어지만 좌표 질의와 **별 요청**이다: `PopupInfo` 는 관측소당 수 KB 의
    HTML 이라 전 관측소분이 ~20MB(2026-07-29 실측 4페이지 16.5s). 좌표 질의에 합치면
    ES 를 안 도는 run 까지 그 비용을 내야 한다 — 그래서 필요할 때만 부른다(§main lazy).
    geometry 는 좌표 질의가 이미 가져오므로 여기선 끈다.
    """
    params = urllib.parse.urlencode({
        "where": "1=1",
        "outFields": "AirQualityStationEoICode,PopupInfo",
        "returnGeometry": "false",
        "f": "json",
        "resultOffset": offset,
        "resultRecordCount": page_size,
    })
    return f"{STATIONS_QUERY_URL}?{params}"


# PopupInfo 안의 다운로드 링크에서 `<국가코드>/<samplingpoint>` 를 뽑는다.
# 실측 형태: ".../airquality-p-e1a/ES/SP_28009001_10_49.parquet" (컨테이너명은 dataset 별로
# 다르므로 앵커로 쓰지 않는다). parquet 의 `Samplingpoint` 컬럼 값이 정확히 `ES/SP_...` 라
# 그대로 조인 키가 된다.
_POPUP_SAMPLINGPOINT_RE = re.compile(r"/([A-Z]{2})/(SP[^/\"'\s]+?)\.parquet")


def parse_samplingpoint_index_response(data: dict) -> dict[str, str]:
    """ArcGIS `query?f=json` 응답 → {"<CC>/<samplingpoint>": AirQualityStationEoICode}."""
    index: dict[str, str] = {}
    for feature in data.get("features", []):
        attrs = feature.get("attributes", {})
        eoi_code = attrs.get("AirQualityStationEoICode")
        popup = attrs.get("PopupInfo") or ""
        if not eoi_code or not popup:
            continue
        for match in _POPUP_SAMPLINGPOINT_RE.finditer(str(popup)):
            index[f"{match.group(1)}/{match.group(2)}"] = str(eoi_code)
    return index


def paginate_samplingpoint_index(fetch_page) -> dict[str, str]:
    """`paginate_station_coords` 와 같은 종료·이상 규칙(진행 없음/페이지 실패/상한)."""
    index: dict[str, str] = {}
    offset = 0
    for _ in range(STATIONS_MAX_PAGES):
        try:
            data = fetch_page(offset)
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: EEA samplingpoint index page at offset {offset} failed: {e} — "
                  f"{len(index)} entries collected so far", file=sys.stderr)
            return index
        before = len(index)
        index.update(parse_samplingpoint_index_response(data))
        if not data.get("exceededTransferLimit"):
            return index
        if len(index) == before:
            print(f"  WARN: EEA samplingpoint index pagination made no progress at offset "
                  f"{offset} ({len(index)} entries)", file=sys.stderr)
            return index
        offset += STATIONS_PAGE_SIZE
    print(f"  WARN: EEA samplingpoint index hit page cap ({STATIONS_MAX_PAGES}) with "
          f"{len(index)} entries — more remain", file=sys.stderr)
    return index


def fetch_samplingpoint_index() -> dict[str, str]:
    def fetch_page(offset: int) -> dict:
        url = build_samplingpoint_index_query_url(offset)
        with urllib.request.urlopen(url, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return paginate_samplingpoint_index(fetch_page)


# 파서(assemble 단계)가 실제로 읽는 컬럼만. UTD parquet 는 samplingpoint 당 *전체
# 히스토리*(실측 DE: 2013→2025, 105k rows/file)를 담는다 — 요청 시간창은 URL 선별에만
# 작용. 전 컬럼 to_dict 는 파일당 ~100MB 로 인플레 × 12 워커 = 로컬 실측 peak 7.2GB,
# CI 16GB 러너 OOM(runner agent 사망 = "operation was canceled" ~5분 취소 3건의 원인).
#
# `Validity` 는 좁히면 안 되는 컬럼이다. 여기서 빠뜨리면 `latest_rows_by_samplingpoint()`
# 의 `Validity>=1` 가드가 `row.get("Validity") is None` 으로 떨어져 **조용히 통과**한다
# — 테스트는 손으로 만든 fixture 에 키가 있어 계속 초록불이라 결함이 안 드러난다.
# 2026-07-28 라이브 실측이 그 상태였다: EEA 는 당일 남은 시간대를 `Value=-999`,
# `Validity=-1` 플레이스홀더 행으로 미리 채워 두는데, 그 행이 "End 최대" 경합에서 이겨
# (a) DE 관측소 416/416 이 `validAt` 미래로 찍혀 하류 가드에 통째로 버려지고
# (b) 살아남은 국가에서는 `-999` 가 실측치로 발행됐다(발행 1,114 값 중 174 = 15.6%).
# 컬럼을 다시 좁힐 땐 `test_parquet_columns_cover_validity_guard` 를 먼저 볼 것.
PARQUET_COLUMNS = ["Samplingpoint", "Pollutant", "End", "Value", "Unit", "Validity"]


def download_parquet_rows(url: str, cutoff: datetime | None = None) -> list[dict]:
    """parquet 1개 다운로드 → record(dict) 리스트. pandas/pyarrow 로 파싱.

    cutoff 가 주어지면 End >= cutoff 행만 dict 화한다 — 히스토리 전체를 메모리에
    올리지 않는 것이 목적이라 필터는 반드시 to_dict *전에* 벡터 연산으로 수행.
    """
    import tempfile

    import pandas as pd

    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        # urlretrieve 는 타임아웃 인자가 없어 행 걸면 job 전체를 물고 죽는다.
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
            tmp.write(resp.read())
            tmp.flush()
        df = pd.read_parquet(tmp.name, columns=PARQUET_COLUMNS)
    if cutoff is not None:
        end_ts = pd.to_datetime(df["End"], errors="coerce", utc=True)
        df = df[end_ts >= cutoff]
    df = df.copy()
    df["End"] = df["End"].astype(str)
    return df.to_dict("records")


@dataclass
class CountryDownload:
    """국가 1개 다운로드 결과 + 왜 덜 받았는지.

    `dropped` 와 `failures` 는 **서로 다른 사건**이다. dropped 는 예산이 모자라 아예
    제출하지 못했거나 유기한 파일(→ 예산/스케줄 문제), failures 는 제출은 했는데
    개별 요청이 죽은 파일(→ 네트워크/원본 문제, FR 의 `urlopen error timed out` 다수).
    한 숫자로 합치면 예산을 올려야 할 상황과 올려도 소용없는 상황이 구분되지 않는다.
    """

    rows: list[dict] = field(default_factory=list)
    files_total: int = 0
    dropped: int = 0
    failures: int = 0


def download_country_parquets(
    country: str, urls: list[str], deadline: float, cutoff: datetime | None = None,
) -> CountryDownload:
    """국가 1개의 parquet URL 들을 워커 풀로 병렬 다운로드.

    deadline(time.monotonic 기준)이 지나면 새 다운로드를 더 제출하지 않고, 이미 제출된
    것만 마저 회수한다. 못 내려받은 개수는 WARN 으로 드러낸다 — 조용한 절단 금지.
    개별 실패는 기존 순차 루프와 동일하게 WARN + 계속.
    """
    rows: list[dict] = []
    failures = 0
    url_iter = iter(urls)
    abandoned = 0
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS)
    try:
        pending: dict[concurrent.futures.Future, str] = {}
        while True:
            while len(pending) < DOWNLOAD_WORKERS and time.monotonic() < deadline:
                url = next(url_iter, None)
                if url is None:
                    break
                pending[pool.submit(download_parquet_rows, url, cutoff)] = url
            if not pending:
                break
            done, _ = concurrent.futures.wait(
                pending,
                # deadline 이후엔 grace 상한 안에서만 기다린다 — 드립피딩 소켓이
                # 드레인을 무한정 붙들지 못하게 (CI 240s+ 초과 실측 대응).
                timeout=max(0.0, deadline + DRAIN_GRACE_SECONDS - time.monotonic()),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done and time.monotonic() >= deadline + DRAIN_GRACE_SECONDS:
                abandoned = len(pending)
                print(f"  WARN [{country}]: drain grace ({DRAIN_GRACE_SECONDS:.0f}s) "
                      f"exhausted — abandoning {abandoned} in-flight downloads",
                      file=sys.stderr)
                break
            for fut in done:
                url = pending.pop(fut)
                try:
                    rows.extend(fut.result())
                except Exception as e:  # noqa: BLE001
                    failures += 1
                    print(f"  WARN [{country}] {url}: download/parse failed — {e}",
                          file=sys.stderr)
    finally:
        # wait=False: 유기된 스레드를 join 하지 않는다. 스레드는 non-daemon 이라 일반
        # 인터프리터 종료를 붙들 수 있음 — main 끝의 os._exit 가 그 케이스를 끊는다.
        pool.shutdown(wait=False, cancel_futures=True)
    dropped = sum(1 for _ in url_iter) + abandoned
    if dropped:
        # deadline 파라미터 기준으로 판정된 결과라 예산 초 수치는 인용하지 않는다
        # (호출자가 다른 deadline 을 넘겨도 메시지가 정확하도록).
        print(f"  WARN [{country}]: wall-clock budget exhausted — "
              f"{dropped}/{len(urls)} parquet files not downloaded (partial country data)",
              file=sys.stderr)
    return CountryDownload(
        rows=rows, files_total=len(urls), dropped=dropped, failures=failures,
    )


def country_coverage(
    status: str, result: CountryDownload | None = None, stations: int = 0,
    drops: dict[str, int] | None = None,
) -> dict:
    """국가 1개의 커버리지 레코드.

    status 는 "이번 run 에 무슨 일이 있었나" 를 하나로 답한다:
      - `visited`            수집 성공(부분일 수 있음 — files_dropped/failures 확인)
      - `skipped_budget`     예산이 끊겨 국가를 시작조차 못 함
      - `no_files_in_window` 창 안에 parquet 0건 (원본 쪽 사정)
      - `no_rows`            받긴 했는데 파싱된 행 0

    `rows_dropped` 는 행이 조립 관문에서 탈락한 사유별 수 — `visited` + `stations 0` 이
    나왔을 때 예산 부족인지 조인/단위 실패인지 사이드카만 보고 가려낼 수 있게 한다.
    """
    return {
        "status": status,
        "stations": stations,
        "files_total": result.files_total if result else 0,
        "files_dropped_budget": result.dropped if result else 0,
        "download_failures": result.failures if result else 0,
        "rows_dropped": dict(drops) if drops else {},
    }


def render_coverage(coverage: dict[str, dict]) -> str:
    """국가별 한 줄 요약 — 초록 run 의 로그에서도 눈에 걸리게."""
    lines = ["  coverage (country: status stations files_total dropped failures)"]
    for country in sorted(coverage):
        c = coverage[country]
        line = (
            f"    {country}: {c['status']} {c['stations']} {c['files_total']} "
            f"{c['files_dropped_budget']} {c['download_failures']}"
        )
        # 행 탈락이 있으면 사유를 같은 줄에 — stations 0 의 원인이 로그에서 바로 읽히게.
        row_drops = c.get("rows_dropped") or {}
        if row_drops:
            detail = " ".join(f"{k}={v}" for k, v in sorted(row_drops.items()))
            line += f" | rows_dropped: {detail}"
        lines.append(line)
    return "\n".join(lines)


def write_coverage(coverage: dict[str, dict], order: list[str], now: datetime) -> None:
    """커버리지 사이드카 write. 경로 미설정이면 no-op(로컬 실행 부담 0)."""
    if not COVERAGE_PATH:
        return
    doc = {
        "budgetSeconds": BUDGET_SECONDS,
        "countryOrder": order,
        "rotationHourUtc": now.hour,
        "countries": coverage,
    }
    tmp_path = f"{COVERAGE_PATH}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp_path, COVERAGE_PATH)


def main() -> int:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.now(timezone.utc)
    start_iso = (now - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    end_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    # 행 단위 필터 컷오프. End 문자열은 tz 표기가 없어 UTC 로 간주해 비교 — 2h 마진을
    # 두어 tz 해석 차이로 창 언저리 행이 잘리지 않게 한다 (신선도 판정은 parse 단계
    # freshness 로직이 그대로 담당).
    row_cutoff = now - timedelta(hours=LOOKBACK_HOURS + 2)

    # 예산 시계는 첫 네트워크 호출 전에 시작 — 메타데이터 호출(station coords 60s,
    # 국가별 URL fetch 120s)도 같은 예산에서 차감돼야 step timeout(10m) 안에 끝난다.
    deadline = time.monotonic() + BUDGET_SECONDS

    station_coords = fetch_station_coords()
    if not station_coords:
        print("ERROR: EEA station metadata fetch failed (0 stations) — abort", file=sys.stderr)
        return 1
    print(f"  {len(station_coords)} EEA station coordinates loaded")

    all_readings: list[dict] = []
    # 시작점을 UTC 시로 회전 — 예산이 6개국을 다 못 덮어도 시간에 걸쳐 전부 돌게 한다.
    # 이번 run 에서 안 돈 국가는 발행 파일에서 빠지므로, workflow 의 merge 단계가
    # 직전 발행분에서 아직 만료되지 않은 관측소만 이어붙인다(fabricate 아님).
    rotated = rotate_countries(COUNTRIES, now.hour)
    print(f"  country order (UTC hour {now.hour}): {' '.join(rotated)}")
    coverage: dict[str, dict] = {}
    # samplingpoint→EoI 인덱스는 **지연 로드**. ES 처럼 Samplingpoint 가 EoI 코드를 담지
    # 않는 국가에서만 필요한데 응답이 ~20MB(실측 16.5s)라, 안 쓰는 run 이 그 값을 내지
    # 않게 한다. 한 번 로드하면 이후 국가에도 그대로 쓴다.
    sp_index: dict[str, str] | None = None
    for country in rotated:
        if time.monotonic() >= deadline:
            print(f"  WARN [{country}]: wall-clock budget ({BUDGET_SECONDS:.0f}s) exhausted "
                  f"before country started — skip country", file=sys.stderr)
            coverage[country] = country_coverage("skipped_budget")
            continue
        urls = fetch_parquet_urls(country, start_iso, end_iso)
        if not urls:
            print(f"  WARN [{country}]: 0 parquet files in window — skip country", file=sys.stderr)
            coverage[country] = country_coverage("no_files_in_window")
            continue
        result = download_country_parquets(country, urls, deadline, cutoff=row_cutoff)
        if not result.rows:
            print(f"  WARN [{country}]: 0 rows parsed — skip country", file=sys.stderr)
            coverage[country] = country_coverage("no_rows", result)
            continue
        latest = latest_rows_by_samplingpoint(result.rows)
        drops: dict[str, int] = {}
        readings = assemble_country_readings(
            latest, station_coords, generated_at, sp_index=sp_index, drops=drops,
        )
        # 행은 있는데 조립 결과가 0 이고 원인이 조인 실패라면, 그 국가는 Samplingpoint 에
        # EoI 코드를 안 담는 쪽(ES 실측)이다 — 인덱스를 그때 한 번 받아 다시 조립한다.
        # 인덱스를 이미 들고도 0 이면 재시도하지 않는다(같은 결과 + 20MB 재요청 회피).
        if not readings and drops.get("eoi_join") and sp_index is None:
            print(f"  [{country}] 0 readings with {drops['eoi_join']} EoI join failures — "
                  f"loading samplingpoint index", file=sys.stderr)
            sp_index = fetch_samplingpoint_index()
            print(f"  samplingpoint index: {len(sp_index)} entries")
            drops = {}
            readings = assemble_country_readings(
                latest, station_coords, generated_at, sp_index=sp_index, drops=drops,
            )
        print(f"  [{country}] {len(readings)} station readings")
        coverage[country] = country_coverage(
            "visited", result, stations=len(readings), drops=drops,
        )
        all_readings.extend(readings)

    # rotate_countries 는 순열이라 모든 국가가 위 루프를 지난다 — 예산이 끊긴 뒤에도
    # 남은 국가는 continue 로 skipped_budget 이 찍힌다. 즉 coverage 는 항상 전 국가를 덮는다.
    print(render_coverage(coverage))
    write_coverage(coverage, rotated, now)

    if not all_readings:
        print("ERROR: 0 valid station readings assembled across all countries — abort "
              "(last-good kept)", file=sys.stderr)
        return 1

    tmp_path = f"{OUTPUT_PATH}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(all_readings, f, separators=(",", ":"))
    os.replace(tmp_path, OUTPUT_PATH)
    print(f"Done: EEA-UTD {len(all_readings)} station readings ({len(COUNTRIES)} countries "
          f"requested) -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    # sys.exit 는 유기된 non-daemon 다운로드 스레드를 join 하며 블록될 수 있다 —
    # output 은 main() 안에서 이미 flush+os.replace 로 착지했으므로 즉시 종료가 안전.
    os._exit(main())
