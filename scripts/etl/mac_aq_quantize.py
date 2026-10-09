#!/usr/bin/env python3
"""macOS 무료 글로벌 근실시간 대기질 파이프라인 — quantization(수치 절감) 계층.

**설계 SOT 는 "quantization" 또는 정밀도 자릿수를 명시하지 않는다** — 이 문서 침묵 영역에
지어내지 않고 이 모듈이 채택한 보수적 기본값을 명시한다: pollutant 소수점 1자리(µg/m3).
근거 — CAMS/GEFS 는 모델 격자값(계측기 실측이 아님)이라 원 정밀도 자체가 낮고, 저가
PM 센서의 실측 불확도도 통상 ±10~15%(수 µg/m3) 대이므로 소수점 둘째 자리는 계측 정밀도가
뒷받침하지 않는 허위 정밀도(false precision)다. 소수점 1자리면 표시값 변화 없이 JSON
직렬화 바이트만 줄어든다(예: "13.23"→"13.2").

**§5(ML/예측 불확실성) 가드 정합**: 이 모듈은 `pollutants[*].data`/`value` 숫자만 반올림
한다 — `quality`(등급/score)·`provenance`·`qaFlags`·`attribution` 등 신뢰도/출처 필드는
절대 건드리지 않는다(양자화로 불확실성 정보를 지우지 않는다).

`mac_aq_adapter.py` 는 수정하지 않는다 — 신규 계층.
"""
from __future__ import annotations

import json

DEFAULT_DECIMALS = 1  # 위 docstring 근거. 환경변수로 재정의 가능(MAC_QA_DECIMALS).


def quantize_value(value: float, decimals: int = DEFAULT_DECIMALS) -> float:
    """단일 수치 반올림. 정수/None 은 그대로(반올림할 소수부가 없음)."""
    if value is None or isinstance(value, bool):
        return value
    if not isinstance(value, (int, float)):
        return value
    return round(value, decimals)


def quantize_grid_pollutant_block(block: dict, decimals: int = DEFAULT_DECIMALS) -> dict:
    """grid pollutant block 의 `data` 배열만 반올림. `unit`/`sourceVariable`/`conversion`
    (provenance 소스)은 그대로 보존."""
    data = block.get("data") or []
    return {**block, "data": [quantize_value(v, decimals) for v in data]}


def quantize_grid_pollutants(pollutants: dict, decimals: int = DEFAULT_DECIMALS) -> dict:
    return {
        key: quantize_grid_pollutant_block(block, decimals)
        for key, block in pollutants.items()
    }


def quantize_point_pollutants(pollutants: dict, decimals: int = DEFAULT_DECIMALS) -> dict:
    """point reading pollutant dict — 각 `value` 만 반올림, 나머지 provenance 필드 보존."""
    out = {}
    for key, block in pollutants.items():
        if isinstance(block, dict) and "value" in block:
            out[key] = {**block, "value": quantize_value(block["value"], decimals)}
        else:
            out[key] = block
    return out


def quantize_reading(reading: dict, decimals: int = DEFAULT_DECIMALS) -> dict:
    """reading 전체(grid 또는 point) 를 감지해 pollutants 만 양자화. 보호 필드는 그대로."""
    pollutants = reading.get("pollutants") or {}
    if "grid" in reading:
        new_pollutants = quantize_grid_pollutants(pollutants, decimals)
    else:
        new_pollutants = quantize_point_pollutants(pollutants, decimals)
    return {**reading, "pollutants": new_pollutants}


def estimate_size_reduction(original: dict, quantized: dict) -> dict:
    """직렬화 바이트 비교(발행 script 관례와 동일한 compact separators 사용)."""
    original_bytes = len(json.dumps(original, separators=(",", ":")).encode("utf-8"))
    quantized_bytes = len(json.dumps(quantized, separators=(",", ":")).encode("utf-8"))
    saved = original_bytes - quantized_bytes
    ratio = (saved / original_bytes) if original_bytes else 0.0
    return {
        "originalBytes": original_bytes,
        "quantizedBytes": quantized_bytes,
        "savedBytes": saved,
        "savedRatio": round(ratio, 4),
    }
