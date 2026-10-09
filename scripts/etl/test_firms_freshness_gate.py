"""firms-collect.yml 의 freshness 게이트(heredoc `check_freshness.py`) 행동 테스트 (AAA).

**왜 이 파일이 있나.** 수집 로직은 `collect_firms.py` 로 옮겨져 `test_collect_firms.py` 가
직접 시험하지만, 발행 *후* 를 검증하는 freshness 게이트는 아직 워크플로 YAML 안
heredoc 이라 어떤 스위트도 닿지 않았다. 여기서 YAML 에서 그대로 꺼내 실행한다 —
사본을 두면 드리프트하고, 드리프트한 사본의 통과는 의미가 없다.

이 테스트는 모노레포(`AirLens-cloud/AirLens`)에 있던 `scripts/ci/firms_freshness_guard_test.py`
의 이식이다. 2026-09-26 모노레포의 FIRMS 크론이 은퇴하고 이 레포가 단일 발행자가
되면서 그 테스트도 함께 지워졌는데, 이 레포에는 같은 게이트를 시험하는 것이 없었다
(`test_collect_all_heredoc.py` 는 data-collect-hourly 전용).

게이트가 막아야 하는 것 (셋 다 2026-07-30 에 실제로 열렸거나 그 사고의 교훈이다):
  1) 나이만 재면 "방금 쓴 빈 객체" 가 초록불을 받는다 → count<=0 도 실패로.
  2) 오래된 사본은 실패로 (기존 stale 게이트 회귀 방지).
  3) 업로드 직후 캐시된 옛 사본을 읽으면 방금 올린 것을 검증하지 못한다
     → run-id 캐시 버스터 + no-cache.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/firms-collect.yml"
SCRIPT = "check_freshness.py"
BLOCK_RE = re.compile(r"cat > (\w+\.py) << 'SCRIPT'\n(.*?)\n\s*SCRIPT\n", re.S)

# urllib.request.urlopen 을 고정 payload 로 바꿔치기 — 네트워크 없이 게이트 로직만 돈다.
SHIM = """
import io, urllib.request
class _R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
_PAYLOAD = %r
urllib.request.urlopen = lambda *a, **k: _R(_PAYLOAD.encode())
"""

ENV = {
    "MAX_STALENESS_HOURS": "18",
    "GITHUB_RUN_ID": "test",
    "GITHUB_RUN_ATTEMPT": "1",
}


def _extract_gate() -> str:
    """YAML 에서 heredoc python 을 추출. 추출 실패는 vacuous pass 이므로 곧 실패다."""
    blocks = {name: textwrap.dedent(body)
              for name, body in BLOCK_RE.findall(WORKFLOW.read_text(encoding="utf-8"))}
    assert SCRIPT in blocks, (
        f"{WORKFLOW.name} 에서 {SCRIPT} 를 추출하지 못했다 (찾은 것: {sorted(blocks)}). "
        "heredoc 형식이나 들여쓰기가 바뀌면 이 시험은 아무것도 검사하지 않게 된다."
    )
    return blocks[SCRIPT]


@pytest.fixture(scope="module")
def gate_src() -> str:
    return _extract_gate()


def _run(tmp_path: Path, gate_src: str, payload: object) -> subprocess.CompletedProcess[str]:
    target = tmp_path / f"shim_{SCRIPT}"
    target.write_text(SHIM % json.dumps(payload) + gate_src, encoding="utf-8")
    return subprocess.run([sys.executable, str(target)], capture_output=True, text=True,
                          cwd=tmp_path, env={"PATH": "/usr/bin:/bin", **ENV})


def test_fresh_but_empty_payload_fails(tmp_path, gate_src):
    # Arrange — 나이 게이트만 있던 구멍: 방금 쓴 count=0
    payload = {"refTime": datetime.now(timezone.utc).isoformat(), "count": 0}
    # Act
    r = _run(tmp_path, gate_src, payload)
    # Assert
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FIRMS empty" in r.stderr


def test_fresh_and_populated_payload_passes(tmp_path, gate_src):
    # Arrange
    payload = {"refTime": datetime.now(timezone.utc).isoformat(), "count": 17356}
    # Act
    r = _run(tmp_path, gate_src, payload)
    # Assert — 통과하면서 count 를 함께 보고한다
    assert r.returncode == 0, r.stdout + r.stderr
    assert "count=17356" in r.stdout


def test_stale_payload_fails(tmp_path, gate_src):
    # Arrange — SLA(18h) 를 한참 넘긴 사본
    payload = {"refTime": "2026-07-25T08:24:17.300068+00:00", "count": 17356}
    # Act
    r = _run(tmp_path, gate_src, payload)
    # Assert
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FIRMS stale" in r.stderr


def test_probe_reads_the_target_with_a_cache_buster(gate_src):
    # Arrange / Act — 구조 검사: 매 실행 고유 URL + no-cache
    # Assert
    assert "GITHUB_RUN_ID" in gate_src and "cb=" in gate_src, "run-id 캐시 버스터가 없다"
    assert "no-cache" in gate_src, "no-cache 헤더가 없다"
    assert "wind-data/active-fires.json" in gate_src, "업로드 스텝과 같은 대상을 읽어야 게이트다"
