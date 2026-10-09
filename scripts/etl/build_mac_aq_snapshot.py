#!/usr/bin/env python3
"""macOS 무료 글로벌 근실시간 대기질 파이프라인 — QA→provenance→quantize 조립 스크립트.

입력: `mac_aq_adapter.py` 계약을 따르는 producer 산출물 JSON **1개 파일**
  (`collect_cams_global.py` / `collect_gefs_chem_global.py` 의 grid_reading, 또는
  W5-b 지역 어댑터의 point_reading 1건 dict 혹은 다건 list).
출력: 같은 파일에 QA 리포트(quality 등급 조정) + `provenance` 블록을 추가하고 pollutant
수치를 양자화한 "최종 배포 snapshot" 1개.

**스코프 경계(문서 미지정 영역 — 지어내지 않음)**: 이 스크립트는 **단일 소스** 파일만
처리한다. CAMS 와 GEFS-Aerosols(또는 지역 관측소)를 하나의 `current.json` 으로 합치는
**cross-source cascade 병합**(설계 SOT: "관측 보정은 거리·신선도 threshold를 통과한 경우에만
적용" — 정확한 거리/신선도 숫자 미지정)은 여기서 구현하지 않는다. 그 병합은 별도 결정
(임계값 확정)이 필요한 후속 작업이다 — `build_mac_aq_tiles.py`(P0 파일 구조의 manifest/
geocell 타일 조립, 이 스크립트의 산출물을 입력으로 받음) 쪽에서 처리될 후보.

형제 producer 스크립트(`collect_cams_global.py` 등)와 동일한 정직성 원칙:
  - 파이프라인 자체 실패(입력 파일 없음/파싱 실패/스키마 위반) 시 exit 1, 기존
    last-good 출력 파일은 건드리지 않는다.
  - QA 이상치는 그 자체로 발행을 막지 않는다 — 등급 강등 + 리포트만(팀 지시 정합).
  - 성공한 결과만 임시 파일에 쓰고 `os.replace` 로 교체.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mac_aq_adapter as adapter  # noqa: E402
import mac_aq_provenance as provenance  # noqa: E402
import mac_aq_qa as qa  # noqa: E402
import mac_aq_quantize as quantize  # noqa: E402

INPUT_PATH = os.environ.get("MAC_QA_INPUT_PATH", "")
OUTPUT_PATH = os.environ.get("MAC_QA_OUTPUT_PATH", "")
DECIMALS = int(os.environ.get("MAC_QA_DECIMALS", str(quantize.DEFAULT_DECIMALS)))
MIN_STATIONS = int(os.environ.get("MAC_QA_MIN_STATIONS", "1"))
# publisher-side invariant(PR7): validAt 이 generatedAt 보다 이 허용치를 넘겨 미래면 드롭한다.
# mac_aq_qa.py 의 freshness 체크는 등급 강등만 하고 발행을 막지 않는다(팀 정책) — "미래
# validAt" 은 등급 문제가 아니라 데이터 정합성 문제라 여기서 별도로 드롭한다. 값은 5분 —
# 같은 CI job 안 producer→publisher 사이 시계 오차를 흡수하는 보수적 여유(설계 SOT 미지정,
# 이 스크립트가 채택한 기본값).
VALID_AT_CLOCK_SKEW_TOLERANCE_MINUTES = float(
    os.environ.get("MAC_QA_VALID_AT_TOLERANCE_MINUTES", "5")
)


def _is_grid_reading(record: dict) -> bool:
    return "grid" in record


def _parse_envelope_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_valid_at_future(
    record: dict, tolerance_minutes: float = VALID_AT_CLOCK_SKEW_TOLERANCE_MINUTES,
) -> bool:
    """`validAt` 이 자신의 `generatedAt` 보다 `tolerance_minutes` 를 넘겨 미래인가.

    Publisher-side invariant(PR7) — `mac_aq_qa.py` 의 freshness 체크는 F 등급 강등만
    하고 발행을 막지 않는다(기존 정책, 팀 지시 정합). "미래에 관측된 값"은 등급을 낮춰
    보존할 값이 아니라 정합성 위반이므로 여기서 드롭 대상으로 판정한다. 필드가 없으면
    False(스키마 결손은 `adapter.validate_*` 가 별도로 잡는다) — pure, 테스트 대상.
    """
    valid_dt = _parse_envelope_dt(record.get("validAt"))
    generated_dt = _parse_envelope_dt(record.get("generatedAt"))
    if valid_dt is None or generated_dt is None:
        return False
    return valid_dt > generated_dt + timedelta(minutes=tolerance_minutes)


def drop_future_valid_at(readings: list[dict]) -> tuple[list[dict], int]:
    """point reading 리스트에서 `validAt` 이 미래인 레코드만 드롭. (kept, dropped_count) 반환.

    관측소 단위 격리(`collect_mac_eea_utd.assemble_country_readings` 의 per-station 격리
    원칙과 정합) — 한 관측소가 드롭되어도 나머지는 그대로 발행한다. drop 은 stderr 에
    로그로 남긴다(silent drop 금지).
    """
    kept: list[dict] = []
    dropped = 0
    for r in readings:
        if is_valid_at_future(r):
            print(
                f"  WARN dropped (validAt {r.get('validAt')!r} is after generatedAt "
                f"{r.get('generatedAt')!r} + {VALID_AT_CLOCK_SKEW_TOLERANCE_MINUTES:.0f}min "
                f"tolerance): sourceVersion={r.get('sourceVersion')!r}",
                file=sys.stderr,
            )
            dropped += 1
        else:
            kept.append(r)
    return kept, dropped


def process_grid_reading(reading: dict, now: datetime) -> tuple[dict, dict]:
    """단일 grid_reading → (final_record, summary). 실패 시 raise(호출자가 exit 1)."""
    if is_valid_at_future(reading):
        raise ValueError(
            f"grid reading dropped — validAt {reading.get('validAt')!r} is after generatedAt "
            f"{reading.get('generatedAt')!r} (+{VALID_AT_CLOCK_SKEW_TOLERANCE_MINUTES:.0f}min "
            "tolerance); last-good kept"
        )
    qa_updated, qa_report = qa.qa_grid_reading(reading, now)
    prov = provenance.build_provenance(reading, reading.get("pollutants") or {}, qa_report, processed_at=now)
    quantized = quantize.quantize_reading(qa_updated, DECIMALS)
    final_record = {**quantized, "provenance": prov}

    errors = adapter.validate_grid_reading(final_record)
    if errors:
        raise ValueError(f"post-processing broke grid schema: {errors}")

    size_report = quantize.estimate_size_reduction(reading, quantized)
    summary = {"qa": qa_report, "quantization": size_report}
    return final_record, summary


def process_point_reading_list(readings: list[dict], now: datetime) -> tuple[list[dict], dict]:
    """point_reading 리스트 → (final_records, summary). 실패 시 raise."""
    readings, dropped_future_count = drop_future_valid_at(readings)
    if dropped_future_count and not readings:
        # 전량이 미래 validAt 이면 발행할 게 없다 — 빈 배열로 last-good 을 덮어쓰지 않고
        # 다른 파이프라인 실패와 동일하게 exit 1(호출자가 last-good 유지).
        raise ValueError(
            f"all {dropped_future_count} reading(s) dropped — validAt after generatedAt "
            f"(+{VALID_AT_CLOCK_SKEW_TOLERANCE_MINUTES:.0f}min tolerance); last-good kept"
        )

    qa_updated, qa_report = qa.qa_point_reading_list(readings, now, MIN_STATIONS)
    qa_report["droppedFutureValidAtCount"] = dropped_future_count

    final_records = [quantize.quantize_reading(updated, DECIMALS) for updated in qa_updated]

    # 레코드마다 **자기 envelope** 으로 provenance 를 만든다. 리스트 전체의 source 목록을
    # 공유하면 관측소마다 sourceVersion 이 다른 소스(EEA)에서 크기가 N² 로 자란다 —
    # `mac_aq_provenance.build_point_provenance` docstring 의 1GB 실측 참조.
    # QA 는 additive 라 readings[i] ↔ qa_updated[i] ↔ final_records[i] 가 1:1 로 대응한다.
    reading_count = len(final_records)
    for i, (record, envelope) in enumerate(zip(final_records, readings)):
        record["provenance"] = provenance.build_point_provenance(
            envelope, reading_count=reading_count, reading_index=i, processed_at=now
        )

    for record in final_records:
        errors = adapter.validate_point_reading(record)
        if errors:
            raise ValueError(f"post-processing broke point schema: {errors}")

    size_report = quantize.estimate_size_reduction(readings, [
        quantize.quantize_reading(r, DECIMALS) for r in qa_updated
    ])
    summary = {"qa": qa_report, "quantization": size_report}
    return final_records, summary


def process(record: dict | list[dict], now: datetime) -> tuple[dict | list[dict], dict]:
    """입력 shape(단일 point dict / point list / grid dict) 자동 감지 후 처리."""
    if isinstance(record, list):
        return process_point_reading_list(record, now)
    if isinstance(record, dict) and _is_grid_reading(record):
        return process_grid_reading(record, now)
    if isinstance(record, dict):
        final_list, summary = process_point_reading_list([record], now)
        return final_list[0], summary
    raise ValueError(f"unrecognized input shape: {type(record)}")


def main() -> int:
    if not INPUT_PATH or not OUTPUT_PATH:
        print("ERROR: MAC_QA_INPUT_PATH and MAC_QA_OUTPUT_PATH must both be set", file=sys.stderr)
        return 1

    try:
        with open(INPUT_PATH) as f:
            record = json.load(f)
        now = datetime.now(timezone.utc)
        final_record, summary = process(record, now)
    except Exception as e:  # noqa: BLE001 — outage/오류는 fail-loud, last-good 은 안 건드림
        print(f"ERROR: mac AQ QA/provenance/quantize pipeline failed — {e}", file=sys.stderr)
        print("  기존 snapshot(있다면) 은 그대로 유지한다.", file=sys.stderr)
        return 1

    tmp_path = f"{OUTPUT_PATH}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(final_record, f, separators=(",", ":"))
    os.replace(tmp_path, OUTPUT_PATH)

    qa_report = summary["qa"]
    size_report = summary["quantization"]
    print(
        f"Done: {INPUT_PATH} -> {OUTPUT_PATH} "
        f"(qa.kind={qa_report.get('kind')}, "
        f"savedBytes={size_report['savedBytes']} ({size_report['savedRatio']:.1%}))"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
