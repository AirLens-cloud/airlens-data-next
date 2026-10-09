"""직전 발행분의 아직 만료되지 않은 관측소를 이번 run 결과에 이어붙인다 (carry-over merge).

왜 필요한가 — EEA 수집기가 국가 순회를 UTC 시로 회전(`collect_mac_eea_utd.rotate_countries`)
하면서 생긴 요구. 회전 전에는 예산이 앞쪽 국가에서 소진돼 ES/PL/NL 이 *영구히* 빠졌고,
회전 후에는 대신 "이번 시각에 안 돈 국가"가 매시간 달라진다. 발행 단계는 소스 파일을
통째로 덮어쓰므로, 회전만 넣으면 국가들이 시간마다 나타났다 사라진다 — workflow 가
파일 단위로 지키던 "한 번의 실패가 소스를 지우게 두지 않는다" 불변식이 *국가 단위*에서
깨진다. 이 스크립트가 그 불변식을 관측소 단위로 복원한다.

정직성 규칙 (형제 스크립트와 동일 원칙 — 없는 데이터를 만들지 않는다):
  - 이어붙이는 값은 직전에 *실제로 발행된* 관측값 그대로다. 재추정·보간 없음.
  - 만료된(`expiresAt <= now`) 관측소는 이어붙이지 않는다 — 조용히 되살리지 않는다.
  - `qaFlags.ageHours` / `qaFlags.expired` 는 **지금 시각 기준으로 다시 계산**한다.
    직전 run 이 기록한 나이를 그대로 두면 낡은 값이 신선한 척한다.
  - 이어붙인 레코드에는 `qaFlags.carriedOver = true` 를 단다 — 이번 run 의 관측이
    아니라는 사실 자체가 소비자에게 보여야 한다.
  - `provenance.sources` 는 그 레코드 **자기 자신 1건**으로 다시 세운다 (`normalize_provenance`).
    과거 발행물을 그대로 실어 오면 과거의 결함도 함께 실어 오기 때문이다 — 나이를 다시
    찍는 것과 같은 원칙. 값·귀속·시각은 건드리지 않는다.
  - `quality` 는 손대지 않는다. `mac_aq_adapter.estimate_quality` 의 freshness 는
    `FRESH_QUALITY_BAND_HOURS` 이하에서 나이와 무관하게 1.0 이고, 이어붙이는 창이
    그 값과 같으므로(아래 MAX_CARRY_OVER_AGE_HOURS) 등급이 낙관적으로 남을 여지가
    없다 — 리터럴을 복제하지 않고 어댑터 상수를 그대로 가져와 두 값이 따로 놀 수
    없게 묶었다. 그 창을 넘는 레코드는 아예 이어붙이지 않는다.

장애 격리 (형제 스크립트와 동일): baseline 은 라이브 사이트에서 받아온 과거 발행물이라
스키마를 신뢰할 수 없다. 레코드 하나가 깨져 있어도 그 레코드만 skip 하고 나머지는
계속 처리한다 — 여기서 예외가 새면 발행 단계 전체가 죽어 EEA 뿐 아니라 다른 소스의
이번 시각 발행까지 통째로 사라진다(원래 고치려던 문제보다 넓은 blast radius).

입출력 (env):
  MAC_MERGE_FRESH_PATH     — 이번 run 의 QA 완료 파일. 없으면 이어붙일 기준이 없으니 no-op.
  MAC_MERGE_BASELINE_PATH  — 직전 발행분(last-good). 없으면 fresh 를 그대로 통과시킨다.
  MAC_MERGE_OUTPUT_PATH    — 병합 결과. 미지정 시 fresh 경로에 덮어쓴다.

point reading 리스트(JSON 배열)에만 적용된다 — grid 스냅샷(단일 dict)은 관측소 단위
합집합이라는 개념 자체가 없으므로 건드리지 않고 그대로 통과시킨다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mac_aq_adapter as adapter  # noqa: E402
import mac_aq_provenance as provenance  # noqa: E402
import mac_aq_qa as qa  # noqa: E402

# quality 등급을 다시 계산하지 않아도 되는 상한. 어댑터의 flat freshness 밴드를 그대로
# 가져온다 — 리터럴을 복제하면 밴드가 좁아졌을 때 이 파일만 옛 값에 남아 낙관적 등급이
# 조용히 새어 나간다. 이보다 오래된 레코드는 등급 재계산 로직을 복제하는 대신 버린다.
MAX_CARRY_OVER_AGE_HOURS = float(adapter.FRESH_QUALITY_BAND_HOURS)

FRESH_PATH = os.environ.get("MAC_MERGE_FRESH_PATH", "")
BASELINE_PATH = os.environ.get("MAC_MERGE_BASELINE_PATH", "")
OUTPUT_PATH = os.environ.get("MAC_MERGE_OUTPUT_PATH", "") or FRESH_PATH


def reading_key(reading: dict) -> str | None:
    """관측소 동일성 키. `sourceVersion` 은 소스가 관측소마다 유일하게 찍는 값
    (EEA: `E2a-UTD-<EoI code>`). 없으면 좌표로 떨어진다 — 둘 다 없으면 동일성을
    주장할 수 없으므로 None(이어붙이지 않음).
    """
    source_version = reading.get("sourceVersion")
    if isinstance(source_version, str) and source_version:
        return source_version
    lat, lon = reading.get("lat"), reading.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return f"@{round(float(lat), 4)},{round(float(lon), 4)}"
    return None


def normalize_provenance(reading: dict) -> dict | None:
    """`provenance.sources` 를 이 레코드 **자기 자신 1건**으로 다시 세운다. 변경 없으면 None.

    carry-over 는 과거 발행물을 그대로 실어 오므로 과거의 *결함*도 함께 실어 온다.
    2026-07-30 발행분은 관측소 1,453개가 각자 이웃 1,452개의 출처를 싣고 있었고
    (레코드당 482KB, 파일 약 1GB — 원인·수정은 `mac_aq_provenance.build_point_provenance`),
    생성 쪽을 고친 뒤에도 carry-over 가 만료 전까지 옛 레코드를 계속 재발행했다
    (수정 직후 발행분 223,969,909 B 의 대부분).

    나이를 다시 찍는 것과 같은 원칙이다 — 과거 run 이 기록한 값을 그대로 두면 틀린 채로
    신선한 척한다. `sources[0]` 을 쓰지 않는 이유: 공유 목록의 첫 항목은 그 레코드가 아니라
    리스트 맨 앞 관측소다(꼬리 레코드에서 실측 확인). 레코드 자신의 envelope 필드에서
    다시 만들어야 자기 출처가 된다. 값·귀속·시각 무변조 — 남의 출처를 떼내는 것뿐이다.
    """
    prov = reading.get("provenance")
    if not isinstance(prov, dict):
        return None
    sources = prov.get("sources")
    if not isinstance(sources, list) or len(sources) <= 1:
        return None
    return {**prov, "sources": [provenance.build_source_provenance(reading)]}


def restamp_freshness(reading: dict, now: datetime) -> dict:
    """`qaFlags` 의 나이·만료를 지금 시각 기준으로 다시 찍고 carry-over 표식을 단다."""
    freshness = qa.check_timestamp_freshness(reading, now)
    flags = {
        **(reading.get("qaFlags") or {}),
        "expired": freshness["isExpired"],
        "ageHours": freshness["ageHours"],
        "carriedOver": True,
    }
    restamped = {**reading, "qaFlags": flags}
    normalized = normalize_provenance(reading)
    if normalized is not None:
        restamped["provenance"] = normalized
    return restamped


def merge(fresh: list[dict], baseline: list[dict], now: datetime) -> tuple[list[dict], dict]:
    """fresh 우선 합집합. 반환 = (merged, report)."""
    fresh_keys = {k for k in (reading_key(r) for r in fresh) if k is not None}

    carried: list[dict] = []
    dropped_expired = 0
    dropped_too_old = 0
    dropped_unkeyed = 0
    dropped_malformed = 0
    for record in baseline:
        # 레코드 단위 격리 — 깨진 타임스탬프/비-dict 원소 하나가 발행 전체를 죽이지
        # 못하게 한다. 판단 근거가 깨졌으면 이어붙이지 않는 쪽(안전)으로 떨어진다.
        try:
            key = reading_key(record)
            if key is None:
                dropped_unkeyed += 1
                continue
            if key in fresh_keys:
                continue  # 이번 run 이 더 새 값을 냈다
            freshness = qa.check_timestamp_freshness(record, now)
            if freshness["isExpired"]:
                dropped_expired += 1
                continue
            age_hours = freshness["ageHours"]
            # `validAt` 부재 → 나이를 알 수 없다. 모르는 나이를 "충분히 신선"으로 간주
            # 하지 않는다(정직성) — 이어붙이지 않고 too-old 로 센다.
            #
            # EEA 실데이터에서 이 상한(6h)은 `expiresAt = observedAt + 4h` 보다 뒤라
            # 나이 초과는 항상 만료가 먼저 잡는다. 즉 이 분기는 EEA 한정으로는 도달
            # 불가능한 방어선이고, 다른 프로듀서·스키마 변화용으로 남겨 둔다.
            if age_hours is None or age_hours > MAX_CARRY_OVER_AGE_HOURS:
                dropped_too_old += 1
                continue
            carried.append(restamp_freshness(record, now))
        except (AttributeError, TypeError, ValueError) as exc:
            dropped_malformed += 1
            print(f"  WARN: baseline record skipped ({type(exc).__name__}: {exc})",
                  file=sys.stderr)

    report = {
        "freshCount": len(fresh),
        "baselineCount": len(baseline),
        "carriedOverCount": len(carried),
        "droppedExpired": dropped_expired,
        "droppedTooOld": dropped_too_old,
        "droppedUnkeyed": dropped_unkeyed,
        "droppedMalformed": dropped_malformed,
    }
    return fresh + carried, report


def _load(path: str) -> list[dict] | dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARN: {path} unreadable ({exc}) — treated as absent", file=sys.stderr)
        return None


def main() -> int:
    if not FRESH_PATH:
        print("ERROR: MAC_MERGE_FRESH_PATH must be set", file=sys.stderr)
        return 1

    fresh = _load(FRESH_PATH)
    if fresh is None:
        # 이번 run 이 아무것도 못 냈다 — 발행 단계의 파일 단위 last-good 폴백이
        # 그대로 담당한다. 여기서 baseline 을 "새 발행분"으로 승격시키면 낡은
        # 스냅샷에 새 발행 시각이 찍히므로 하지 않는다.
        print("  merge: no fresh output this run — leaving last-good untouched")
        return 0
    if not isinstance(fresh, list):
        print("  merge: fresh output is not a point-reading list — passthrough")
        return 0

    baseline = _load(BASELINE_PATH)
    if not isinstance(baseline, list):
        merged, report = fresh, {"freshCount": len(fresh), "baselineCount": 0, "carriedOverCount": 0}
    else:
        try:
            merged, report = merge(fresh, baseline, datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 — 마지막 그물
            # 레코드 단위 격리를 뚫은 예기치 못한 실패. 이어붙이기를 포기할 뿐,
            # 이번 run 의 fresh 결과와 다른 소스들의 발행까지 끌고 내려가지 않는다.
            print(f"WARN: carry-over merge failed ({type(exc).__name__}: {exc}) — "
                  "publishing this run's fresh output only", file=sys.stderr)
            return 0

    tmp_path = f"{OUTPUT_PATH}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(merged, f, separators=(",", ":"))
    os.replace(tmp_path, OUTPUT_PATH)
    print(f"  merge: {json.dumps(report, separators=(',', ':'))} -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
