"""`.github/workflows/*.yml` 이 파싱 가능한지 지키는 게이트 (AAA).

**왜 이 파일이 있나 (2026-09-05 실측 사고).** `openaq-bulk-collect.yml` 의 Summary
스텝이 `python3 -c "` 뒤 파이썬 본문을 **컬럼 0** 에서 시작했다. 컬럼 0 은 `run: |`
블록 스칼라의 종료 신호라, YAML 파서가 `import json` 을 새 매핑 키로 읽고 파일
전체가 파싱 불가가 됐다. GitHub 은 워크플로 이름조차 못 뽑아 경로로 표시했고
(`.github/workflows/openaq-bulk-collect.yml`), push 마다 스텝 0개짜리
startup_failure 를 적재했다 — `on: workflow_dispatch` 만 선언한 워크플로가 push
이벤트로 실패하는 것이 이 상태의 지문이다.

그런데 그 PR 의 `PR Check` 는 **green 이었다**. 이 레포의 PR 게이트는
`pytest scripts` 단일 잡이라 워크플로 YAML 을 아무도 안 봤기 때문이다(모노레포엔
`CI Workflow Lint` 가 있지만 여기엔 없다). 즉 워크플로는 "머지되고 나서 실행될 때"
처음 검증되는 유일한 표면이었다. 이 파일이 그 갭을 닫는다 — 새 액션·새 러너 없이
기존 pytest 잡 안에서 돈다.

검사 대상은 **GitHub 이 실제로 추출하는 필드**로 한정한다(name / 트리거 / jobs) —
전체 스키마 린트가 아니다. 이번에 깨진 지점이 정확히 거기다.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github/workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))

# PyYAML 은 YAML 1.1 이라 bare `on` 을 boolean True 로 읽는다(GitHub 의 1.2 파서는
# 문자열 "on"). 트리거 존재 확인은 두 형태를 모두 받아야 한다.
TRIGGER_KEYS = ("on", True)


def test_workflow_glob_is_not_empty():
    # Arrange/Act/Assert — 글롭이 0건이면 아래 parametrize 가 통째로 무음 통과한다.
    assert len(WORKFLOWS) >= 10, f"워크플로를 {len(WORKFLOWS)}개만 찾았다 — 글롭 경로 확인"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_parses_and_has_fields_github_reads(path: Path):
    # Arrange
    text = path.read_text(encoding="utf-8")

    # Act — 파싱 자체가 이 게이트의 본체 (블록 스칼라 파손이 여기서 잡힌다)
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:  # noqa: BLE001
        pytest.fail(f"{path.name} 파싱 실패 — GitHub 도 못 읽어 startup_failure 가 된다:\n{e}")

    # Assert — GitHub 이 뽑는 3개 필드. name 이 없으면 UI 가 경로를 이름으로 쓴다.
    assert isinstance(doc, dict), f"{path.name}: 최상위가 매핑이 아니다"
    assert isinstance(doc.get("name"), str) and doc["name"].strip(), \
        f"{path.name}: 최상위 name 부재/공백 — 워크플로 목록에 경로로 표시된다"
    assert any(k in doc for k in TRIGGER_KEYS), f"{path.name}: 트리거(on) 부재"
    assert isinstance(doc.get("jobs"), dict) and doc["jobs"], f"{path.name}: jobs 부재/빈 값"
