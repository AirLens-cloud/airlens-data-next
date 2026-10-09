"""contracts/validate.py — 발행물 계약 검증기 (stdlib 전용).

**정본은 이 파일이다** (2026-10 조직 재편). 발행(`scripts/etl/hf_publish.py`)이
일어나는 레포에 계약을 둬야 검사되지 않는 계약이 생기지 않는다. 다른 레포가 쓰려면
이 레포의 태그를 기준으로 동기화한다 — 반대 방향으로 고치지 않는다.

버전 키는 생산자마다 뜻이 다르다. ML 산출물의 `schema_version` 은 계약 이름
(`grid_latest.v1`)이라 `--schema auto` 로 해석된다. 수집 산출물의 `schemaVersion` 은
`"1.0"` 같은 숫자 버전이라 계약 이름이 아니다 — 이 산출물들은 호출부가 계약 이름을
명시한다 (`--schema current-aq-grid.v1`). 그래서 `resolve_schema()` 는
`schema_version` 만 본다.

왜 직접 쓰는가: 검증은 *발행 직전*에 돌아야 하고, 그 지점은 CI 러너와
E2 사이드카처럼 의존성을 최소로 유지하는 환경이다. `jsonschema` 는 현재 이 레포의
선언 의존성이 아니고(로컬에만 우연히 존재, CI 의 `uv sync --frozen` 에는 없다),
계약 스키마가 쓰는 키워드는 좁은 부분집합이라 stdlib 로 충분하다.

**설계의 핵심은 미지원 키워드를 실패로 취급하는 것이다.** 검증기가 모르는 키워드를
조용히 건너뛰면 "스키마에 썼으니 지켜지겠지" 라는 착각이 생기고, 그 순간 초록불이
무검증을 위장한다. 그래서 스키마 로드 시점에 전체를 훑어 미지원 키워드가 하나라도
있으면 즉시 `ContractError` 로 죽는다 — 검증 실패보다 나쁜 것은 검증한 척이다.

지원 키워드: type / properties / required / items / enum / const /
additionalProperties(bool) / minimum / maximum / minItems / format("date-time" 만).
그 외(`anyOf`, `$ref`, `pattern` 등)는 필요해지는 시점에 이 파일에 구현하고 테스트를
추가한 뒤에 스키마에서 쓴다.

CLI:
    python3 contracts/validate.py <payload.json> [--schema auto|<path>]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

CONTRACTS_DIR = Path(__file__).resolve().parent

SUPPORTED_KEYWORDS = {
    # 구조
    "type", "properties", "required", "items", "additionalProperties",
    # 값 제약
    "enum", "const", "minimum", "maximum", "minItems", "format",
    # 주석 계열 — 검증에 쓰이지 않지만 문서로서 허용
    "$schema", "$id", "title", "description", "examples", "deprecated",
}

SUPPORTED_FORMATS = {"date-time"}

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


class ContractError(Exception):
    """계약 위반 또는 계약 자체의 결함. 둘 다 발행을 막아야 하므로 한 예외로 둔다."""


def _assert_supported(schema: Any, where: str = "$") -> None:
    """스키마 전체를 훑어 미지원 키워드를 찾는다 (검증 전에 1회)."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key not in SUPPORTED_KEYWORDS:
                raise ContractError(
                    f"{where}: 검증기가 모르는 키워드 '{key}' — 무검증을 통과로 두지 "
                    f"않는다. contracts/validate.py 에 구현 후 사용할 것."
                )
            if key == "format" and value not in SUPPORTED_FORMATS:
                raise ContractError(
                    f"{where}.format: 미지원 format '{value}' "
                    f"(지원: {sorted(SUPPORTED_FORMATS)})"
                )
            # additionalProperties 는 bool 만 해석한다. 스키마 객체를 주면
            # _validate 가 조용히 무시하므로(=무검증) 로드 시점에 막는다.
            if key == "additionalProperties" and not isinstance(value, bool):
                raise ContractError(
                    f"{where}.additionalProperties: bool 만 지원한다 "
                    f"(스키마 객체 형태는 미구현 — 조용히 통과시키지 않는다)"
                )
            if key == "properties" and isinstance(value, dict):
                for prop, sub in value.items():
                    _assert_supported(sub, f"{where}.properties.{prop}")
            elif key == "items":
                # tuple-form items(`"items": [스키마, ...]`)는 _validate 가 항목별
                # 검증을 건너뛴다 — 지원하는 척하지 말고 로드에서 죽는다.
                if not isinstance(value, dict):
                    raise ContractError(
                        f"{where}.items: 리스트(tuple validation) 형태는 미지원 — "
                        f"단일 스키마 객체만 쓴다"
                    )
                _assert_supported(value, f"{where}.items")


def _type_matches(value: Any, type_name: str) -> bool:
    expected = _TYPES.get(type_name)
    if expected is None:
        raise ContractError(f"알 수 없는 type '{type_name}'")
    # bool 은 int 의 서브클래스라 number/integer 로 새어 들어간다 — 명시적으로 차단.
    if type_name in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def _validate(value: Any, schema: dict, path: str) -> None:
    if "type" in schema:
        names = schema["type"]
        names = [names] if isinstance(names, str) else list(names)
        if not any(_type_matches(value, n) for n in names):
            raise ContractError(
                f"{path}: type 불일치 — 기대 {names}, 실제 "
                f"{type(value).__name__} ({value!r:.60})"
            )

    if "const" in schema and value != schema["const"]:
        raise ContractError(f"{path}: const {schema['const']!r} 기대, 실제 {value!r}")

    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: enum {schema['enum']} 밖의 값 {value!r}")

    if "format" in schema and isinstance(value, str):
        # date-time 만 지원 (_assert_supported 가 그 외를 이미 막았다).
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError(f"{path}: date-time 파싱 실패 — {value!r}") from exc

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractError(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractError(f"{path}: {value} > maximum {schema['maximum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ContractError(f"{path}: 필수 필드 '{key}' 누락")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(props))
            if extra:
                raise ContractError(f"{path}: 계약에 없는 필드 {extra}")
        for key, sub_schema in props.items():
            if key in value:
                _validate(value[key], sub_schema, f"{path}.{key}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ContractError(
                f"{path}: 항목 {len(value)}개 < minItems {schema['minItems']}"
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{i}]")


def load_schema(source: str | Path) -> dict:
    """스키마 파일을 읽고 미지원 키워드를 즉시 검사한다."""
    path = Path(source)
    if not path.exists():
        # `grid_latest.v1` 처럼 이름만 준 경우 contracts/ 안에서 찾는다.
        path = CONTRACTS_DIR / f"{source}.schema.json"
    if not path.exists():
        raise ContractError(f"스키마를 찾을 수 없다: {source}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    _assert_supported(schema, path.name)
    return schema


def resolve_schema(payload: dict) -> dict:
    """페이로드가 스스로 신고한 `schema_version`(계약 이름) 으로 계약을 고른다.

    자기 신고를 믿는 대신 *존재하는 계약 파일* 로만 해석한다 — 없는 버전을 신고하면
    통과가 아니라 실패다.
    """
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    if not version:
        raise ContractError(
            "payload 에 schema_version(계약 이름) 이 없다 — schemaVersion(숫자 버전)만 "
            "싣는 산출물은 --schema 에 계약 이름을 명시할 것."
        )
    # 페이로드가 경로를 신고해 자기 스키마를 고르는 일을 막는다 — 이름만 받고,
    # contracts/ 안에서만 찾는다.
    if not isinstance(version, str) or "/" in version or "\\" in version or version.startswith("."):
        raise ContractError(f"schema_version 은 계약 이름이어야 한다 (경로 불가): {version!r}")
    path = CONTRACTS_DIR / f"{version}.schema.json"
    if not path.exists():
        raise ContractError(f"계약이 없다: {version}")
    return load_schema(path)


def validate_payload(payload: Any, schema: dict) -> None:
    """위반 시 `ContractError`. 통과하면 조용히 반환."""
    _validate(payload, schema, "$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", help="검증할 JSON 파일")
    parser.add_argument(
        "--schema",
        default="auto",
        help="'auto'(payload 의 schema_version 으로 해석) 또는 스키마 경로/이름",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    try:
        # 스키마 해석 실패(신고 없음/없는 버전)도 검증 실패다 — 트레이스백이 아니라
        # 운영자가 읽는 한 줄로 떨어뜨린다.
        schema = (
            resolve_schema(payload) if args.schema == "auto" else load_schema(args.schema)
        )
        validate_payload(payload, schema)
    except ContractError as exc:
        print(f"❌ 계약 위반: {exc}", file=sys.stderr)
        return 1
    print(f"✅ 계약 통과: {args.payload} ({payload.get('schema_version')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
