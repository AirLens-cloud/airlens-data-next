# contracts

`Robeedau/airlens-live`에 올라가는 발행물의 계약을 둔다. 발행 코드(`scripts/etl/hf_publish.py`, `scripts/publish_contracts.py`)와
같은 레포에 있어야 발행 직전 검사에서 빠지는 계약이 생기지 않는다. 다른 레포에서 쓸 때는 이 레포의 태그를 기준으로 가져간다. 고칠 일이 있으면 여기서 고친다.

- [`EVIDENCE_CONTRACT.md`](EVIDENCE_CONTRACT.md): 사용자에게 보이는 값이 지켜야 할 규칙. 아래 스키마 대부분이 이 문서의 절을 구현한다.
- [`validate.py`](validate.py): 표준 라이브러리만 쓰는 검증기. 지원하지 않는 스키마 키워드가 있으면 검증을 시작하기 전에 실패한다.

## 스키마

| 계약 | 발행물 | 만드는 곳 | EVIDENCE_CONTRACT |
|---|---|---|---|
| `current-aq-grid.v1` | `aq-data/current-{pm25,pm10}-grid.json` | `scripts/etl/collect_noaa_aq.py` (`data-collect-hourly.yml`) | — |
| `web-aq-grid.v1` | `mac-data/data/web/v1/current-{pm25,pm10}-grid.json` | `scripts/etl/build_web_aq_grid.py` (`mac-data-publish.yml`) | — |
| `data-product-manifest.v1` | `meta/product_manifest.json` | `scripts/publish_contracts.py` (`contracts-publish.yml`) | §5-1 |
| `source-registry.v1` | `meta/source_registry.json` | 위와 같음 | §5-4 |
| `product-health.v1` | `meta/product_health.json` | 위와 같음 | §5-5 |
| `evidence-envelope.v1` | 사용자에게 보이는 값 하나의 최소 단위 | 아직 발행물 없음 (테스트에서만 사용) | §2 |

`current-aq-grid.v1`과 `web-aq-grid.v1`은 파일 이름이 같지만 다른 계약이다. 앞의 것은 조밀 격자이고 뒤의 것은 웹용으로 줄인 희소 격자다.

ML 예측 산출물의 스키마(`grid_latest.v1`, `data_quality.v1` 등)는 아직 ML 파이프라인(비공개) 쪽에 있다. 이 폴더로 옮겨 올 예정이다.

## 검증

```bash
python3 contracts/validate.py <payload.json>                             # payload의 schema_version으로 계약을 고른다
python3 contracts/validate.py <payload.json> --schema current-aq-grid.v1 # 계약 이름이나 경로를 직접 준다
```

`schema_version`(계약 이름)을 싣는 산출물은 기본값 `--schema auto`로 검증한다. 수집 산출물의 `schemaVersion`은 `"1.0"` 같은
숫자 버전이어서 계약 이름을 직접 줘야 한다. 발행할 때는 `hf_publish.py --schema …`가 업로드 직전에 같은 검증을 한다.

지원 키워드는 `type`, `properties`, `required`, `items`, `enum`, `const`, `additionalProperties`(bool), `minimum`, `maximum`,
`minItems`, `format`(`date-time`만)이다. 다른 키워드가 필요하면 `validate.py`에 먼저 구현하고 테스트를 붙인 다음 스키마에 쓴다.

## DQSS

DQSS(Data Quality Scoring System)는 관측소마다 매기는 데이터 품질 점수이고 이 절이 그 정의다. 계산은 ML 파이프라인의 규칙 기반 엔진(`RuleBasedDQSS`)이 맡는다.
결과는 `aq-data/data_quality.json`(`data_quality.v1`)과 `aq-data/quality-history/`로 발행된다.

### 점수

다섯 요소에 20점씩 균등하게 배분해(2026-09-02부터) 0–100점을 낸다.

| 요소 (필드) | 보는 것 | 값이 없을 때 `reasons` |
|---|---|---|
| freshness (`freshness`) | 마지막 관측 뒤 지난 시간 | `no_observation_timestamp` |
| completeness (`completeness`) | 24시간 동안 들어온 관측 수 / 기대 관측 수 | `no_reading_count_over_window` |
| consistency (`consistency`) | 같은 지점의 다른 출처와의 차이 | `no_cross_source_pair` |
| stability (`stability`) | 48시간 변동 (멈춘 센서 판정 포함) | `no_rolling_history`, `no_mean_for_normalised_dispersion` |
| model residual (`model_residual`) | 관측과 모델 예측의 평균 절대 차 | `no_model_prediction` |

- 측정하지 못한 요소는 0점이 아니라 `null`로 두고 이유를 `reasons`에 적는다. 중간값으로 채우지 않는다.
- `final_score`는 측정된 요소의 가중치 합(`measured_weight`)으로 다시 정규화한 값이다. 그래서 `measured_weight`와 함께 읽는다.
- 가중치에는 규제 표준이 없다. 균등 가중은 복합지표 문헌에서 가장 흔한 선택이며(Greco et al. 2018), 생산자는 OECD/JRC
  복합지표 핸드북의 절차대로 그 근거를 `weights_rationale`에 공개한다.

### 배지

| `badge` | `final_score` |
|---|---|
| `High` | 80 이상 |
| `Medium` | 50 이상 80 미만 |
| `Low` | 20 이상 50 미만 |
| `Unreliable` | 20 미만 |

- 반올림 전 점수로 판정한다. 컷 80/50/20은 외부 기준에 맞춘 값이 아니며 발행물도 `meta.cutoff_basis`에 그렇게 밝힌다.
- `measured_weight`가 60 미만이면(다섯 요소 중 측정된 것이 셋 미만) 점수는 남기고 `badge`를
  `null`로 둔다(`reasons.grade` = `insufficient_measured_weight`). 측정된 요소가 하나도 없으면 점수도 `null`이다(`no_computable_components`).
- 측정된 요소 가운데 하나라도 0점이면 `High`·`Medium`을 `Low`로 낮춘다(`component_floor_breach:<요소>`). 점수는 바꾸지 않는다.
- 이상치 탐지 모델이 준비돼 있으면 이상치로 판정된 관측소의 점수를 깎고(파이프라인 기본 20점) 배지를 다시 매긴다.
  보류와 하향 규칙은 감점 뒤에도 그대로 적용된다. 감점은 `reasons.anomaly`에 남고 탐지를 돌렸는지는 `meta.anomaly_check`에 적힌다.
- `reliability_score`와 `sensor_type_bonus`는 참고용 라벨이고 점수에 더하지 않는다.

### 웹 표시 등급

airlens-web은 `badge` 대신 `final_score`를 `src/lib/config/globeOntology.ts`의 `DQSS_GRADE_CUTOFFS`로 바꿔 A~F로 보여 준다.
80 이상은 A, 65 이상은 B, 50 이상은 C, 20 이상은 D, 나머지는 F다. 배지 보류와 하향은 이 변환에 들어가지 않는다.

### 이름이 비슷한 다른 값

- `confidence_grade`(`grid_latest.v1`의 예측별 A~F)는 예측 하나의 신뢰도다. DQSS가 아니다.
- 정책 데이터의 `data_quality.dqss_score`는 정책 효과 추정의 패널 적합도다. 이름만 같다.
- 출처, 편집, 센서, 정책 식별의 품질을 한 점수나 한 이름으로 합치지 않는다(EVIDENCE_CONTRACT §1 원칙 4).
