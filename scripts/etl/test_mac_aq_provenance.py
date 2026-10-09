"""mac_aq_provenance.py 단위 테스트 (AAA) — 필수 provenance 필드 존재 + 값 비변조."""
import json
from datetime import datetime, timezone

import mac_aq_adapter as adapter
import mac_aq_provenance as provenance


def _envelope(**overrides):
    base = dict(
        kind="analysis", source="CAMS", source_version="cycle-20260716T1200Z",
        generated_at="2026-07-16T15:00:00Z", observed_at=None,
        valid_at="2026-07-16T15:00:00Z", expires_at="2026-07-17T03:00:00Z",
        resolution_km=44, attribution="Contains modified Copernicus ... information",
        quality={"grade": "B", "score": 84},
    )
    base.update(overrides)
    return adapter.build_envelope(**base)


def test_build_source_provenance_surfaces_required_fields_without_mutating():
    # Arrange
    env = _envelope()
    # Act
    result = provenance.build_source_provenance(env)
    # Assert — Glass-box: 클라이언트가 "어디서 왔나"를 표시할 수 있는 최소 필드셋
    for field in ("source", "sourceVersion", "kind", "collectedAt", "observedAt",
                  "validAt", "expiresAt", "attribution"):
        assert field in result
    assert result["source"] == "CAMS"
    assert result["collectedAt"] == env["generatedAt"]  # collectedAt = 수집 시각(generatedAt)


def test_build_pollutant_transforms_extracts_conversion_per_key():
    # Arrange
    pollutants = {
        "pm25": {"unit": "ug/m3", "sourceVariable": "pm2p5", "conversion": "kg/m3 x 1e9", "data": [1.0]},
        "o3": {"unit": "ug/m3", "sourceVariable": "go3", "conversion": "mixing x density", "data": [2.0]},
    }
    # Act
    result = provenance.build_pollutant_transforms(pollutants)
    # Assert
    assert result["pm25"] == {"sourceVariable": "pm2p5", "conversion": "kg/m3 x 1e9", "unit": "ug/m3"}
    assert result["o3"]["conversion"] == "mixing x density"
    assert "data" not in result["pm25"]  # 값 자체는 provenance 블록에 복제 안 함


def test_build_provenance_includes_qa_report_and_pipeline_version():
    # Arrange
    env = _envelope()
    pollutants = {"pm25": {"unit": "ug/m3", "sourceVariable": "x", "conversion": "y", "data": [1.0]}}
    qa_report = {"kind": "grid", "overallAnomalyRatio": 0.0}
    now = datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc)
    # Act
    result = provenance.build_provenance(env, pollutants, qa_report, processed_at=now)
    # Assert
    assert result["pipelineVersion"] == provenance.PIPELINE_VERSION
    assert result["processedAt"] == "2026-07-16T16:00:00Z"
    assert result["sources"][0]["source"] == "CAMS"
    assert result["transforms"]["pm25"]["sourceVariable"] == "x"
    assert result["qa"] == qa_report


def test_build_point_provenance_carries_only_its_own_source():
    # Arrange — 관측소 자기 envelope 하나
    env = _envelope(kind="observation", source="EEA-UTD", source_version="E2a-UTD-FR12021")
    # Act
    result = provenance.build_point_provenance(env, reading_count=1453, reading_index=7)
    # Assert — 이웃 관측소가 아니라 자기 자신 1건만
    assert len(result["sources"]) == 1
    assert result["sources"][0]["sourceVersion"] == "E2a-UTD-FR12021"
    assert result["readingCount"] == 1453
    assert result["readingIndex"] == 7


def test_build_point_provenance_keeps_its_own_pollutant_transforms():
    # Arrange — Glass-box: 변환식은 레코드마다 자기 것이 남아야 한다
    env = _envelope(source_version="E2a-UTD-IT0001")
    env["pollutants"] = {"pm25": {"unit": "ug/m3", "sourceVariable": "x", "conversion": "y", "value": 1.0}}
    # Act
    result = provenance.build_point_provenance(env, reading_count=2, reading_index=0)
    # Assert
    assert result["transforms"]["pm25"]["sourceVariable"] == "x"


def test_point_provenance_size_grows_linearly_not_quadratically():
    """관측소마다 sourceVersion 이 다른 소스(EEA)에서 발행물이 N² 로 자라지 않는지.

    회귀 대상 = 2026-07-30 발행분 1,004,136,344 B. 리스트 전체의 source 목록을 모든
    레코드에 공유하던 구현에서 관측소 1,453개가 각각 1,453개 source 항목을 실었다.
    """
    # Arrange — EEA 처럼 sourceVersion 이 전부 다른 관측소 200개
    envelopes = [_envelope(source_version=f"E2a-UTD-FR{i:05d}") for i in range(200)]
    # Act
    blocks = [
        provenance.build_point_provenance(env, reading_count=len(envelopes), reading_index=i)
        for i, env in enumerate(envelopes)
    ]
    # Assert — 총 source 항목 수가 N² (40,000) 이 아니라 N (200)
    assert sum(len(b["sources"]) for b in blocks) == len(envelopes)
    # 직렬화 크기도 레코드당 상수 — 목록이 새면 여기서 곧바로 터진다
    sizes = [len(json.dumps(b)) for b in blocks]
    assert max(sizes) < 2 * min(sizes)
