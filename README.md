# airlens-data

AirLens 데이터 수집 파이프라인 — 스케줄 수집기가 외부 공개 API에서 데이터를 모아
Hugging Face 데이터셋 [`Robeedau/airlens-live`](https://huggingface.co/datasets/Robeedau/airlens-live)로 발행한다.
(2026-08-26 `AirLens-cloud/AirLens` 모노레포에서 분리. 2026-10-10 public 전환 — 모든 스케줄 잡이 GitHub hosted 러너(`ubuntu-latest`)에서 돈다. 이전 이력은 private `airlens-data-archive`에 보존.)

## 워크플로

| 워크플로 | 주기 | 수집원 → HF 발행 경로 |
|---|---|---|
| `data-collect-hourly` | 3h | Open-Meteo 날씨·해양·가스(O3/NO2/CO) + NOAA GEFS PM2.5/PM10 + GFS 바람 → `aq-data/`, `wind-data/` |
| `timeline-collect` | 3h | NOAA GEFS PM2.5 ±24h 타임라인 → `aq-data/timeline/` |
| `forecast-collect` | 6h | Open-Meteo CAMS 도시별 PM2.5 예보 → `aq-data/forecast.json` (도시 시드 = `seed/cams_forecast.json`) |
| `firms-collect` | 6h | NASA FIRMS 화재 핫스팟 → `wind-data/active-fires.json` |
| `news-collect` | 6h | RSS 뉴스 → `news-data/articles.json` |
| `mac-data-publish` | 1h | CAMS + GEFS-chem + AirKorea + EEA → `mac-data/data/{mac,web}/v1/` |
| `hf-live-squash` | 주간(일 19:20 UTC) | HF repo 히스토리 squash (용량 관리) |
| `openaq-bulk-collect` | 일간(01:45 UTC) | OpenAQ 파라미터별 벌크 수집 → `insights-data/openaq-bulk/` |
| `policy-collect` | 주간(일 04:00 UTC) | 대기질 정책 레지스트리 → `insights-data/policy/` |
| `contracts-publish` | 3h | 산출물 계약 manifest/registry/health 생성 → `meta/` (`product_manifest.json`·`source_registry.json`·`product_health.json`) |
| `shadow-retention` | 일간(02:40 UTC) | E2 사이드카 shadow 슬롯 14일 초과분 정리 → `openaq-shadow/`, `sensor-community-shadow/` |
| `mac-snapshot-healthcheck` | 1h(:35) | 발행된 mac 스냅샷 신선도 프로브 (발행 없음, advisory) |
| `e2-shadow-freshness` | 6h(:25) | `openaq-shadow/`·`sensor-community-shadow/` 최신 슬롯 프로브 (발행 없음) |
| `keepalive` | 월 2회(1·15일) | public 레포 60일 비활동 자동 비활성화 방지 (워크플로 재활성화, 발행 없음) |
| `pr-check` | PR/push | 테스트·린트 (발행 없음) |

ETL 스크립트는 `scripts/etl/` (모노레포 경로 보존 — 워크플로 수정 최소화).
HF 초기 시드(hf-live-seed)는 모노레포 정적 데이터 의존이라 이관하지 않음(이미 시드 완료).

## Secrets (Actions repo-level)

등록: Settings → Secrets and variables → Actions → New repository secret

| 이름 | 용도 |
|---|---|
| `HF_TOKEN` | HF `Robeedau/airlens-live` write (전 워크플로) |
| `NASA_FIRMS_MAP_KEY` | firms-collect |
| `CDS_API_KEY` | mac-data-publish (Copernicus ADS — CAMS. `CDS_API_URL`은 워크플로에 하드코딩) |
| `AIRKOREA_API_KEY` | mac-data-publish (공공데이터포털 에어코리아) |

## 로컬 훅

```bash
brew install gitleaks
git config core.hooksPath scripts/git-hooks
```
