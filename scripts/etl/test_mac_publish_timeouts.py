"""수집 스텝의 wall-clock 백스톱과 job timeout 의 산술을 고정 (AAA).

발의: run 30353041479 (2026-07-28 11:00Z). CAMS 스텝이 상류 큐에서 25분을 다 쓰고
job 이 CANCELLED 됐고, 취소는 뒤 스텝을 전부 건너뛴다 — GEFS·AirKorea·EEA 가 아예
돌지 않았다. 한 소스가 망가져서 **네 소스의 발행이 통째로** 날아갔고, healthcheck
프로브는 그 결과를 airkorea/eea-utd `stale` + cams `absent` 세 증상으로 보고했다.

두 가지를 고정한다:

1. **모든 수집 스텝은 셸 `timeout` 백스톱을 갖는다.** 스텝 레벨 `timeout-minutes`
   가 아니라 셸 `timeout` 이어야 한다 — 전자는 스텝을 CANCELLED 로 표시하고
   `continue-on-error` 는 취소를 덮지 않는다(run 29988635214 가 EEA 로 증명).
   후자는 exit 124 = 평범한 step FAILURE 라 `continue-on-error` 가 먹는다.

2. **백스톱의 합 + 오버헤드 < job timeout.** 이 산술이 깨지면 모든 소스가 느린 run
   에서 이 수정이 막으려던 취소가 그대로 재발한다. 백스톱만 올리고 job timeout 을
   안 올리는(또는 그 반대의) 반쪽 수정을 잡는 게 이 테스트의 요점이다.

값을 이 파일에 복사하지 않는다 — 워크플로 YAML 을 직접 읽는다. 복사하면 새 수집기가
추가돼도 테스트가 모르고, 그게 #1028 의 구조("테스트가 프로덕션의 선언 자체를
검증하지 않았다")다. `test_ci_pin_parity.py` 와 같은 이유로 YAML 파서 의존성도 만들지
않는다(etl 게이트는 pytest/pandas/pyarrow 만 설치한다).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_WORKFLOW = REPO_ROOT / ".github/workflows/mac-data-publish.yml"

# 수집 스텝이 실패해도 나머지를 발행해야 하므로 셸 `timeout` 이 유일한 정답이다.
_SHELL_TIMEOUT_RE = re.compile(r"^\s*timeout\s+(\d+)\s+python3\s", re.M)
# 스텝 레벨 `timeout-minutes` = 취소 함정. 수집 스텝에는 있으면 안 된다.
_STEP_TIMEOUT_MINUTES_RE = re.compile(r"^        timeout-minutes:", re.M)

# 셋업(checkout/setup-python/apt/pip) + QA/assemble/publish/deploy 의 실측 여유.
# 성공 run 30330875144 는 수집 799s 대비 총 824s 였다(≈25s, 상태를 가진 러너 기준).
# hosted 콜드 스타트(apt + pip + setup-python)를 감안해 180s 로 잡는다.
OVERHEAD_SECONDS = 180


def _text() -> str:
    return PUBLISH_WORKFLOW.read_text(encoding="utf-8")


def _steps(text: str) -> list[tuple[str, str]]:
    """`- name:` 6칸 들여쓰기 = 스텝 경계. run 블록 안의 텍스트는 더 깊게 들여써 있다."""
    chunks = re.split(r"^      - name: ", text, flags=re.M)
    steps: list[tuple[str, str]] = []
    for chunk in chunks[1:]:
        name, _, body = chunk.partition("\n")
        steps.append((name.strip(), body))
    return steps


def _collector_steps(text: str) -> list[tuple[str, str]]:
    return [(n, b) for n, b in _steps(text) if n.startswith("Collect ")]


def _collect_job_timeout_seconds(text: str) -> int:
    """`collect-and-build` job 의 timeout-minutes (뒤 `deploy` job 것과 헷갈리지 않게)."""
    start = text.index("\n  collect-and-build:")
    match = re.search(r"^    timeout-minutes: (\d+)", text[start:], re.M)
    assert match, "collect-and-build 의 job timeout-minutes 를 못 찾았다"
    return int(match.group(1)) * 60


def test_workflow_exists():
    # Arrange / Act / Assert — 경로가 바뀌면 아래 전부가 조용히 vacuous PASS 가 된다.
    assert PUBLISH_WORKFLOW.is_file(), PUBLISH_WORKFLOW


def test_collector_steps_are_discovered():
    # 파싱이 깨져 0개를 걷어오면 아래 계약들이 전부 무의미해진다.
    names = [n for n, _ in _collector_steps(_text())]
    assert len(names) >= 4, f"수집 스텝 파싱 실패 — 걷어온 것: {names}"


@pytest.mark.parametrize("name", [n for n, _ in _collector_steps(_text().replace("\r\n", "\n"))])
def test_every_collector_has_a_shell_timeout_backstop(name: str):
    body = dict(_collector_steps(_text()))[name]

    backstops = _SHELL_TIMEOUT_RE.findall(body)

    assert backstops, (
        f"수집 스텝 '{name}' 에 셸 `timeout` 백스톱이 없다. 상류가 멈추면 job timeout 을 "
        f"통째로 잡아먹고, 취소는 뒤 스텝을 전부 건너뛴다 (run 30353041479)."
    )


@pytest.mark.parametrize("name", [n for n, _ in _collector_steps(_text().replace("\r\n", "\n"))])
def test_collectors_do_not_use_step_level_timeout_minutes(name: str):
    body = dict(_collector_steps(_text()))[name]

    assert not _STEP_TIMEOUT_MINUTES_RE.search(body), (
        f"수집 스텝 '{name}' 이 스텝 레벨 `timeout-minutes` 를 쓴다. 그러면 스텝이 "
        f"CANCELLED 로 표시되고 `continue-on-error` 가 그걸 덮지 못해 job 전체가 죽는다 "
        f"(run 29988635214). 셸 `timeout N` (exit 124 = FAILURE) 을 쓸 것."
    )


def test_backstop_sum_fits_inside_job_timeout():
    text = _text()

    total = sum(
        int(v) for _, body in _collector_steps(text) for v in _SHELL_TIMEOUT_RE.findall(body)
    )
    job_timeout = _collect_job_timeout_seconds(text)

    assert total + OVERHEAD_SECONDS <= job_timeout, (
        f"백스톱 합 {total}s + 오버헤드 {OVERHEAD_SECONDS}s 가 job timeout {job_timeout}s 를 "
        f"넘는다. 모든 소스가 느린 run 에서 job 이 취소되고, 그러면 이 백스톱들이 막으려던 "
        f"바로 그 전면 발행 실패가 재발한다. 백스톱을 줄이거나 job timeout 을 올릴 것."
    )
