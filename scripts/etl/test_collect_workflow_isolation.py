"""느린 제공자가 무관한 수집기를 인질로 잡지 못하게 고정 (AAA).

**발의 (2026-09-04, run 33859136907).** `data-collect-hourly.yml` 의 "Collect all data
sources" 스텝만 Open-Meteo 를 친다. 아래 NOAA 수집 2종(GEFS-Aerosols AQ 격자,
GFS wind)은 완전히 독립인데, Open-Meteo 가 스로틀되자 그 스텝이 잡 예산 20분을
전부 먹고 잡이 타임아웃 됐다 — **AQ 격자·wind·업로드가 전부 skip**.

실측 지속시간: 정상 4.3~4.5분 → 스로틀 9.5분 → 20분 초과.

그래서 그 스텝에 스텝 타임아웃 + continue-on-error 를 걸어 실패를 격리했다.
이 테스트는 그 격리가 조용히 사라지지 않게 못박는다 — 지운 사람은 워크플로가
다시 멀쩡해 보이므로(정상일 땐 4분이면 끝난다) 스로틀이 올 때까지 모른다.

PyYAML 은 requirements.txt 에 없다(CI 미보장) — `test_ci_pin_parity.py` 와 같은
이유로 정규식 파싱을 쓴다.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/data-collect-hourly.yml"
SLOW_STEP = "Collect all data sources"


def _step_block(name: str) -> str:
    """해당 스텝의 `- name:` 부터 다음 스텝 시작 전까지."""
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index(f"- name: {name}")
    nxt = text.find("\n      - name: ", start + 1)
    return text[start: nxt if nxt != -1 else len(text)]


def _job_timeout_minutes() -> int:
    text = WORKFLOW.read_text(encoding="utf-8")
    # 잡 레벨은 들여쓰기 4칸, 스텝 레벨은 8칸 — 첫 매치가 잡 레벨이다.
    m = re.search(r"^    timeout-minutes:\s*(\d+)", text, re.M)
    assert m, "잡 레벨 timeout-minutes 를 못 찾았다 — 파싱이 깨졌다(vacuous pass 방지)"
    return int(m.group(1))


def test_slow_provider_step_has_its_own_timeout():
    # Arrange
    block = _step_block(SLOW_STEP)
    # Act
    m = re.search(r"^        timeout-minutes:\s*(\d+)", block, re.M)
    # Assert — 없으면 잡 예산 전체를 쓸 수 있다
    assert m, f"'{SLOW_STEP}' 에 스텝 타임아웃이 없다 — 잡 예산을 전부 먹을 수 있다"
    assert int(m.group(1)) < _job_timeout_minutes(), (
        "스텝 타임아웃이 잡 타임아웃 이상이면 격리가 아니다 — 스텝이 먼저 끊겨야 "
        "뒤따르는 NOAA 수집·업로드가 남은 예산으로 돈다"
    )


def test_slow_provider_step_failure_does_not_skip_the_independent_collectors():
    # Arrange / Act
    block = _step_block(SLOW_STEP)
    # Assert — 실패해도 뒤 스텝이 돌아야 한다. 조용해지는 건 아니다:
    # 산출물이 계속 안 올라오면 6h SLA 로 신선도 프로브가 run 을 빨갛게 만든다.
    assert re.search(r"^        continue-on-error:\s*true", block, re.M), (
        f"'{SLOW_STEP}' 이 continue-on-error 없이 실패하면 NOAA AQ·wind·업로드가 전부 skip 된다"
    )


def test_independent_collectors_still_follow_this_step():
    # Arrange / Act — 격리의 의미는 "뒤에 지킬 것이 있다" 는 전제 위에 있다.
    text = WORKFLOW.read_text(encoding="utf-8")
    slow_at = text.index(f"- name: {SLOW_STEP}")
    # Assert
    for later in ("Collect AQ grid (NOAA", "Collect wind grid (NOAA", "Upload AQ grid data to HF"):
        idx = text.find(f"- name: {later}")
        assert idx > slow_at, f"'{later}' 스텝이 사라졌거나 순서가 바뀌었다"


def test_the_isolated_failure_is_still_reported():
    # Arrange — 격리가 침묵이 되면 안 된다. `continue-on-error` 스텝에 `id` 가 없으면
    # 그 실패는 어디에도 남지 않는다(`test_build_mac_index.test_continue_on_error_steps_
    # _are_observable` 가 전 워크플로에 대해 강제하는 규칙 — 이 변경이 실제로 걸렸다).
    block = _step_block(SLOW_STEP)
    text = WORKFLOW.read_text(encoding="utf-8")

    # Act / Assert — id 가 있어야 outcome 을 참조할 수단이 생기고,
    m = re.search(r"^        id:\s*(\S+)", block, re.M)
    assert m, f"'{SLOW_STEP}' 에 id 가 없으면 outcome 을 참조할 수 없다"
    # 참조하는 스텝이 실제로 있어야 그 수단이 쓰인다(id 만 달고 안 읽으면 여전히 침묵).
    assert f"steps.{m.group(1)}.outcome" in text, (
        f"id '{m.group(1)}' 를 아무도 읽지 않는다 — 실패가 여전히 로그 밖으로 안 나온다"
    )
    assert "::warning title=Open-Meteo collection" in text, (
        "실패 경로에 애노테이션이 없다 — run 요약에서 보이지 않는다"
    )


def test_uploads_skip_missing_files_so_a_dead_provider_does_not_fail_the_upload():
    # Arrange / Act — 격리가 성립하려면 업로드가 없는 파일을 견뎌야 한다.
    # Open-Meteo 스텝이 끊기면 weather-grid/marine-data/가스/pollen 이 아예 없다.
    text = WORKFLOW.read_text(encoding="utf-8")
    # Assert — 두 업로드 루프 모두 존재 검사로 감싸져 있다
    assert text.count('if [ -f "${FILE}.json" ]; then') >= 2, (
        "업로드 루프가 파일 존재를 확인하지 않으면, 제공자 한 곳이 죽을 때 업로드 스텝이 함께 죽는다"
    )
