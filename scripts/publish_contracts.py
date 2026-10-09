#!/usr/bin/env python3
"""publish_contracts.py — Evidence Contract producer 발행 1단계.

EVIDENCE_CONTRACT.md (Obsidian-airlens/raw/docs/platform/EVIDENCE_CONTRACT.md) §5 의
producer 의무 중 세 발행물을 만든다:

  meta/product_manifest.json  — Data Product Manifest v1 (§5-1)
  meta/source_registry.json   — Source Registry v1 (§5-4)
  meta/product_health.json    — ProductHealth v1 (§5-5)

파티션 재배치(§5-2, `product=/schema=/nature=/.../part-*.parquet`)와 Raw/Normalized/
Product/Receipt 4계층 분리(§5-3)는 이 스크립트의 범위 밖이다 — 현재 HF
`Robeedau/airlens-live` 레이아웃은 여전히 flat JSON(레거시)이고, 그 사실을 있는
그대로 발행한다. 소비자(airlens-web) 마이그레이션 계획은 후속 작업.

정직성 원칙(§1-1): 실측하지 못한 필드는 상수로 채우지 않고 null + reason 을 남긴다.
아래 PRODUCTS/SOURCES 카탈로그는 이 레포 워크플로·코드 주석에서 확인한 사실만
기록했다 — 확인 못 한 값(라이선스 등)은 "unverified"로 명시했다.

Usage:
  python3 scripts/publish_contracts.py --dry-run   # meta/*.json 로컬 생성만 (기본)
  python3 scripts/publish_contracts.py --publish    # 생성 후 HF meta/ 로 업로드

Environment:
  HF_TOKEN       업로드 시 필요 (읽기는 공개 dataset 이라 불필요)
  HF_LIVE_REPO   대상 repo (기본 Robeedau/airlens-live)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import HfApi

REPO_ID = os.environ.get("HF_LIVE_REPO", "Robeedau/airlens-live")
REPO_TYPE = "dataset"
SCHEMA_VERSION = "1.0"
REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"

# ──────────────────────────────────────────────────────────────────────
# Product catalog — 이 레포 워크플로·수집기 코드에서 확인한 실제 발행 구조.
#
# `paths`: HF repo 안의 정확한 경로 또는 "prefix/" 형태 디렉터리 접두어. 서로 겹치지
# 않게 설계했다(순서 무관 — 매칭 함수가 첫 일치를 그대로 쓰되 접두어가 배타적이라
# 충돌이 없다). 여기 없는 파일은 "unclassified"로 집계해 조용히 누락시키지 않는다.
#
# `owner`: 실제로 이 파일을 발행하는 워크플로/시스템. `aq-data/predictions`,
# `aq-data/quality-history`, `aq-data/data_quality.json`, `insights-data/by_country`,
# `insights-data/policy-impact`, `blog-data`, `app-models/aod` 는 이 레포의 10개
# 워크플로 어디에서도 grep 되지 않았다 — AirLens-platform 모노레포(models/ETL)가
# 발행하는 것으로 보이나 이 레포에서는 확인 불가하다. owner 를 그렇게 정직하게 적고
# freshnessSlaSeconds 는 null + reason 으로 남긴다(§1-1 위반 회피).
# ──────────────────────────────────────────────────────────────────────
PRODUCTS: list[dict] = [
    {
        "productId": "aq-data-grids",
        "title": "Global pollutant grids (PM2.5/PM10/O3/NO2/CO)",
        "description": (
            "전지구 격자 스냅샷, 매 실행마다 최신 1장으로 덮어쓴다(이력 미보존). "
            "PM2.5/PM10 은 NOAA GEFS-Aerosols 1°(nLat 181 x nLon 360, 65,160셀), "
            "O3/NO2/CO 는 Open-Meteo Air Quality API(CAMS 기반) 5°(nLat 33 x nLon 72). "
            "**해상도가 변수마다 다르다** — 한 파일군이라고 하나로 서술하면 PM 격자를 "
            "실제보다 25배 성기게 신고하게 된다(2026-09-04 발행물 실측으로 정정). "
            "단일 파일군이지만 소스가 섞여 있다는 사실을 sourceRefs 에 그대로 "
            "노출한다(§1-5: 소스 합성 위장 금지)."
        ),
        "owner": "airlens-data (.github/workflows/data-collect-hourly.yml)",
        "nature": "analysis",  # 지상 관측 재분배가 아니라 CAMS/GEFS 모델 격자 — "observation" 오분류 회피
        "variables": [
            {"name": "pm2_5", "unit": "unverified — payload/collector에 unit 필드 없음(NOAA GEFS-Aerosols 관례상 µg/m³ 추정, 미검증)"},
            {"name": "pm10", "unit": "unverified — 위와 동일 사유"},
            {"name": "ozone", "unit": "µg/m³ (open-meteo.com/en/docs/air-quality-api, 2026-09-03 조회 확인)"},
            {"name": "nitrogen_dioxide", "unit": "µg/m³ (위와 동일 조회)"},
            {"name": "carbon_monoxide", "unit": "µg/m³ (위와 동일 조회)"},
            {"name": "pollen (6 species: grass/birch/alder/mugwort/olive/ragweed)", "unit": "grains/m³ (data-collect-hourly.yml:215 comment)"},
        ],
        "spatialCoverage": "global — pm2_5/pm10 on a 1° grid (nLat=181, nLon=360), o3/no2/co on a 5° grid (nLat=33, nLon=72); pollen bounded to Europe (CAMS pollen domain, lat 34-73 lon -12-46)",
        "temporalCoverage": "latest snapshot only — no history retained (overwritten every run)",
        "resolution": "1deg (pm2_5, pm10) / 5deg (o3, no2, co)",
        "sourceRefs": ["noaa-gefs-aerosols", "open-meteo-air-quality", "open-meteo-weather"],
        "license": "mixed — see sourceRefs (NOAA=public domain, Open-Meteo=CC-BY-4.0)",
        "attribution": "NOAA/NCEP GEFS-Aerosols; Open-Meteo (open-meteo.com)",
        "freshnessSlaSeconds": 21600,
        "freshnessSlaReason": "6종 전부 — data-collect-hourly.yml 'Verify AQ grid freshness (live probe)' 가 발행물을 되읽어 6h(MAX_AQ_STALENESS_HOURS) 초과 시 fail-loud. 6h = 수집 cron 3h('5 */3 * * *') 두 사이클 연속 결손. 나이 필드는 격자마다 다르다: pm2_5/pm10 = generatedAt(그쪽 timestamp 는 데이터 사이클 시각이라 수집이 멈춰도 갱신되므로 건강 신호가 아니다), o3/no2/co = timestamp(collect_all.py 가 수집 벽시계를 epoch ms 로 쓴다), pollen = collected_at.",
        "knownLimitations": [
            "신선도 게이트의 형태 검사는 격자마다 다르다 — pm2_5/pm10 은 조밀(nLat*nLon == len(points)), o3/no2/co 는 희소(값을 못 받은 셀이 빠진다, 실측 2196/2376)라 셀 수 초과만 잡고 커버리지 하한은 걸지 않는다(근거 없는 임계값 발명 회피), pollen 은 count == len(points). 즉 가스 격자의 커버리지가 서서히 줄어드는 열화는 이 게이트가 잡지 못한다.",
            "그리드 payload 에 unit 필드가 없다 — 수집기 코드에도 명시적 unit 어서션이 없다(pm2_5/pm10).",
            "격자 셀 값이며 관측소 값이 아니다 — EVIDENCE_CONTRACT.md §6-3 GridCell/StationObservation 분리 대상.",
            "PM2.5/PM10 과 O3/NO2/CO 가 서로 다른 소스(NOAA vs Open-Meteo)인데 파일명은 동일 규약(current-*-grid.json)이라 시각적으로 구분되지 않는다.",
        ],
        "paths": [
            "aq-data/current-pm25-grid.json",
            "aq-data/current-pm10-grid.json",
            "aq-data/current-o3-grid.json",
            "aq-data/current-no2-grid.json",
            "aq-data/current-co-grid.json",
            "aq-data/pollen-grid.json",
        ],
    },
    {
        "productId": "wind-data",
        "title": "Weather / marine / wind grids",
        "description": (
            "weather-grid·marine-data 는 Open-Meteo(forecast/marine API), wind-surface·"
            "wind-850hpa(+2deg 변형)는 NOAA GFS 1° grib 직접 파싱(collect_gfs_wind.py) — "
            "역시 파일군 하나에 소스 둘이 섞여 있다."
        ),
        "owner": "airlens-data (.github/workflows/data-collect-hourly.yml)",
        "nature": "analysis",
        "variables": [
            {"name": "wind_u/wind_v (surface 10m, 850hPa)", "unit": "m/s (NOAA GFS UGRD/VGRD 표준 — collect_gfs_wind.py 헤더 명시)"},
            {"name": "sst/waves/wave_period/current_vel/current_dir", "unit": "unverified — Open-Meteo Marine API 응답, unit 필드 미확인"},
            {"name": "temperature/uvi/mslp (weather-grid)", "unit": "unverified — Open-Meteo Forecast API, unit 필드 미확인"},
        ],
        "spatialCoverage": "global — wind 1deg(GFS), marine 10deg step, weather grid resolution not re-verified here",
        "temporalCoverage": "latest snapshot only — no history retained",
        "resolution": "wind=1deg(GFS)/2deg-thinned variant; marine=10deg; weather=unverified",
        "sourceRefs": ["noaa-gfs-wind", "open-meteo-weather"],
        "license": "mixed — see sourceRefs (NOAA=public domain, Open-Meteo=CC-BY-4.0)",
        "attribution": "NOAA GFS (AWS Open Data); Open-Meteo (open-meteo.com)",
        "freshnessSlaSeconds": 21600,
        "freshnessSlaReason": None,
        "knownLimitations": [
            "wind-850hpa 는 반드시 850mb 레벨이어야 하며 collect_gfs_wind.py 는 레벨 누락 시 전체 실패로 처리한다(surface 오염 방지) — 그러나 이 매니페스트는 그 보장을 재검증하지 않는다.",
        ],
        "paths": [
            "wind-data/weather-grid.json",
            "wind-data/marine-data.json",
            "wind-data/wind-surface.json",
            "wind-data/wind-surface-2deg.json",
            "wind-data/wind-850hpa.json",
            "wind-data/wind-850hpa-2deg.json",
        ],
    },
    {
        "productId": "active-fires",
        "title": "NASA FIRMS active fire hotspots",
        "description": "VIIRS 활성 화재 핫스팟, FRP 내림차순 상위 20000건 상한(6.7MB 사고 재발 방지, collect_firms.py 주석).",
        "owner": "airlens-data (.github/workflows/firms-collect.yml)",
        "nature": "observation",
        "variables": [
            {"name": "brightness", "unit": "unverified — FIRMS 문서 attribute table 미조회(WebFetch 페이지에 값 없음), VIIRS 관례상 Kelvin 추정"},
            {"name": "frp", "unit": "unverified — 동일 사유, 관례상 MW(Fire Radiative Power) 추정"},
            {"name": "confidence", "unit": "categorical (l/n/h)"},
        ],
        "spatialCoverage": "global",
        "temporalCoverage": "day_range=2 (최근 2일 UTC 기준, collect_firms.py DAY_RANGE)",
        "resolution": "point (per-detection, non-gridded)",
        "sourceRefs": ["nasa-firms"],
        "license": "unverified",
        "attribution": "NASA FIRMS / VIIRS SNPP NRT",
        "freshnessSlaSeconds": 64800,
        "freshnessSlaReason": None,
        "knownLimitations": [
            "20000건 상한 — 실제 탐지 건수가 이를 넘으면 FRP 하위 꼬리가 잘린다(전체를 부분으로 위장하지 않음, count 필드에 실제 발행 건수 그대로 노출).",
            "day_range=2 는 '최근 24시간'이 아니라 'UTC 현재 날짜 기준 2일' — 실행 시각에 따라 커버리지가 들쭉날쭉하다(collect_firms.py 주석).",
        ],
        "paths": ["wind-data/active-fires.json"],
    },
    {
        "productId": "aq-data-timeline",
        "title": "GEFS-Aerosols PM2.5 timeline (-24h..+24h)",
        "description": "3시간 간격 PM2.5 프레임 다수 파일 — NOAA GEFS-Aerosols.",
        "owner": "airlens-data (.github/workflows/timeline-collect.yml)",
        "nature": "forecast",
        "variables": [{"name": "pm2_5", "unit": "unverified — payload에 unit 필드 없음, NOAA GEFS-Aerosols 관례상 µg/m³ 추정"}],
        "spatialCoverage": "global",
        "temporalCoverage": "-24h..+24h, 3h step (다수 개별 프레임 파일 + manifest.json)",
        "resolution": "unverified — 파일 내 격자 해상도 미재확인(제조사 grid 그대로 사용 추정)",
        "sourceRefs": ["noaa-gefs-aerosols"],
        "license": "US public domain (data-collect-hourly.yml:149 comment; 동일 NOAA GEFS-Aerosols 계열)",
        "attribution": "NOAA/NCEP GEFS-Aerosols",
        "freshnessSlaSeconds": 32400,
        "freshnessSlaReason": None,
        "knownLimitations": [],
        "paths": ["aq-data/timeline/"],
    },
    {
        "productId": "aq-data-forecast",
        "title": "CAMS PM2.5 city forecast",
        "description": "seed/cams_forecast.json 도시 시드 목록에 대한 시간별 PM2.5 예보(Open-Meteo CAMS 엔드포인트) + 30일 아카이브 + AIFS weather 아카이브.",
        "owner": "airlens-data (.github/workflows/forecast-collect.yml)",
        "nature": "forecast",
        "variables": [{"name": "pm25 (hourly, per city)", "unit": "unverified — forecast.json payload에 unit 필드 없음, Open-Meteo CAMS 관례상 µg/m³ 추정"}],
        "spatialCoverage": "city-seed list only (seed/cams_forecast.json) — not gridded",
        "temporalCoverage": "hourly forecast horizon (구체 길이 payload 재확인 필요) + forecast-archive/ 30일 보존(hf_publish.py prune 관례)",
        "resolution": "point (per-city)",
        "sourceRefs": ["open-meteo-cams-forecast"],
        "license": "CC-BY-4.0 (forecast-collect.yml:196 comment)",
        "attribution": "Open-Meteo CAMS (open-meteo.com)",
        "freshnessSlaSeconds": 86400,
        "freshnessSlaReason": None,
        "knownLimitations": ["forecast-archive/ retention 은 hf_publish.py prune 서브커맨드가 담당하나 이 스크립트는 실제 30일 준수 여부를 재검증하지 않는다."],
        "paths": ["aq-data/forecast.json", "aq-data/forecast-archive/"],
    },
    {
        "productId": "news-data",
        "title": "RSS environment/air-quality news (merged, deduped)",
        "description": "22개 RSS 피드 병합·누적(article_url 기준 dedup) — collect_news.py FEEDS.",
        "owner": "airlens-data (.github/workflows/news-collect.yml)",
        "nature": "observation",
        "variables": [{"name": "articles[]", "unit": "n/a (editorial records, not a measured quantity)"}],
        "spatialCoverage": "global (22 RSS feeds, region-tagged where documented)",
        "temporalCoverage": "cumulative — merged with previous publish, never truncated by this workflow",
        "resolution": "n/a (document-level records)",
        "sourceRefs": ["rss-news-aggregate"],
        "license": "unverified — per-publisher terms apply, no aggregate license asserted",
        "attribution": "각 원 기사 발행사 (UNEP/WHO/EPA/EEA/NASA Climate 등 22개 피드, collect_news.py FEEDS)",
        "freshnessSlaSeconds": 64800,
        "freshnessSlaReason": None,
        "knownLimitations": ["병합 누적이라 개별 피드 장애가 refTime 을 막지 않는다 — 전체 피드 동시 실패만 감지된다(news-collect.yml 주석)."],
        "paths": ["news-data/articles.json"],
    },
    {
        "productId": "mac-data",
        "title": "Mac free global AQ snapshot (CAMS + GEFS-chem + AirKorea + EEA-UTD)",
        "description": "소스별 QA·정규화(mac_aq_adapter)를 거친 flat 발행 — 목표 파티션/타일 레이아웃(design SOT)은 아직 미구현, index.json 이 observability 용.",
        "owner": "airlens-data (.github/workflows/mac-data-publish.yml)",
        "nature": "analysis",
        "variables": [{"name": "pm2_5/pm10 (mac/web variants)", "unit": "unverified — mac_aq_adapter 정규화 스키마 unit 필드 미재확인"}],
        "spatialCoverage": "CAMS=global, GEFS-chem=global, AirKorea=한국, EEA-UTD=유럽",
        "temporalCoverage": "latest snapshot per source, last-good baseline kept on source failure (design SOT '검증 게이트')",
        "resolution": "unverified — 소스별 상이(design SOT 목표 geohash 타일링은 미구현)",
        "sourceRefs": ["copernicus-ads-cams", "noaa-gefs-chem", "airkorea", "eea-utd"],
        "license": "mixed — see sourceRefs, mostly unverified except noaa-gefs-chem (public domain)",
        "attribution": "Copernicus ADS (CAMS); NOAA/NCEP GEFS-Chem; AirKorea(공공데이터포털); EEA Up-To-Date",
        "freshnessSlaSeconds": 10800,
        "freshnessSlaReason": None,
        "knownLimitations": [
            "각 소스는 continue-on-error — 한 소스가 죽어도 나머지가 발행되고 워크플로는 초록불이다. 실제 감시는 mac-snapshot-healthcheck.yml (별도 probe)가 담당.",
            "mac-snapshot-healthcheck.yml 은 현재 advisory(항상 exit 0) — CAMS 복구 전까지 fail-loud 비활성 (워크플로 주석).",
            "AirKorea 는 API 키 미설정 시 자동 skip, EEA 는 keyless.",
        ],
        "paths": ["mac-data/data/mac/v1/", "mac-data/data/web/v1/"],
    },
    {
        "productId": "insights-policy",
        "title": "Policy registry (structured, $0 sources)",
        "description": "Federal Register / WHO standards / major policies(+ opt-in CPR corpus) 병합 — scripts/etl/collect_policies/SOURCES.md.",
        "owner": "airlens-data (.github/workflows/policy-collect.yml)",
        "nature": "policy",
        "variables": [{"name": "policy records (effective_date/pollutants/standards/...)", "unit": "n/a (structured records)"}],
        "spatialCoverage": "~124개국 (SOURCES.md 실측 수치, ~376 policies)",
        "temporalCoverage": "weekly refresh (light sources) — CPR corpus는 opt-in dispatch만",
        "resolution": "n/a (document/record-level)",
        "sourceRefs": ["policy-structured-sources"],
        "license": "mixed — CPR=CC-BY-4.0(collect_cpr.py:5), 나머지 unverified",
        "attribution": "Climate Policy Radar/CCLW; WHO Air Quality Standards DB; 각국 정부/UNEP/CCAC; US Federal Register",
        "freshnessSlaSeconds": None,
        "freshnessSlaReason": "policy-collect.yml 은 weekly cron(Sun 04:00 UTC)뿐 staleness gate가 워크플로에 없음",
        "knownLimitations": [
            "China(MEE)/India(CPCB) 비구조 정책 페이지는 의도적으로 제외($0/LLM-0 제약, SOURCES.md '제외' 섹션) — 두 국가는 WHO 표준·major-policies 로만 커버.",
            "CPR corpus(3.59GB) 갱신은 opt-in dispatch — 정기 cron 은 light 소스만 갱신한다.",
        ],
        "paths": ["insights-data/policy/"],
    },
    {
        "productId": "openaq-shadow",
        "title": "OpenAQ backfill shadow",
        "description": "E2 사이드카(Oracle VM systemd 타이머)가 이 레포 밖에서 직접 발행하는 시간별 슬롯 gzip.",
        "owner": "E2 sidecar (Oracle VM, e2-sidecar/systemd/airlens-openaq-shadow.{service,timer}) — 이 레포 GitHub Actions 워크플로 밖",
        "nature": "observation",
        "variables": [{"name": "station observations (pollutant mix per OpenAQ schema)", "unit": "unverified — OpenAQ 응답 원본 그대로, 정규화 미확인"}],
        "spatialCoverage": "global (OpenAQ 커버리지 그대로)",
        "temporalCoverage": "hourly slots, backfill 포함",
        "resolution": "point (station-level)",
        "sourceRefs": ["openaq"],
        "license": "unverified",
        "attribution": "OpenAQ (openaq.org)",
        "freshnessSlaSeconds": 14400,
        "freshnessSlaReason": None,
        "knownLimitations": ["e2-shadow-freshness.yml 은 슬롯 파일명 존재만 프로브 — payload 내용 검증은 하지 않는다."],
        "paths": ["openaq-shadow/"],
    },
    {
        "productId": "sensor-community-shadow",
        "title": "Sensor.Community backfill shadow",
        "description": "openaq-shadow 와 동일 체계, Sensor.Community 저가 센서망.",
        "owner": "E2 sidecar (Oracle VM, e2-sidecar/systemd/airlens-sc-shadow.{service,timer}) — 이 레포 GitHub Actions 워크플로 밖",
        "nature": "observation",
        "variables": [{"name": "low-cost sensor readings", "unit": "unverified — Sensor.Community 응답 원본"}],
        "spatialCoverage": "global (Sensor.Community 커버리지 그대로, 유럽 밀집)",
        "temporalCoverage": "hourly slots",
        "resolution": "point (sensor-level, community/저가 tier)",
        "sourceRefs": ["sensor-community"],
        "license": "unverified",
        "attribution": "Sensor.Community (sensor.community)",
        "freshnessSlaSeconds": 14400,
        "freshnessSlaReason": None,
        "knownLimitations": ["quality.tier 는 'community'(EVIDENCE_CONTRACT §2)로 표시해야 하며 'reference'와 혼합 저장하지 않는다(§5-2 '저비용과 reference 를 합쳐 저장해도 tier 는 절대 소실하지 않는다')."],
        "paths": ["sensor-community-shadow/"],
    },
    {
        "productId": "aq-data-predictions",
        "title": "AOD→PM2.5 model predictions (p10-p90 + DQSS)",
        "description": "XGBoost-GTWR 기반 예측 그리드, uncertainty 밴드 포함(payload 실측: predicted_p10/p50/p90, epistemic_std).",
        "owner": "AirLens-platform monorepo (models/ETL) — 이 레포 10개 워크플로 어디에서도 aq-data/predictions 발행 경로가 grep 되지 않음",
        "nature": "inferred",
        "variables": [
            {"name": "predicted_p10/p50/p90", "unit": "unverified — payload에 unit 필드 없음, PM2.5 문맥상 µg/m³ 추정"},
            {"name": "uncertainty/epistemic_std", "unit": "unverified"},
        ],
        "spatialCoverage": "unverified — 이 레포에서 생성 코드가 없어 커버리지 재확인 불가(payload count=621 관측)",
        "temporalCoverage": "unverified — 관측된 관측 슬롯 필드(observation_slot) 기준으로 3h 근사 추정, 계약 문서 없음",
        "resolution": "point (per-station, observation_source=openaq-shadow 등)",
        "sourceRefs": ["airlens-platform-monorepo", "openaq"],
        "license": "unverified",
        "attribution": "AirLens ML-AODtoPM25Model (source 필드 실측값)",
        "freshnessSlaSeconds": None,
        "freshnessSlaReason": "이 레포 워크플로가 발행하지 않음 — SLA 계약을 확인할 소스가 없다",
        "knownLimitations": ["이 매니페스트는 HF 상 실제 파일 존재/나이만 관측했다 — 생성 파이프라인 자체는 이 레포 범위 밖(models/ETL)이라 코드 근거 인용 불가."],
        "paths": ["aq-data/predictions/"],
    },
    {
        "productId": "aq-data-quality",
        "title": "DQSS data quality scores",
        "description": "RuleBasedDQSS 엔진 산출물(payload 실측: measured_weight_range, graded_stations, total_stations).",
        "owner": "AirLens-platform monorepo (models/ETL) — 이 레포 워크플로 밖",
        "nature": "analysis",
        "variables": [{"name": "final_score / graded_stations / total_stations", "unit": "n/a (quality score, dimensionless)"}],
        "spatialCoverage": "unverified — total_stations=621(payload 실측 스냅샷, 시점에 따라 변동 가능)",
        "temporalCoverage": "unverified",
        "resolution": "point (per-station)",
        "sourceRefs": ["airlens-platform-monorepo"],
        "license": "unverified",
        "attribution": "AirLens RuleBasedDQSS engine (payload 실측값)",
        "freshnessSlaSeconds": None,
        "freshnessSlaReason": "이 레포 워크플로가 발행하지 않음",
        "knownLimitations": ["payload 자체가 'completeness: unavailable-pending-shadow-aggregation' 등 내부 미측정 필드를 이미 정직하게 표기하고 있다(실측 확인, 2026-09-03)."],
        "paths": ["aq-data/data_quality.json", "aq-data/quality-history/"],
    },
    {
        "productId": "insights-country",
        "title": "By-country insights summaries",
        "description": "국가별 요약(index.json + by_country/{CC}.json).",
        "owner": "AirLens-platform monorepo (models/ETL) — 이 레포 워크플로 밖",
        "nature": "analysis",
        "variables": [{"name": "unverified — 페이로드 상세 미조회", "unit": "unverified"}],
        "spatialCoverage": "국가 단위 (파일 수로 실측 — meta 발행 시 fileCount 참조)",
        "temporalCoverage": "unverified",
        "resolution": "country",
        "sourceRefs": ["airlens-platform-monorepo"],
        "license": "unverified",
        "attribution": "AirLens insights synthesis (모노레포)",
        "freshnessSlaSeconds": None,
        "freshnessSlaReason": "이 레포 워크플로가 발행하지 않음",
        "knownLimitations": [],
        "paths": ["insights-data/by_country/", "insights-data/index.json"],
    },
    {
        "productId": "insights-policy-impact",
        "title": "Policy impact analysis (SDID)",
        "description": "국가별 정책 인과 분석 산출물(policy-impact/{CC}.json).",
        "owner": "AirLens-platform monorepo (models/ETL) — 이 레포 워크플로 밖",
        "nature": "analysis",
        "variables": [{"name": "unverified — 페이로드 상세 미조회", "unit": "unverified"}],
        "spatialCoverage": "국가 단위",
        "temporalCoverage": "unverified",
        "resolution": "country",
        "sourceRefs": ["airlens-platform-monorepo"],
        "license": "unverified",
        "attribution": "AirLens SDID engine (모노레포)",
        "freshnessSlaSeconds": None,
        "freshnessSlaReason": "이 레포 워크플로가 발행하지 않음",
        "knownLimitations": [],
        "paths": ["insights-data/policy-impact/"],
    },
    {
        "productId": "blog-data",
        "title": "Blog posts/drafts",
        "description": "에디토리얼 콘텐츠 — 측정된 물리량이 아니다.",
        "owner": "AirLens-platform monorepo — 이 레포 워크플로 밖",
        "nature": "policy",  # 가장 근접한 enum — 서술 콘텐츠에 더 맞는 nature 가 스펙에 없음, 아래 knownLimitations 에 명시
        "variables": [{"name": "posts[]/drafts[]", "unit": "n/a (editorial content)"}],
        "spatialCoverage": "n/a",
        "temporalCoverage": "unverified",
        "resolution": "n/a",
        "sourceRefs": ["airlens-platform-monorepo"],
        "license": "unverified",
        "attribution": "AirLens editorial (모노레포)",
        "freshnessSlaSeconds": None,
        "freshnessSlaReason": "이 레포 워크플로가 발행하지 않음",
        "knownLimitations": ["EVIDENCE_CONTRACT.md DataNature enum 에 에디토리얼 콘텐츠용 값이 없다 — 'policy'를 임시 배정했다(측정치가 아니므로 소비자는 이 필드를 신뢰 판단에 쓰면 안 된다)."],
        "paths": ["blog-data/"],
    },
    {
        "productId": "app-models-aod",
        "title": "AOD→PM2.5 model artifact (sha256-attested)",
        "description": "GTWR/XGBoost 모델 바이너리 — LFS sha256 로 attestation 가능(HF 실측, model-artifact-integrity.md Layer 1과 정합).",
        "owner": "AirLens-platform monorepo (models/ETL) — 이 레포 워크플로 밖, per-release 배포",
        "nature": "inferred",
        "variables": [{"name": "model weights/metadata (not a measured phenomenon)", "unit": "n/a"}],
        "spatialCoverage": "n/a (model artifact)",
        "temporalCoverage": "per-release (README)",
        "resolution": "n/a",
        "sourceRefs": ["airlens-platform-monorepo"],
        "license": "unverified",
        "attribution": "AirLens AOD→PM2.5 model (모노레포)",
        "freshnessSlaSeconds": None,
        "freshnessSlaReason": "release 단위라 시간 기반 SLA 개념이 적용되지 않는다",
        "knownLimitations": [],
        "paths": ["app-models/aod/"],
    },
]

# ──────────────────────────────────────────────────────────────────────
# Source registry — 문서화된 사실만. license 를 확인하지 못한 항목은
# "unverified"로 명시한다(추측 금지 — cite-or-don't-claim).
# ──────────────────────────────────────────────────────────────────────
SOURCES: list[dict] = [
    {
        "sourceId": "noaa-gefs-aerosols",
        "provider": "NOAA/NCEP Global Ensemble Forecast System - Aerosols",
        "license": "US public domain (data-collect-hourly.yml:149 comment, collect_noaa_aq.py)",
        "coverage": "global",
        "cadence": "PT3H",
    },
    {
        "sourceId": "noaa-gfs-wind",
        "provider": "NOAA GFS 1° (UGRD/VGRD, surface 10m + 850hPa)",
        "license": "US public domain (AWS Open Data S3 anonymous access, collect_gfs_wind.py header)",
        "coverage": "global",
        "cadence": "PT3H",
    },
    {
        "sourceId": "open-meteo-air-quality",
        "provider": "Open-Meteo Air Quality API (air-quality-api.open-meteo.com, CAMS-based gases)",
        "license": "CC-BY-4.0 (open-meteo.com/en/docs/air-quality-api, units WebFetch-verified 2026-09-03)",
        "coverage": "global",
        "cadence": "PT3H",
    },
    {
        "sourceId": "open-meteo-weather",
        "provider": "Open-Meteo Forecast + Marine APIs (weather-grid, marine-data, pollen)",
        "license": "CC-BY-4.0 (forecast-collect.yml:196 comment)",
        "coverage": "global (pollen bounded to Europe, CAMS pollen domain)",
        "cadence": "PT3H",
    },
    {
        "sourceId": "open-meteo-cams-forecast",
        "provider": "Open-Meteo CAMS city PM2.5 forecast",
        "license": "CC-BY-4.0 (forecast-collect.yml:196 comment)",
        "coverage": "city-seed list (seed/cams_forecast.json)",
        "cadence": "PT6H",
    },
    {
        "sourceId": "nasa-firms",
        "provider": "NASA FIRMS VIIRS SNPP NRT active fire detections",
        "license": "unverified",
        "coverage": "global",
        "cadence": "PT6H",
    },
    {
        "sourceId": "rss-news-aggregate",
        "provider": "22 RSS feeds (UNEP/WHO/EPA/EEA/NASA Climate/etc. — scripts/etl/collect_news.py FEEDS)",
        "license": "unverified — per-publisher terms, no aggregate license asserted",
        "coverage": "global",
        "cadence": "PT6H",
    },
    {
        "sourceId": "copernicus-ads-cams",
        "provider": "Copernicus Atmosphere Data Store — CAMS global (CDS_API_KEY)",
        "license": "unverified",
        "coverage": "global",
        "cadence": "PT1H",
    },
    {
        "sourceId": "noaa-gefs-chem",
        "provider": "NOAA/NCEP GEFS-Chem (experimental full chemistry, distinct from GEFS-Aerosols)",
        "license": "US public domain (collect_gefs_chem_global.py:55 comment)",
        "coverage": "global",
        "cadence": "PT1H",
    },
    {
        "sourceId": "airkorea",
        "provider": "AirKorea (공공데이터포털, 에어코리아)",
        "license": "unverified",
        "coverage": "한국",
        "cadence": "PT1H",
    },
    {
        "sourceId": "eea-utd",
        "provider": "EEA Up-To-Date air quality",
        "license": "unverified",
        "coverage": "유럽",
        "cadence": "PT1H",
    },
    {
        "sourceId": "policy-structured-sources",
        "provider": "Climate Policy Radar/CCLW + WHO Air Quality Standards DB + major policies(gov/UNEP/CCAC) + US Federal Register",
        "license": "mixed — CPR=CC-BY-4.0(collect_cpr.py:5), 나머지 unverified",
        "coverage": "~124개국 (SOURCES.md 실측치)",
        "cadence": "P1W",
    },
    {
        "sourceId": "openaq",
        "provider": "OpenAQ",
        "license": "unverified",
        "coverage": "global",
        "cadence": "PT1H",
    },
    {
        "sourceId": "sensor-community",
        "provider": "Sensor.Community",
        "license": "unverified",
        "coverage": "global (유럽 밀집)",
        "cadence": "PT1H",
    },
    {
        "sourceId": "airlens-platform-monorepo",
        "provider": "AirLens-platform monorepo (models/ETL) — internal pipeline, not an external data source",
        "license": "internal — 외부 라이선스 미해당",
        "coverage": "n/a (internal)",
        "cadence": "unverified — 이 레포에서 관측 불가",
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_entries(api: HfApi) -> list:
    """HF repo 전체 파일 트리를 실측(재귀 + last_commit/lfs 확장)."""
    return list(
        api.list_repo_tree(
            repo_id=REPO_ID, repo_type=REPO_TYPE, recursive=True, expand=True
        )
    )


def _matches(path: str, prefixes: list[str]) -> bool:
    for p in prefixes:
        if p.endswith("/"):
            if path.startswith(p):
                return True
        elif path == p:
            return True
    return False


def group_by_product(entries: list) -> tuple[dict[str, list], list]:
    """실제 파일을 productId 로 그룹화. 어느 카탈로그에도 안 걸리면 unclassified."""
    # RepoFolder 는 size 가 없다 — duck-typing으로 구분(실 SDK 클래스 import 없이도
    # 테스트 fixture 가 같은 shape 의 fake 객체로 대체 가능하게 한다).
    files_only = [e for e in entries if hasattr(e, "size")]
    grouped: dict[str, list] = {p["productId"]: [] for p in PRODUCTS}
    unclassified: list = []
    for e in files_only:
        hit = None
        for product in PRODUCTS:
            if _matches(e.path, product["paths"]):
                hit = product["productId"]
                break
        if hit is None:
            unclassified.append(e)
        else:
            grouped[hit].append(e)
    return grouped, unclassified


def _sha256_of(entry) -> tuple[str | None, str | None]:
    lfs = getattr(entry, "lfs", None)
    if lfs is not None and getattr(lfs, "sha256", None):
        return lfs.sha256, None
    return None, "non-LFS git blob (sha1 only) — sha256 미계산(수백 개 파일 전체 다운로드 회피, publish_contracts.py 설계 결정)"


def build_manifest(grouped: dict[str, list]) -> dict:
    generated_at = now_iso()
    products_out = []
    for product in PRODUCTS:
        entries = grouped[product["productId"]]
        last_commit_dates = [
            e.last_commit.date for e in entries if getattr(e, "last_commit", None)
        ]
        last_ingest = max(last_commit_dates).isoformat() if last_commit_dates else None
        last_ingest_reason = None if last_ingest else "이 product 아래 파일이 HF 에 하나도 없음(0건 실측)"

        # 파티션은 §5-2 정식 스킴(product=/schema=/.../part-*.parquet)이 아니라 현재 flat
        # 레이아웃을 카탈로그 선언(product["paths"]) 단위로 요약한 것이다 — 부모 디렉터리로
        # 뭉치면(예: aq-data/ 안 pm25+pm10+o3+no2+co+pollen 6개 파일이 한 파티션이 됨) 서로
        # 다른 소스가 하나로 섞여 §1-5(소스 합성 위장 금지)를 어기므로, 선언된 각 path 를
        # 그대로 파티션 경계로 쓴다(선언됐지만 실측 0건인 경로도 fileCount=0 으로 정직하게 남김).
        partitions = []
        for declared_path in product["paths"]:
            files = [e for e in entries if _matches(e.path, [declared_path])]
            dir_dates = [f.last_commit.date for f in files if getattr(f, "last_commit", None)]
            shas = [_sha256_of(f) for f in files]
            partitions.append(
                {
                    "path": declared_path,
                    "fileCount": len(files),
                    "bytes": sum(f.size for f in files),
                    "rowCount": None,
                    "rowCountReason": (
                        "row-level counting deferred — 현재 flat JSON 레이아웃은 파일마다 스키마가 달라 "
                        "전체 content parse가 필요하다. §5-2 parquet partition 이행 후 row-group 메타데이터로 대체 예정."
                    ),
                    "sha256": shas[0][0] if len(files) == 1 else None,
                    "sha256Reason": (
                        shas[0][1] if len(files) == 1 else
                        "디렉터리 전체 sha256 은 미계산(파일 단위만 계산, 다중 파일 경로는 aggregate hash 미정의)"
                    ),
                    "lastModifiedAt": max(dir_dates).isoformat() if dir_dates else None,
                }
            )

        products_out.append(
            {
                "productId": product["productId"],
                "title": product["title"],
                "description": product["description"],
                "owner": product["owner"],
                "schemaVersion": SCHEMA_VERSION,
                "nature": product["nature"],
                "variables": product["variables"],
                "spatialCoverage": product["spatialCoverage"],
                "temporalCoverage": product["temporalCoverage"],
                "resolution": product["resolution"],
                "sourceRefs": product["sourceRefs"],
                "license": product["license"],
                "attribution": product["attribution"],
                "time": {
                    "lastSuccessfulIngestAt": last_ingest,
                    "lastSuccessfulIngestReason": last_ingest_reason,
                    "publishedAt": last_ingest,
                    "publishedAtReason": last_ingest_reason,
                    "validAt": None,
                    "validAtReason": (
                        "레코드 단위 validStart 는 EvidenceEnvelope 개별 값에서만 확정 가능 — "
                        "매니페스트 granularity 에선 HF commit 시각(=publishedAt 대용)만 실측했다."
                    ),
                },
                "freshnessSlaSeconds": product["freshnessSlaSeconds"],
                "freshnessSlaReason": product["freshnessSlaReason"],
                "partitions": partitions,
                "qualityProfile": {
                    "nullRatio": None,
                    "duplicateRatio": None,
                    "coordinateIssues": None,
                    "temporalGaps": None,
                    "agreement": None,
                    "reason": (
                        "콘텐츠 레벨 QA(null/duplicate/좌표/시간갭/agreement)는 v1 미구현 — "
                        "이번 단계는 존재/신선도/파일 무결성만 실측한다. 후속 단계에서 "
                        "product 별 파서를 추가해 채운다."
                    ),
                },
                "access": {
                    "uiSample": None,
                    "uiSampleReason": "airlens-web 소비자 라우트 매핑은 B1 consumer 측 범위(이 스크립트는 producer 전용)",
                    "restApi": None,
                    "restApiReason": "/v1 공개 REST API 미구현(EVIDENCE_CONTRACT.md §6 대상, 아직 착수 전)",
                    "mcpApi": None,
                    "mcpApiReason": "MCP API 미구현",
                    "directArtifact": f"https://huggingface.co/datasets/{REPO_ID}/resolve/main/{product['paths'][0]}",
                    "citation": f"AirLens Live Data, https://huggingface.co/datasets/{REPO_ID} (CC-BY-4.0, dataset card)",
                },
                "knownLimitations": product["knownLimitations"],
                "deprecation": {"status": "active", "supersededBy": None, "sunsetAt": None},
            }
        )

    return {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "products": products_out}


def build_source_registry(manifest: dict) -> dict:
    """소스 status 는 자체 프로브가 아니라 참조 product 들의 ProductHealth 최악값으로 도출한다."""
    product_by_id = {p["productId"]: p for p in manifest["products"]}
    products_referencing: dict[str, list[str]] = {s["sourceId"]: [] for s in SOURCES}
    for p in manifest["products"]:
        for ref in p["sourceRefs"]:
            products_referencing.setdefault(ref, []).append(p["productId"])

    sources_out = []
    for source in SOURCES:
        pids = products_referencing.get(source["sourceId"], [])
        ingest_times = [
            product_by_id[pid]["time"]["lastSuccessfulIngestAt"]
            for pid in pids
            if pid in product_by_id and product_by_id[pid]["time"]["lastSuccessfulIngestAt"]
        ]
        last_success = max(ingest_times) if ingest_times else None
        last_success_reason = (
            None if last_success else "참조 product 중 실측된 lastSuccessfulIngestAt 이 없음(파일 부재 또는 아직 미계산)"
        )
        sources_out.append(
            {
                "sourceId": source["sourceId"],
                "provider": source["provider"],
                # status 는 build_product_health() 실행 후 2차 패스에서 채운다(아래 main 참조).
                "status": "unknown",
                "license": source["license"],
                "coverage": source["coverage"],
                "cadence": source["cadence"],
                "lastSuccess": last_success,
                "lastSuccessReason": last_success_reason,
                "products": pids,
            }
        )
    return {"schemaVersion": SCHEMA_VERSION, "generatedAt": now_iso(), "sources": sources_out}


def build_product_health(manifest: dict) -> dict:
    generated_at = now_iso()
    now = datetime.now(timezone.utc)
    products_out = []
    for p in manifest["products"]:
        last_published = p["time"]["publishedAt"]
        total_files = sum(part["fileCount"] for part in p["partitions"])
        artifact_reachable = total_files > 0
        sla = p["freshnessSlaSeconds"]

        age_seconds = None
        if last_published:
            published_dt = datetime.fromisoformat(last_published)
            age_seconds = (now - published_dt).total_seconds()

        if not artifact_reachable:
            status = "missing"
            reason = "HF 상 이 product 경로 아래 파일이 0건 실측됨"
        elif sla is not None and age_seconds is not None and age_seconds > sla:
            status = "stale"
            reason = f"ageSeconds({age_seconds:.0f}) > slaSeconds({sla}) 실측"
        elif sla is None:
            status = "partial"
            reason = "freshnessSlaSeconds 미계약(owner 또는 워크플로 근거 부재) — 파일 존재/나이는 실측했으나 SLA 판정 불가"
        else:
            status = "ready"
            reason = None

        entry = {
            "productId": p["productId"],
            "status": status,
            "lastProbeAt": generated_at,
            "schemaValid": bool(artifact_reachable),
            "artifactReachable": artifact_reachable,
        }
        # schemaValid: v1 은 콘텐츠 스키마 검증을 하지 않는다(qualityProfile.reason과 동일 사유) —
        # artifactReachable 을 그대로 대리한다("파일이 존재한다"만 확인, 파싱 가능성은 미검증).
        if last_published:
            entry["lastPublishedAt"] = last_published
        if age_seconds is not None:
            entry["ageSeconds"] = round(age_seconds, 1)
        if sla is not None:
            entry["slaSeconds"] = sla
        # checksumValid: 파티션 중 하나라도 sha256 실측이 있으면 True, 전부 null 이면 필드 자체를 생략(unknown).
        has_any_sha = any(part["sha256"] for part in p["partitions"])
        if artifact_reachable:
            entry["checksumValid"] = has_any_sha
        if reason:
            entry["reason"] = reason
        products_out.append(entry)

    return {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "products": products_out}


def derive_source_status(source_registry: dict, product_health: dict) -> dict:
    health_by_id = {h["productId"]: h for h in product_health["products"]}
    rank = {"ready": 0, "partial": 1, "stale": 2, "missing": 3, "invalid": 3}
    for s in source_registry["sources"]:
        statuses = [health_by_id[pid]["status"] for pid in s["products"] if pid in health_by_id]
        if not statuses:
            s["status"] = "unknown"
            continue
        worst = max(statuses, key=lambda st: rank.get(st, 3))
        s["status"] = {"ready": "active", "partial": "active", "stale": "degraded", "missing": "degraded", "invalid": "degraded"}[worst]
    return source_registry


def validate_all(manifest: dict, source_registry: dict, product_health: dict) -> None:
    import jsonschema

    schemas = {
        "manifest": (manifest, "data-product-manifest.v1.schema.json"),
        "source_registry": (source_registry, "source-registry.v1.schema.json"),
        "product_health": (product_health, "product-health.v1.schema.json"),
    }
    for name, (doc, schema_file) in schemas.items():
        schema = json.loads((CONTRACTS_DIR / schema_file).read_text())
        jsonschema.validate(instance=doc, schema=schema)
        print(f"schema OK: {name} <- contracts/{schema_file}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="meta/*.json 로컬 생성만(기본 동작과 동일 — 명시용 플래그)")
    parser.add_argument("--publish", action="store_true", help="생성 후 HF meta/ 로 업로드(HF_TOKEN 필요)")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "meta"))
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    api = HfApi(token=os.environ.get("HF_TOKEN") or None)
    entries = fetch_entries(api)
    grouped, unclassified = group_by_product(entries)

    manifest = build_manifest(grouped)
    source_registry = build_source_registry(manifest)
    product_health = build_product_health(manifest)
    source_registry = derive_source_status(source_registry, product_health)

    validate_all(manifest, source_registry, product_health)

    files = {
        "product_manifest.json": manifest,
        "source_registry.json": source_registry,
        "product_health.json": product_health,
    }
    for filename, doc in files.items():
        path = out_dir / filename
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path} ({path.stat().st_size} bytes)")

    total_matched = sum(len(v) for v in grouped.values())
    print(
        f"\ninventory: {len(entries)} tree entries, {total_matched} matched to "
        f"{len(PRODUCTS)} products, {len(unclassified)} unclassified"
    )
    if unclassified:
        print("unclassified paths (catalog gap — add to PRODUCTS or confirm intentional):")
        for e in unclassified[:20]:
            print(f"  {e.path}")
        if len(unclassified) > 20:
            print(f"  ... +{len(unclassified) - 20} more")

    if args.publish:
        if not os.environ.get("HF_TOKEN"):
            print("ERROR: --publish requires HF_TOKEN", file=sys.stderr)
            return 1
        write_api = HfApi(token=os.environ["HF_TOKEN"])
        for filename in files:
            write_api.upload_file(
                path_or_fileobj=str(out_dir / filename),
                path_in_repo=f"meta/{filename}",
                repo_id=REPO_ID,
                repo_type=REPO_TYPE,
                commit_message=f"Update meta/{filename} (Evidence Contract B1)",
            )
            print(f"uploaded meta/{filename} -> {REPO_ID}")
    else:
        print("\ndry-run: meta/ 로컬 생성만 완료, HF 업로드는 생략(--publish 로 발행)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
