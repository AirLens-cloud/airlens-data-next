#!/usr/bin/env python3
"""macOS 무료 글로벌 근실시간 대기질 파이프라인 — provenance(출처 이력) 계층.

설계 배경(비공개 내부 노트 2026-07-16 — 동작 정의는 코드·테스트·계약):
§"필수 레코드" — `source`/`sourceVersion`/`generatedAt`/`observedAt`/`validAt`/`attribution`
필드가 이미 envelope 에 있다. 이 모듈은 그 필드들을 **흩어놓지 않고 한 곳(`provenance`
블록)에 모아** mac 클라이언트가 "이 값이 어디서 왔나"를 한 번에 렌더링할 수 있게 한다
(Glass-box 정합). 값 자체는 건드리지 않는다 — 순수 메타데이터 조립.

`mac_aq_adapter.py` 는 수정하지 않는다 — 이 모듈은 그 위에 얹는 신규 계층.
"""
from __future__ import annotations

from datetime import datetime, timezone

PIPELINE_VERSION = "mac-p1-qa-1"  # 이 QA/provenance/quantize 계층의 버전 태그


def build_source_provenance(envelope: dict) -> dict:
    """envelope 공통 필드에서 provenance 표시용 요약 1건 추출. 값 변조 없음."""
    return {
        "source": envelope.get("source"),
        "sourceVersion": envelope.get("sourceVersion"),
        "kind": envelope.get("kind"),
        "collectedAt": envelope.get("generatedAt"),  # 수집 파이프라인이 데이터를 받은 시각
        "observedAt": envelope.get("observedAt"),    # 관측소 실측 시각(격자 소스는 null)
        "validAt": envelope.get("validAt"),          # 모델/관측값이 유효한 시각
        "expiresAt": envelope.get("expiresAt"),
        "resolutionKm": envelope.get("resolutionKm"),
        "attribution": envelope.get("attribution"),
    }


def build_pollutant_transforms(pollutants: dict) -> dict:
    """pollutant 별 원본 변수명 + 변환식 — 이미 어댑터가 각 값에 기록해 둔 것을
    provenance 블록에서 한 번에 보이게 재수집(단일 소스 of display, 값 복제 아님)."""
    transforms = {}
    for key, block in pollutants.items():
        if not isinstance(block, dict):
            continue
        transforms[key] = {
            "sourceVariable": block.get("sourceVariable"),
            "conversion": block.get("conversion"),
            "unit": block.get("unit"),
        }
    return transforms


def build_provenance(
    envelope: dict,
    pollutants: dict,
    qa_report: dict,
    *,
    processed_at: datetime | None = None,
) -> dict:
    """단일 소스 reading(grid 또는 point 1건) 의 provenance 블록.

    호출자(`build_mac_aq_snapshot.py`)가 최종 레코드의 `provenance` 키에 이 결과를 얹는다.
    """
    processed_at = processed_at or datetime.now(timezone.utc)
    return {
        "pipelineVersion": PIPELINE_VERSION,
        "processedAt": processed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [build_source_provenance(envelope)],
        "transforms": build_pollutant_transforms(pollutants),
        "qa": qa_report,
    }


def build_point_provenance(
    envelope: dict,
    *,
    reading_count: int,
    reading_index: int,
    processed_at: datetime | None = None,
) -> dict:
    """관측소 1건의 provenance. `sources` 는 **자기 자신 1건**이다.

    이전 구현(`build_multi_source_provenance`)은 리스트 전체의 source 목록을 하나 만들어
    모든 레코드에 같이 붙였다. "리스트 전체가 보통 같은 `source`+`sourceVersion` 한 번의
    수집 실행에서 나온다"는 전제였는데, EEA 는 `sourceVersion` 을 **관측소마다** 찍는다
    (`E2a-UTD-<EoI 코드>` — `merge_point_readings.reading_key` 가 바로 그 유일성에 기대
    동일성 키로 쓴다). 그래서 dedupe 가 한 건도 합치지 못했고, N개 관측소 각각이 N개
    source 항목을 실어 크기가 N² 로 자랐다.

    실측(2026-07-30 발행분): 관측소 1,453개 → 레코드 1건 482KB → 파일 1,004,136,344 B
    (약 1GB)가 GitHub Pages 로 발행돼 mac 앱 클라이언트가 매 갱신마다 받고 있었다.

    자기 source 1건만 싣는 게 크기뿐 아니라 **정직성** 면에서도 맞다 — FR12021 레코드의
    provenance 가 다른 관측소 1,452개의 수집 이력을 나열하던 것은 정보가 아니라 소음이다.
    Glass-box 는 그대로다: 각 레코드가 자기 출처·버전·귀속·시각·변환식을 전부 공개한다
    (오히려 이전엔 빠져 있던 `transforms` 가 되살아난다).
    """
    processed_at = processed_at or datetime.now(timezone.utc)
    return {
        "pipelineVersion": PIPELINE_VERSION,
        "processedAt": processed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [build_source_provenance(envelope)],
        "transforms": build_pollutant_transforms(envelope.get("pollutants") or {}),
        "readingCount": reading_count,
        "readingIndex": reading_index,
    }
