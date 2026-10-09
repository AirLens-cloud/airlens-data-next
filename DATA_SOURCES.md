# DATA_SOURCES — 외부 데이터 출처·라이선스·표기

이 파이프라인이 수집해 Hugging Face 데이터셋
[`Robeedau/airlens-live`](https://huggingface.co/datasets/Robeedau/airlens-live)로 발행하는
외부 출처 목록이다. **이 레포의 코드 라이선스(AGPL-3.0-or-later, `LICENSE`)는 데이터에 적용되지
않는다.** 각 출처의 라이선스·약관이 해당 데이터에 적용된다.

- 열거 기준: `scripts/etl/collect_*.py`, `scripts/etl/collect_policies/*`, `e2-sidecar/sidecar/*.ts`, 워크플로.
- 검증 방식: 제공처 약관 페이지의 내용을 웹 검색 결과로 확인했다(제공처 도메인 직접 fetch 는 이 환경에서 차단됨).
  확인하지 못한 항목은 추측하지 않고 **미확인**으로 적는다. 미확인 항목은 발행 전 제공처 약관에서 직접 확인해야 한다.
- 데이터셋 카드(`scripts/etl/hf_publish.py`)는 단일 라이선스를 선언하지 않는다. `license: other`로 두고 이 문서를
  링크한다(2026-10-10 결정, 이전 선언은 `cc-by-4.0`). 아래 "충돌·주의" 항목은 단일 cc-by-4.0 선언과 맞지 않았던
  출처이며, 이용자가 특히 확인해야 할 조건이다.

## 출처 표

| 출처 | 수집 내용 (코드) | 라이선스·약관 | 필수 표기 | 약관 페이지 |
|---|---|---|---|---|
| Copernicus CAMS (ADS) | 전지구 대기질 분석/예보 (`collect_cams_global.py`) | Licence to use Copernicus Products (CAMS 라이선스) | 수정·가공 시 "Contains modified Copernicus Atmosphere Monitoring Service information [연도]" + 유럽위원회·ECMWF 는 이용에 책임이 없다는 고지. 코드 표기: "Contains modified Copernicus Atmosphere Monitoring Service information" | https://apps.ecmwf.int/datasets/licences/cams |
| NOAA/NCEP GEFS-Aerosols | PM2.5/PM10 격자, ±24h 타임라인 (`collect_noaa_aq.py`, `collect_gefs_chem_global.py`, `collect_gefs_timeline.py`) | 미국 정부 저작물/퍼블릭 도메인. NODD 배포 데이터는 CC0 1.0 (GFS 레지스트리 문구로 확인, GEFS 개별 문구는 **미확인**) | 법적 필수는 아니나 NOAA 가 출처 표기 요청. NOAA 후원·보증을 암시 금지, 가공 데이터를 원본으로 표시 금지. 코드 표기: "NOAA/NCEP GEFS-Aerosols (U.S. public domain, https://registry.opendata.aws/noaa-gefs/)" | https://registry.opendata.aws/noaa-gefs/ |
| NOAA/NCEP GFS | 지표·850hPa 바람 (`collect_gfs_wind.py`) | NODD: CC0 1.0 / 퍼블릭 도메인 | 위와 동일 (표기 요청, 보증 암시 금지) | https://registry.opendata.aws/noaa-gfs-bdp-pds/ |
| Open-Meteo | 날씨·해양·가스(O3/NO2/CO)·꽃가루·CAMS 예보 (`openmeteo_chunks.py`, `data-collect-hourly.yml`, `forecast-collect.yml`) | 데이터 CC BY 4.0. **무료 API 는 비상업 용도만 허용** (일 10,000회·시간 5,000회·분 600회 미만) | CC BY 4.0 표기. 표시 시 "Weather data by Open-Meteo.com" 링크 | https://open-meteo.com/en/terms |
| 에어코리아(한국환경공단, 공공데이터포털 API) | 국내 관측소 실시간 대기질 (`collect_mac_airkorea.py`, `e2-sidecar/sidecar/airkorea_shadow.ts`) | 공공누리 제3유형 (출처표시·변경금지). 공공데이터포털 데이터셋 15073861 상세의 "이용허락범위"에서 직접 확인(2026-10-10) | 출처 표시 필수. 코드 표기: "Korea Environment Corporation (AirKorea) — data.go.kr public API". 변경금지 조건이라 가공(재격자화·병합)한 값의 재배포는 별도 확인 필요 | https://www.data.go.kr/data/15073861/openapi.do |
| EEA Up-To-Date 대기질 | 유럽 관측소 UTD (`collect_mac_eea_utd.py`) | EEA 저작권 고지: CC BY (EEA 페이지는 버전 미명시, 코드 표기는 CC BY 4.0). 제3자 제공 콘텐츠는 제외 | EEA 를 원 출처로 표기, 의미 왜곡 금지. 코드 표기: "European Environment Agency (EEA) Up-To-Date air quality data — CC BY 4.0" | https://www.eea.europa.eu/legal/copyright |
| OpenAQ | 관측소별 측정값·벌크 CSV (`collect_openaq_bulk.py`, `e2-sidecar/sidecar/openaq_shadow.ts`, `openaq_backfill.ts`) | 관측소(원 제공처)별로 다른 라이선스. OpenAQ 는 개방 라이선스(CC0/CC BY 4.0 등) 데이터만 수집한다고 명시. API 레퍼런스에는 "무료는 비상업 용도"라는 문구가 남아 있어 문서 간 불일치 | 원 제공처별 표기(API 의 license/sourceUrl 필드). 이용자가 제공처 약관을 준수할 책임 | https://docs.openaq.org/about/terms , https://docs.openaq.org/resources/licenses |
| Sensor.Community | 전지구 저가 센서 스냅샷 (`e2-sidecar/sidecar/sensor_community_shadow.ts`) | **미확인** (공식 라이선스 문구를 확인하지 못함. ODbL 일 가능성이 있으나 추정일 뿐) | 미확인 | https://sensor.community/ , https://archive.sensor.community/ |
| NASA FIRMS | 활성 화재 핫스팟 VIIRS/MODIS NRT (`collect_firms.py`) | NASA 오픈 데이터(전면 공개 공유 정책). 상세 약관은 FIRMS/LANCE 페이지 | NASA FIRMS(LANCE, ESDIS) 사용 인정 문구 권장: "We acknowledge the use of data and/or imagery from NASA's Fire Information for Resource Management System (FIRMS) …" (전문은 FIRMS 페이지 최신본 확인 필요) | https://www.earthdata.nasa.gov/data/tools/firms |
| Climate Policy Radar (HF `ClimatePolicyRadar/all-document-text-data`) | 정책 문서 메타·본문 (`collect_policies/collect_cpr.py`) | **미확인** (`collect_policies/SOURCES.md` 는 CC-BY-4.0 로 기재하나 제공처 페이지에서 확인하지 못함) | 미확인 | https://huggingface.co/datasets/ClimatePolicyRadar/all-document-text-data |
| WHO 대기질 기준 | 국가별 대기질 기준 (`collect_policies/collect_who_standards.py`, 코드 내장 표) | WHO 일반 저작권 정책: CC BY-NC-SA 3.0 IGO 로 발행된 자료는 비상업·동일조건. 이 도구 페이지에 대한 개별 라이선스는 **미확인** | 일반 WHO 인용 형식 "[제목]. [발행지]: World Health Organization; [연도]. Licence: CC BY-NC-SA 3.0 IGO." (해당 자료에 적용될 때). 개별 확인 필요 | https://www.who.int/about/who-we-are/publishing-policies/copyright , https://www.who.int/tools/air-quality-standards |
| US Federal Register | EPA 대기질 규칙 (`collect_policies/collect_federal_register.py`) | 미국 연방정부 저작물(퍼블릭 도메인으로 알려짐). API 공식 약관 문구는 **미확인** | 요구 없음(미확인) | https://www.federalregister.gov/developers/documentation/api/v1 |
| 각국 정부·UNEP·CCAC 정책(수작업 큐레이션) | 주요 대기질 정책 요약 (`collect_policies/collect_major_policies.py`) | **미확인** (기관별 상이, 개별 확인 안 함) | 미확인 | 각 레코드의 `sourceUrl` |
| 뉴스 RSS 20개 매체 | 제목·요약(≤500자)·URL·대표 이미지 URL (`collect_news.py`): UNEP, WHO, Climate Home News, Clean Air Fund, Phys.org, IQAir, Carbon Brief, The Guardian, EcoWatch, Environmental Health News, China Dialogue, The Third Pole, US EPA, EEA, NASA Climate, Mongabay(+India), Yale Environment 360, Dialogue Earth, Eco-Business | **미확인** — 매체별 저작권이 적용되며 개별 약관은 확인하지 않음 | 매체명·원문 링크 (데이터에 `source`/`url` 로 포함) | 각 매체 사이트 약관 (`collect_news.py` 의 `site_url`) |

선택 기능: `collect_llm_extract.py` 는 수동 실행 시에만 OpenAI API 를 호출하며 외부 *데이터* 출처가 아니다
(기본 크론에서는 꺼져 있음).

## 충돌·주의 (이전 cc-by-4.0 선언과의 관계)

해결하지 않고 기록만 한다.

1. **Open-Meteo 무료 API 는 비상업 한정**이다. 데이터는 CC BY 4.0 이지만, 무료 엔드포인트로 수집한 데이터를 상업적으로
   재배포·이용하는 것이 허용되는지는 제공처 확인이 필요하다.
2. **WHO(CC BY-NC-SA 3.0 IGO 일 경우)** 는 비상업·동일조건이라 cc-by-4.0 과 양립하지 않는다 (개별 라이선스 미확인).
3. **Sensor.Community** 가 ODbL 이라면 공유 동일조건(share-alike)이 걸린다 (미확인).
4. **OpenAQ** 는 관측소별 원 제공처 라이선스를 따르며, 일부는 CC BY 4.0 이 아닐 수 있다.
5. **뉴스 RSS** 제목·요약은 제3자 저작물이라 CC BY 4.0 으로 재라이선스할 권리가 없을 수 있다.
6. **에어코리아는 공공누리 제3유형(출처표시·변경금지)**이다. 상업 이용은 허용되지만 변경금지라, 가공한 값을 cc-by-4.0 으로
   재배포하는 것과 맞지 않을 수 있다.
7. **Climate Policy Radar·각국 정책** 은 라이선스 미확인.
