"""publish_contracts.py 계약 시험.

EVIDENCE_CONTRACT.md §7 conformance 요구 중 이 스크립트가 커버하는 부분:
  - 생성물이 자기 스키마에 valid 한지 (jsonschema, 픽스처 기반)
  - withheld/no-coverage 상태의 EvidenceEnvelope 은 reason 없이 통과하지 못한다
  - stale ProductHealth 는 reason 없이 통과하지 못한다
  - PRODUCTS 카탈로그의 sourceRefs 가 SOURCES 카탈로그에 실제로 존재한다(오타 방지)

네트워크 호출 0 — HF API 는 fake RepoFile 객체로 대체한다(HfApi.list_repo_tree 자체를
호출하지 않고, group_by_product/build_manifest/build_product_health 순수 함수만 시험).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import publish_contracts as pc
import pytest

CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"


def _schema(name: str) -> dict:
    return json.loads((CONTRACTS_DIR / name).read_text())


@dataclass
class FakeLastCommit:
    date: datetime


@dataclass
class FakeLfs:
    sha256: str


@dataclass
class FakeRepoFile:
    path: str
    size: int
    last_commit: FakeLastCommit | None = None
    lfs: FakeLfs | None = None


def _file(path: str, size: int = 100, age_seconds: int = 0, sha256: str | None = None) -> FakeRepoFile:
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    lfs = FakeLfs(sha256=sha256) if sha256 else None
    return FakeRepoFile(path=path, size=size, last_commit=FakeLastCommit(date=ts), lfs=lfs)


# ── §7 conformance: 카탈로그 자체 정합성 (오타/드리프트 방지) ──────────


def test_every_product_source_ref_exists_in_source_registry():
    source_ids = {s["sourceId"] for s in pc.SOURCES}
    for product in pc.PRODUCTS:
        for ref in product["sourceRefs"]:
            assert ref in source_ids, f"{product['productId']} references unknown source {ref!r}"


def test_product_ids_are_unique():
    ids = [p["productId"] for p in pc.PRODUCTS]
    assert len(ids) == len(set(ids))


def test_source_ids_are_unique():
    ids = [s["sourceId"] for s in pc.SOURCES]
    assert len(ids) == len(set(ids))


def test_every_product_has_at_least_one_declared_path():
    for product in pc.PRODUCTS:
        assert product["paths"], f"{product['productId']} has no declared paths"


# ── build_manifest / build_source_registry / build_product_health ──────


@pytest.fixture
def sample_entries():
    """PRODUCTS 카탈로그 중 두 product 만 실측이 있고 나머지는 0건인 픽스처."""
    return [
        _file("aq-data/current-pm25-grid.json", size=2_500_000, age_seconds=3600),
        _file("aq-data/current-pm10-grid.json", size=2_500_000, age_seconds=3600),
        _file("aq-data/current-o3-grid.json", size=80_000, age_seconds=3600),
        _file("aq-data/current-no2-grid.json", size=80_000, age_seconds=3600),
        _file("aq-data/current-co-grid.json", size=80_000, age_seconds=3600),
        _file("aq-data/pollen-grid.json", size=46_000, age_seconds=3600),
        _file("wind-data/active-fires.json", size=1_986_293, age_seconds=100_000),  # > 18h SLA -> stale
        _file(
            "app-models/aod/aod_pm25_v2.pkl",
            size=14_767_567,
            age_seconds=60,
            sha256="4c59657841ce03e4eebb503c8dd87d9a9bc8c178057bb3e27bbd4e97ef4eca58",
        ),
    ]


def test_manifest_conforms_to_schema(sample_entries):
    grouped, unclassified = pc.group_by_product(sample_entries)
    manifest = pc.build_manifest(grouped)
    jsonschema.validate(instance=manifest, schema=_schema("data-product-manifest.v1.schema.json"))
    assert unclassified == []


def test_source_registry_conforms_to_schema(sample_entries):
    grouped, _ = pc.group_by_product(sample_entries)
    manifest = pc.build_manifest(grouped)
    registry = pc.build_source_registry(manifest)
    jsonschema.validate(instance=registry, schema=_schema("source-registry.v1.schema.json"))


def test_product_health_conforms_to_schema(sample_entries):
    grouped, _ = pc.group_by_product(sample_entries)
    manifest = pc.build_manifest(grouped)
    health = pc.build_product_health(manifest)
    jsonschema.validate(instance=health, schema=_schema("product-health.v1.schema.json"))


def test_stale_artifact_is_flagged_stale_with_reason(sample_entries):
    """EVIDENCE_CONTRACT.md §7: stale artifact 주입 시 ProductHealth stale + reason."""
    grouped, _ = pc.group_by_product(sample_entries)
    manifest = pc.build_manifest(grouped)
    health = pc.build_product_health(manifest)
    by_id = {h["productId"]: h for h in health["products"]}
    assert by_id["active-fires"]["status"] == "stale"
    assert by_id["active-fires"]["reason"]
    assert by_id["active-fires"]["ageSeconds"] > by_id["active-fires"]["slaSeconds"]


def test_missing_product_reports_zero_files_not_fabricated(sample_entries):
    """카탈로그에는 있으나 이번 트리에 파일이 없는 product 는 missing + fileCount 0 이어야 한다."""
    grouped, _ = pc.group_by_product(sample_entries)
    manifest = pc.build_manifest(grouped)
    health = pc.build_product_health(manifest)
    by_id = {h["productId"]: h for h in health["products"]}
    news = by_id["news-data"]
    assert news["status"] == "missing"
    assert news["artifactReachable"] is False
    manifest_by_id = {p["productId"]: p for p in manifest["products"]}
    total_files = sum(part["fileCount"] for part in manifest_by_id["news-data"]["partitions"])
    assert total_files == 0


def test_unclassified_file_is_surfaced_not_silently_dropped():
    """카탈로그 밖 파일은 조용히 사라지지 않고 unclassified 로 나온다."""
    entries = [_file("some-new-bucket/unexpected.json")]
    grouped, unclassified = pc.group_by_product(entries)
    assert all(v == [] for v in grouped.values())
    assert len(unclassified) == 1
    assert unclassified[0].path == "some-new-bucket/unexpected.json"


def test_sha256_present_for_lfs_null_with_reason_otherwise(sample_entries):
    grouped, _ = pc.group_by_product(sample_entries)
    manifest = pc.build_manifest(grouped)
    by_id = {p["productId"]: p for p in manifest["products"]}
    aod_partitions = by_id["app-models-aod"]["partitions"]
    aod_pkl = next(p for p in aod_partitions if p["path"] == "app-models/aod/")
    assert aod_pkl["sha256"] == "4c59657841ce03e4eebb503c8dd87d9a9bc8c178057bb3e27bbd4e97ef4eca58"

    grid_partitions = by_id["aq-data-grids"]["partitions"]
    pm25 = next(p for p in grid_partitions if p["path"] == "aq-data/current-pm25-grid.json")
    assert pm25["sha256"] is None
    assert pm25["sha256Reason"]


def test_derive_source_status_reflects_worst_linked_product(sample_entries):
    grouped, _ = pc.group_by_product(sample_entries)
    manifest = pc.build_manifest(grouped)
    health = pc.build_product_health(manifest)
    registry = pc.build_source_registry(manifest)
    registry = pc.derive_source_status(registry, health)
    by_id = {s["sourceId"]: s for s in registry["sources"]}
    # active-fires(stale) 를 참조하는 nasa-firms 는 degraded 여야 한다.
    assert by_id["nasa-firms"]["status"] == "degraded"
    # aq-data-grids(ready, SLA 미계약이라도 partial) 를 참조하는 open-meteo-air-quality 는 active.
    assert by_id["open-meteo-air-quality"]["status"] == "active"


# ── EvidenceEnvelope schema: withheld/no-coverage requires reason (§7) ──


def _base_envelope(**overrides) -> dict:
    base = {
        "schemaVersion": "1.0",
        "receiptId": "test-receipt-1",
        "phenomenon": "pm2_5",
        "value": None,
        "unit": "µg/m³",
        "nature": "observation",
        "time": {
            "validStart": "2026-09-03T00:00:00+00:00",
            "ingestedAt": "2026-09-03T00:01:00+00:00",
            "publishedAt": "2026-09-03T00:02:00+00:00",
        },
        "space": {},
        "source": {
            "sourceId": "openaq",
            "provider": "OpenAQ",
            "licenseCode": "unverified",
            "attribution": "OpenAQ",
        },
        "quality": {"tier": "community", "status": "ready"},
        "lineage": {
            "datasetVersion": "v1",
            "transformations": [],
            "artifactSha256": "0" * 64,
        },
    }
    base.update(overrides)
    return base


def test_evidence_envelope_withheld_without_reason_is_rejected():
    schema = _schema("evidence-envelope.v1.schema.json")
    envelope = _base_envelope(quality={"tier": "community", "status": "withheld"})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=envelope, schema=schema)


def test_evidence_envelope_withheld_with_reason_is_accepted():
    schema = _schema("evidence-envelope.v1.schema.json")
    envelope = _base_envelope(
        quality={"tier": "community", "status": "withheld", "reason": "sensor offline > 24h"}
    )
    jsonschema.validate(instance=envelope, schema=schema)


def test_evidence_envelope_ready_status_does_not_require_reason():
    schema = _schema("evidence-envelope.v1.schema.json")
    envelope = _base_envelope()
    jsonschema.validate(instance=envelope, schema=schema)
