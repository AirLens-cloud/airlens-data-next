"""web/v1 격자 계약 게이트 (AAA). 네트워크 0 — 생산자를 실제로 돌려 그 산출물을 검증한다.

**왜 별 계약인가.** `mac-data/data/web/v1/current-pm25-grid.json` 은 `aq-data/` 쪽과
**파일명이 같지만 다른 제품**이다 — 저쪽은 `collect_noaa_aq.py` 의 조밀 격자로
`schemaVersion`/`generatedAt`/`distribution`/EPA 공시를 싣고, 이쪽은 mac 스냅샷을
다운샘플한 희소 격자라 그 필드가 아예 없다. 그래서 aq-data 만 `current-aq-grid.v1` 로
막혀 있었고 **이 경로는 무검증으로 발행돼 왔다**. 남의 계약을 씌우면 정상 발행이 죽으므로
`web-aq-grid.v1` 을 따로 뒀다 (같은 이름이 다른 quantity 를 가리는 함정).

여기서 검사하는 것 3가지:
1. 생산자 산출물이 계약을 만족한다 — 픽스처가 아니라 `convert_pollutant()` 실제 출력으로.
   계약을 손으로 쓴 JSON 에만 맞추면 생산자와 조용히 갈라진다.
2. 계약이 **비어 있지 않다** — 필드를 빼거나 음수를 넣으면 실제로 죽는지(vacuous 회피).
3. 워크플로가 그 게이트를 실제로 부른다 — 계약 파일만 있고 아무도 안 부르면 게이트가 아니다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import build_web_aq_grid as bw
import hf_publish as hp

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = REPO_ROOT / "contracts/web-aq-grid.v1.schema.json"
WORKFLOW = REPO_ROOT / ".github/workflows/mac-data-publish.yml"

_GRID = {"nx": 4, "ny": 3, "la1": 10.0, "lo1": 0.0, "dx": 5.0, "dy": 5.0}
_DATA = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]


def _reading() -> dict:
    return {
        "schemaVersion": 1,
        "kind": "analysis",
        "source": "NOAA GEFS-Aerosols",
        "generatedAt": "2026-08-19T09:00:00Z",
        "grid": dict(_GRID),
        "pollutants": {
            "pm25": {"unit": "ug/m3", "sourceVariable": "PMTF", "conversion": "x", "data": list(_DATA)},
        },
    }


def _write(dir_path: Path, name: str, doc: dict) -> None:
    (dir_path / name).write_text(json.dumps(doc), encoding="utf-8")


# ───────────────────── 1. 생산자 ↔ 계약 ─────────────────────

def test_producer_output_satisfies_the_contract(tmp_path):
    # Arrange — 손으로 쓴 문서가 아니라 생산자를 실제로 돌린 결과를 검증한다.
    doc = bw.convert_pollutant(_reading(), "pm25", 5.0)
    _write(tmp_path, "current-pm25-grid.json", doc)

    # Act / Assert — 위반이면 check_contract 가 SystemExit 로 죽는다
    hp.check_contract(str(tmp_path), str(SCHEMA), "current-*-grid.json")


# ───────────────────── 2. 계약이 비어 있지 않다 ─────────────────────

@pytest.mark.parametrize(
    "mutate, why",
    [
        (lambda d: d.pop("nNegativeCellsDropped"), "공시 필드가 사라지면 '0건'과 '안 셌다'가 구별 안 된다"),
        (lambda d: d.pop("variable"), "무슨 오염물질인지 모르는 격자"),
        (lambda d: d.pop("nLat"), "격자 모양을 모르면 소비자가 재구성할 수 없다"),
        (lambda d: d["points"].append({"lat": 0.0, "lon": 0.0, "value": -1.0}), "음수 농도"),
        (lambda d: d.update(surprise=1), "선언 안 된 필드(additionalProperties)"),
    ],
)
def test_contract_rejects_a_broken_document(tmp_path, mutate, why):
    # Arrange — 생산자 정상 출력에서 한 군데만 망가뜨린다.
    doc = bw.convert_pollutant(_reading(), "pm25", 5.0)
    mutate(doc)
    _write(tmp_path, "current-pm25-grid.json", doc)

    # Act / Assert — 통과하면 계약이 그 필드에 대해 아무 말도 안 하고 있다는 뜻
    with pytest.raises(SystemExit):
        hp.check_contract(str(tmp_path), str(SCHEMA), "current-*-grid.json")


def test_zero_is_a_reading_and_stays_valid(tmp_path):
    # Arrange — 하한은 음수만 막는다. 0 은 실제 측정값이라 막으면 안 된다.
    doc = bw.convert_pollutant(_reading(), "pm25", 5.0)
    doc["points"].append({"lat": 0.0, "lon": 0.0, "value": 0.0})
    _write(tmp_path, "current-pm25-grid.json", doc)

    # Act / Assert
    hp.check_contract(str(tmp_path), str(SCHEMA), "current-*-grid.json")


# ───────────────────── 3. 글롭 좁힘의 안전성 ─────────────────────

def test_health_json_is_skipped_by_name_not_silently(tmp_path, capsys):
    # Arrange — 이 디렉터리는 격자 2개 + health.json 을 함께 담는다(다른 제품).
    _write(tmp_path, "current-pm25-grid.json", bw.convert_pollutant(_reading(), "pm25", 5.0))
    _write(tmp_path, "health.json", {"sources": {}})

    # Act
    hp.check_contract(str(tmp_path), str(SCHEMA), "current-*-grid.json")

    # Assert — 건너뛴 파일이 이름으로 찍혀야 한다. 부분 검증을 전체 검증으로 위장하지 않는다.
    out = capsys.readouterr().out
    assert "health.json" in out and "skip" in out
    assert "contract ok: 1 file(s)" in out


def test_a_glob_that_matches_nothing_fails_instead_of_passing(tmp_path):
    # Arrange — 오타 난 글롭은 "위반 0건"이 아니라 "검증이 안 돌았다"이다.
    _write(tmp_path, "current-pm25-grid.json", bw.convert_pollutant(_reading(), "pm25", 5.0))

    # Act / Assert
    with pytest.raises(SystemExit):
        hp.check_contract(str(tmp_path), str(SCHEMA), "typo-*.json")


def test_default_glob_still_validates_every_json(tmp_path):
    # Arrange — 좁힘은 opt-in 이다. 기본값이 조용히 넓어지면 다른 호출부가 무검증이 된다.
    _write(tmp_path, "current-pm25-grid.json", bw.convert_pollutant(_reading(), "pm25", 5.0))
    _write(tmp_path, "health.json", {"sources": {}})

    # Act / Assert — health.json 이 계약에 안 맞으므로 기본 글롭에서는 죽어야 한다
    with pytest.raises(SystemExit):
        hp.check_contract(str(tmp_path), str(SCHEMA))


# ───────────────────── 4. 워크플로가 실제로 부른다 ─────────────────────

def test_workflow_gates_the_web_v1_upload_with_this_contract():
    # Arrange — 계약 파일만 있고 아무도 안 부르면 게이트가 아니다.
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"--src site/data/web/v1[^\n]*\n(?:\s*[^\n]*\\\n)*\s*[^\n]*", text)
    assert m, "web/v1 업로드 호출을 못 찾았다 — 파싱이 깨졌다(vacuous pass 방지)"
    block = m.group(0)

    # Assert
    # 이름 형태로 넘긴다 — `load_schema` 가 contracts/ 에서 해석하므로 cwd 에 의존하지 않는다
    # (워크플로에서 상대경로는 조용히 죽는 함정이 있다).
    assert "--schema web-aq-grid.v1" in block, "web/v1 업로드가 계약 없이 발행된다"
    assert "--schema-targets" in block, (
        "글롭 없이 이 계약을 걸면 health.json 이 위반으로 잡혀 정상 발행이 죽는다"
    )


def test_the_two_same_named_products_do_not_share_a_contract():
    # Arrange — 같은 파일명이라 한쪽 계약을 다른 쪽에 씌우기 쉽다. 실제로 씌우면 죽는다.
    # 검사 대상은 실제로 넘기는 `--schema` 인자다(주석에 이름이 나오는 건 무해하다).
    text = WORKFLOW.read_text(encoding="utf-8")
    passed = re.findall(r"--schema\s+(\S+)", text)

    # Assert
    assert passed, "이 워크플로가 계약을 하나도 안 건다 — 게이트 부재"
    assert not [s for s in passed if "current-aq-grid.v1" in s], (
        f"aq-data 쪽 계약이 mac 워크플로에 새어 들어왔다 — 모양이 다르므로 정상 발행이 죽는다: {passed}"
    )
    assert SCHEMA.exists(), f"{SCHEMA.name} 이 없다"
