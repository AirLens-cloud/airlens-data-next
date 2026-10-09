"""PR 게이트와 실 발행 워크플로의 의존성 핀이 갈리지 않게 고정 (AAA).

원본은 모노레포(`AirLens`)에 있었고, 그 레포의 `pr-check.yml` 게이트와
`mac-data-publish.yml` 을 비교했다. 2026-08-26 레포 분할로 수집·발행이 이쪽으로
넘어오면서 비교 대상 둘이 모두 이 레포에 있게 됐다 — 저쪽에 남겨두면 존재하지
않는 워크플로를 보는 테스트가 된다. 그래서 옮기면서 짝을 현실에 맞췄다:

- 게이트(`pr-check.yml`)는 `-r requirements.txt` 로 설치한다.
- 발행(`mac-data-publish.yml`)은 인라인 `pip install name==version` 으로 설치한다.

따라서 비교는 **requirements.txt ↔ 발행 워크플로 인라인 핀** 이다. 이 동기화는
이미 requirements.txt 주석에 "워크플로 인라인 pip install 과 동기 유지" 라고
적혀 있었지만 강제되지 않았다 — 주석으로 적어두는 방식은 이미 실패한 적이 있다
(EEA 예산이 코드 480 / 워크플로 600 으로 갈린 채 방치). 그래서 테스트로 고정한다.

핀 목록을 이 파일에 복사하지 않는다 — 복사하면 새 패키지가 추가돼도 테스트가
모른다. 파일을 직접 읽어 비교한다.

`>=`/`<` 범위 지정(huggingface_hub)은 `==` 정규식이 잡지 않는다. 원본과 같은
한계이며, 범위는 애초에 두 곳이 갈려도 같은 해석으로 수렴할 수 있어 강제 대상이
아니다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_REQUIREMENTS = REPO_ROOT / "requirements.txt"
PUBLISH_WORKFLOW = REPO_ROOT / ".github/workflows/mac-data-publish.yml"

_PIN_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][^\s'\"]*)")


def _workflow_pins(workflow: Path) -> dict[str, set[str]]:
    """워크플로의 모든 `pip install` 줄에서 `name==version` 을 걷어 온다."""
    pins: dict[str, set[str]] = {}
    for line in workflow.read_text(encoding="utf-8").splitlines():
        if "pip install" not in line:
            continue
        for name, version in _PIN_RE.findall(line):
            pins.setdefault(name.lower(), set()).add(version)
    return pins


def _requirements_pins(path: Path) -> dict[str, set[str]]:
    """requirements.txt 의 `name==version` 을 걷어 온다 (주석 줄 제외)."""
    pins: dict[str, set[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        for name, version in _PIN_RE.findall(line):
            pins.setdefault(name.lower(), set()).add(version)
    return pins


def test_source_files_exist():
    # Arrange / Act / Assert — 경로가 바뀌면 아래 비교가 조용히 vacuous PASS 가 된다.
    assert GATE_REQUIREMENTS.is_file(), GATE_REQUIREMENTS
    assert PUBLISH_WORKFLOW.is_file(), PUBLISH_WORKFLOW


def test_publish_workflow_pins_are_discoverable():
    # Arrange / Act
    publish = _workflow_pins(PUBLISH_WORKFLOW)
    # Assert — 파서가 아무것도 못 찾으면 아래 parity 테스트가 무의미해진다.
    assert publish, f"{PUBLISH_WORKFLOW.name} 에서 pip 핀을 하나도 못 찾았다 — 파서 확인"


def test_requirements_pins_are_discoverable():
    # Arrange / Act
    gate = _requirements_pins(GATE_REQUIREMENTS)
    # Assert — 같은 이유. 주석 제거가 과하게 먹으면 여기서 빈다.
    assert gate, "requirements.txt 에서 핀을 하나도 못 찾았다 — 파서 확인"


def test_shared_pins_match_between_gate_and_publish():
    # Arrange
    gate = _requirements_pins(GATE_REQUIREMENTS)
    publish = _workflow_pins(PUBLISH_WORKFLOW)
    shared = sorted(set(gate) & set(publish))
    if not shared:
        pytest.skip("공통 핀이 없다 — 비교할 대상 없음")

    # Act
    mismatched = {
        name: (sorted(gate[name]), sorted(publish[name]))
        for name in shared
        if gate[name] != publish[name]
    }

    # Assert
    assert not mismatched, (
        "PR 게이트(requirements.txt)와 실 발행 워크플로의 핀이 갈렸다 — 게이트가 "
        f"프로덕션과 다른 라이브러리로 돌고 있다: {mismatched}"
    )


def test_each_package_is_pinned_to_a_single_version_per_source():
    # Arrange / Act — 한 곳에서 같은 패키지를 두 버전으로 설치하면 어느 쪽이
    # 이기는지가 순서에 달리고, 위 parity 비교도 흐려진다.
    conflicts: dict[str, dict[str, list[str]]] = {}
    for label, pins in (
        (GATE_REQUIREMENTS.name, _requirements_pins(GATE_REQUIREMENTS)),
        (PUBLISH_WORKFLOW.name, _workflow_pins(PUBLISH_WORKFLOW)),
    ):
        dupes = {n: sorted(v) for n, v in pins.items() if len(v) > 1}
        if dupes:
            conflicts[label] = dupes

    # Assert
    assert not conflicts, f"같은 패키지가 두 버전으로 핀돼 있다: {conflicts}"
