# Evidence Contract v1 (2026-08-28)

> **상태**: 정본. 2026-10부터 이 레포(`airlens-data/contracts/`)에 둔다. 규칙 내용은 v1(2026-08-28) 그대로다.
> **목표**: 사용자에게 보이는 핵심 값은 모두 unit·nature·validTime·source에 연결돼야 한다. 이 문서는 그 실행 스펙이다.
> **적용 범위**: producer = ML 파이프라인(private) + `airlens-data`(수집·발행) → HF/정적 발행물. consumer = `airlens-web`(렌더) + public REST/MCP API. 발행 스키마는 이 폴더의 `*.schema.json`이다.

## 1. 원칙 (비협상)

1. `value=null`은 오류가 아니라 명시적 상태일 수 있다 — null을 임의 기본값(예: `globalAQI: 50`)으로 대체하지 않는다.
2. `LIVE` 라벨은 `validStart`·SLA·실측 freshness가 있을 때만 쓴다.
3. p10/p90 등 불확실성 구간은 method와 calibration snapshot이 있을 때만 노출한다 — 임의 band 생성 금지.
4. source trust · editorial trust · sensor quality · policy identification quality를 같은 점수/명칭으로 합치지 않는다 (DQSS 이름 충돌 재발 방지).
5. UI가 요구하는 field를 만들기 위해 producer 의미를 변환하지 않는다 (grid cell → "station" 위장 금지).
6. 규제 평균창 라벨(`24h`/`8h` 등)은 실제 rolling completeness가 증명될 때만 허용 — 시간별 모델값에 부착 금지.

## 2. EvidenceEnvelope v1

모든 사용자 노출 값의 최소 단위.

```ts
type DataNature =
  | 'observation'
  | 'analysis'
  | 'interpolated'
  | 'forecast'
  | 'satellite-derived'
  | 'inferred'
  | 'policy'

interface EvidenceEnvelope<T> {
  schemaVersion: '1.0'
  receiptId: string
  phenomenon: string
  value: T | null
  unit: string
  nature: DataNature
  time: {
    eventTime?: string
    referenceTime?: string
    validStart: string
    validEnd?: string
    ingestedAt: string
    publishedAt: string
  }
  space: {
    geometry?: GeoJSON.Geometry
    bbox?: [number, number, number, number]
    resolution?: string
    h3?: string
  }
  source: {
    sourceId: string
    provider: string
    upstreamDataset?: string
    licenseCode: string
    attribution: string
  }
  quality: {
    tier: 'reference' | 'low-cost' | 'model' | 'community' | 'unknown'
    freshness?: number
    completeness?: number
    agreement?: number
    stability?: number
    status: 'ready' | 'partial' | 'stale' | 'withheld' | 'no-coverage' | 'unavailable'
    reason?: string
  }
  uncertainty?: {
    method: string
    lower: number
    upper: number
    coverage: number
    calibrationSnapshotId?: string
  }
  lineage: {
    datasetVersion: string
    transformations: string[]
    artifactSha256: string
    codeSha?: string
  }
}
```

`time`은 다섯 시각(event/reference/valid/ingested/published) 계약과 1:1이다.

## 3. AnalysisCursor

관측 화면(Observatory) 전체가 공유하는 선택 상태. **URL round-trip 의무** — 새 브라우저가
URL만으로 위치·시각·변수·scale·선택 근거를 복원하지 못하면 shareable scene이 아니다.

```ts
interface AnalysisCursor {
  schemaVersion: '1.0'
  location?: { lat: number; lon: number; label?: string }
  bbox?: [number, number, number, number]
  phenomenon: string
  mode: 'now' | 'forecast' | 'history' | 'compare' | 'research'
  validTime?: string
  referenceTime?: string
  datasetVersion?: string
  layerIds: string[]
  selectionIds: string[]
  scale?: { id: string; min: number; max: number; locked: boolean }
  compare?: { a: string; b: string }
  evidenceReceiptIds: string[]
}
```

## 4. 연구 artifact chain

| 객체 | 역할 | immutable 기준 |
|---|---|---|
| `SceneReceipt` | Cursor와 화면 버전을 고정 | URL + cursor hash |
| `QueryPlan` | dataset·변수·공간·시간·필터·정규화 선언 | canonical JSON + SHA-256 |
| `DatasetSnapshot` | 실제 입력 파일·partition·license 고정 | manifest + artifact hashes |
| `AnalysisRun` | transformation·chart·통계 결과 고정 | plan hash + code/env version |
| `ExperimentReceipt` | hypothesis·split·baseline·metrics·failure slice | snapshot + code SHA + env lock |
| `PublicationReceipt` | 글·figure·citation·한계·재현 명령 묶음 | signed/static bundle manifest |

`PublicationReceipt` 필수 섹션: `What this supports` / `What this does not support` /
withheld 결과 / 표본 부족 / 실패 slice. 결과만 담은 발행물은 반려한다(운영자 승인형).

## 5. Producer 의무 (ML 파이프라인 + airlens-data)

1. **Data Product Manifest v1** — 모든 공개 product 발행물에 동반:
   product id/title/description/owner · schema version · nature · variables/units ·
   spatial/temporal coverage·resolution · source registry refs + license/attribution ·
   published/valid time + freshness SLA + last successful ingest · partitions/files/bytes/
   row counts/SHA-256 · quality profile(null/duplicate/coordinate/temporal gap/agreement) ·
   access(UI sample·REST/MCP·direct artifact·citation) · known limitations ·
   deprecation/supersession.
2. **파티션 규약** — `product={id}/schema={v}/nature={n}/phenomenon={var}/year=/month=/day=/h3={prefix}/part-*.parquet`.
   시간·공간 pruning은 실제 사용자 query와 일치. H3/geohash는 인덱스이지 원 geometry 대체가
   아니다. partition마다 row count·min/max time·bbox·null ratio·hash를 manifest에 기록.
   low-cost와 reference를 합쳐 저장해도 tier는 절대 소실하지 않는다.
3. **저장 계층 책임** — Raw(append-only 원본) / Normalized(unit·time·geometry·source 통일,
   Parquet/Arrow) / Product(목적별 materialization, versioned) / Receipt(canonical JSON +
   SHA-256, immutable). Bronze/Silver/Gold 유행어 대신 이 책임 구분을 고정.
4. **source_registry 발행** — 소스 status·license·coverage·cadence·last success를 발행물로
   제공한다. UI 코드 상수는 registry의 consumer로 대체한다 — 코드에 소스 사실을 하드코딩하지 않는다.
5. **ProductHealth 발행** — process liveness와 dataset readiness를 분리:

```ts
interface ProductHealth {
  productId: string
  status: 'ready' | 'partial' | 'stale' | 'missing' | 'invalid'
  lastProbeAt: string
  lastPublishedAt?: string
  ageSeconds?: number
  slaSeconds?: number
  schemaValid: boolean
  artifactReachable: boolean
  checksumValid?: boolean
  reason?: string
}
```

`/v1/health` = process liveness, `/v1/health/products` = artifact readiness. catalog 목록만
반환하는 health는 §1 위반이다.

## 6. Consumer 의무 (airlens-web + public API)

1. **렌더 게이트** — nature/unit/valid/source 미확정 값은 숫자로 렌더하지 않는다. 부재는
   void/withheld + 이유·마지막 정상 시각·다음 갱신으로 렌더한다 (demo/synthetic 대체 금지;
   공개 데이터 표면에서는 `DEMO` 표시 샘플도 쓰지 않는다).
2. **상태 어휘 정합** — airlens-web의 기존 자산과 다음과 같이 매핑한다:
   - 옛 웹 앱의 `dataState.ts` 7상태(loading/ready/partial/empty/no-coverage/unavailable/error)는
     본 계약 `quality.status` + 로딩/오류 UI 상태의 상위집합 — airlens-web 이식 시
     `quality.status` 6값(ready/partial/stale/withheld/no-coverage/unavailable)을 데이터
     상태로, loading/error를 전송 상태로 분리한다.
   - airlens-web forecast의 null-range 정직 표기("no band published")는 §1-3의 선례 구현 —
     유지·일반화한다.
   - airlens-web `aqi.ts`의 EPA breakpoint 명시 환산은 §1-6 적합 — 비표준 환산(`pm25*1.25`)은
     재도입 금지.
3. **타입 분리 강제** — `GridCell`과 `StationObservation`은 별도 타입이며 assignability를
   타입 테스트로 차단한다. 이 조항을 어기는 코드는 수정 대상이다. `EditorialTrust`와
   `DataQuality`는 별도 ontology·배지.
4. **Evidence 도달 규칙** — 모든 렌더된 mark/수치에서 2 interaction 이내에
   EvidenceEnvelope(값·시각·source·quality·lineage)에 도달할 수 있어야 한다 (Evidence Rail).
5. **시각 문법 연동** — nature → mark 문법은 observation=crisp / analysis·interpolated=soft /
   forecast=hatch / stale=muted+timestamp strike / withheld·no-coverage=void+이유다.
   농도(색)와 provenance(형태·경계·질감)를 같은 채널에 겹치지 않는다.

## 7. Conformance

- 핵심 사용자 노출 값 100%가 EvidenceEnvelope 경유 — schema/fixture/contract test로 강제.
- withheld/no-coverage에는 reason 필수 (스키마 required).
- stale artifact 주입 시 ProductHealth `stale` + UI LIVE 라벨 제거 (자동 테스트).
- `GridCell`↛`StationObservation` assignability 타입 테스트.
- 규제 평균창 라벨은 rolling completeness 증명 데이터에만 (테스트로 고정).
- 표준 없는 AQI 환산 입력 거부 (환산기는 표준 id + averaging window 명시 필수).

## 8. History

- 2026-08-28 — v1 제정. producer/consumer 의무를 분리했다.
- 2026-10-10 — 정본 위치를 이 레포로 옮겼다. 내부 설계 문서 참조와 시점이 지난 위반 사례 목록을 지웠다. 규칙과 절 번호는 그대로다.
