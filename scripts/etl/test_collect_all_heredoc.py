"""data-collect-hourly.yml 의 `collect_all.py` heredoc 구조 테스트 (AAA).

**왜 이 파일이 있나.** 이 수집기는 워크플로 안 heredoc 이라 tracked 스크립트가 아니고,
그래서 **문법 오류조차 CI 가 잡지 않는다** — 깨지면 실행 시점에야 알게 된다. 여기서
heredoc 을 꺼내 `ast.parse` 로 컴파일하는 것만으로도 그 갭이 닫힌다.

**두 번째 이유(2026-09-04 실측 사고).** Open-Meteo 가 pollen 청크를 전부 거절한 실행이
`count=0` 파일을 써서, 461점짜리 멀쩡한 격자를 **빈 격자로 덮었다**. 업로드 루프는
`[ -f ]` 만 보므로 그대로 발행됐고, 신선도 프로브는 발행 *후* 를 보니 이미 늦었다
(빨간불은 떴지만 데이터는 이미 날아간 뒤). 빈 격자는 애초에 산출물이 아니다 —
안 쓰면 직전 발행분이 살아남는다(guard_no_empty_replace 정신).

그 가드는 heredoc 안에 있어 단위 테스트로 호출할 수 없으므로, **구조**를 검사한다:
격자 JSON 을 쓰는 모든 자리가 `if` 아래에 있는가. grep 이 아니라 AST 라서 들여쓰기·
따옴표·f-string 변형에 흔들리지 않는다.
"""
from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/data-collect-hourly.yml"
BEGIN = "cat > collect_all.py << 'SCRIPT'"
END = "SCRIPT"


def _extract_heredoc() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if BEGIN in ln)
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == END)
    return textwrap.dedent("\n".join(lines[start + 1:end]))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    table: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            table[child] = parent
    return table


def _write_calls(tree: ast.AST) -> list[ast.Call]:
    """`open(..., "w")` 호출 전부."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "open" or len(node.args) < 2:
            continue
        mode = node.args[1]
        if isinstance(mode, ast.Constant) and mode.value == "w":
            found.append(node)
    return found


def test_heredoc_is_valid_python():
    # Arrange / Act — tracked 스크립트가 아니라 이 검사가 유일한 문법 게이트다.
    tree = ast.parse(_extract_heredoc())
    # Assert
    assert isinstance(tree, ast.Module)


def test_every_grid_write_is_guarded_against_an_empty_result():
    # Arrange
    src = _extract_heredoc()
    tree = ast.parse(src)
    parents = _parents(tree)
    writes = _write_calls(tree)
    assert writes, "open(..., 'w') 를 하나도 못 찾았다 — 추출이 깨졌다(vacuous pass 방지)"

    # Act — 각 쓰기가 조건문 아래 있는지(= 빈 결과일 때 건너뛸 수 있는 구조인지).
    # 조건이 **상수**면 가드가 아니다(`if True:`) — 구조만 보면 통과하므로 따로 잡는다.
    unguarded = []
    for call in writes:
        node: ast.AST | None = call
        while node is not None and not isinstance(node, ast.If):
            node = parents.get(node)
        segment = ast.get_source_segment(src, call) or "<?>"
        if node is None:
            unguarded.append(f"{segment} (조건 없음)")
        elif isinstance(node.test, ast.Constant):
            unguarded.append(f"{segment} (상수 조건 `if {node.test.value}` — 가드가 아니다)")

    # Assert — 빈 격자를 발행하면 직전 정상 발행분이 사라진다
    assert not unguarded, f"빈 결과를 막지 못하는 쓰기: {unguarded}"


def _payload_dicts(tree: ast.AST) -> list[ast.Dict]:
    """격자 payload dict — `"points"` 키를 가진 dict 리터럴."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        if "points" in keys:
            found.append(node)
    return found


def test_every_grid_payload_discloses_chunk_coverage():
    """부분 수집이 완전본으로 위장하지 못하게 한다.

    2026-09-04 실측: 같은 run 에서 pollen 은 0 점으로 *붕괴*해 `if pts:` 가드가
    잡았지만, 가스 3종은 2196→1980 점(−10%)으로 *감쇠*해 그대로 발행됐다.
    truthy 검사는 1 점만 살아도 통과한다 — 붕괴는 잡히고 감쇠는 통과한다.
    그래서 발행물이 커버리지를 **말하게** 한다.
    """
    # Arrange
    src = _extract_heredoc()
    payloads = _payload_dicts(ast.parse(src))

    # Assert — 네 격자군(weather/marine/gas/pollen) 전부. 추출이 깨지면 vacuous pass.
    assert len(payloads) >= 4, f"payload dict 를 {len(payloads)}개만 찾았다 — 추출이 깨졌다"

    # Act — 커버리지가 들어가는 경로는 `**<블록>_cov` 스프레드 하나로 못박는다.
    missing = []
    for node in payloads:
        spreads = [
            value.id
            for key, value in zip(node.keys, node.values)
            if key is None and isinstance(value, ast.Name)
        ]
        if not any(name.endswith("_cov") for name in spreads):
            missing.append(ast.get_source_segment(src, node) or "<?>")

    # Assert
    assert not missing, f"커버리지 공시 없이 발행하는 payload: {[m[:80] for m in missing]}"


def test_aq_blocks_request_in_large_chunks():
    """AQ 호스트만 요청 수를 줄인다 — 429 는 그 호스트에 국한된 호출량 문제였다.

    지점 수(커버리지)가 아니라 **요청 수**를 줄이는 것이 요점이다. 청크를 다시
    30 으로 되돌리면 요청이 100/run 으로 돌아가 같은 스로틀에 걸린다.
    weather/marine 은 같은 러너에서 멀쩡했으므로 CHUNK=30 그대로 둔다.
    """
    # Arrange
    src = _extract_heredoc()
    tree = ast.parse(src)

    # Act — 상수 값 회수
    chunks = {
        node.targets[0].id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("CHUNK", "AQ_CHUNK")
        and isinstance(node.value, ast.Constant)
    }

    # Assert
    assert chunks.get("AQ_CHUNK", 0) >= 100, f"AQ 청크가 작다: {chunks}"
    assert chunks.get("CHUNK") == 30, f"weather/marine 청크를 건드렸다: {chunks}"
    # URL 리스트는 f-string 안에 `p[0]` 같은 대괄호가 섞여 있어 문자열 자르기로는
    # 못 뗀다(초기 구현이 그래서 항상 red 였다). 할당문을 AST 로 집는다.
    assigns = {
        node.targets[0].id: ast.get_source_segment(src, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    for name in ("gas_urls", "pollen_urls"):
        block = assigns.get(name, "")
        assert block, f"{name} 할당을 못 찾았다 — 추출이 깨졌다(vacuous pass 방지)"
        assert "AQ_CHUNK" in block, f"{name} 가 AQ_CHUNK 를 안 쓴다"
        # slice 와 step 이 갈리면(`range(..., CHUNK)` + `[ci:ci+AQ_CHUNK]`) 청크가
        # 겹쳐 요청이 오히려 늘어난다 — 한쪽만 보면 통과하므로 bare CHUNK 를 금지한다.
        assert not re.search(r"(?<!AQ_)\bCHUNK\b", block), f"{name} 에 bare CHUNK 가 남아 있다: {block[:120]}"


def test_chunk_policy_comes_from_the_tracked_module():
    """재시도·스로틀 정책이 heredoc 안으로 되돌아오면 단위 테스트가 다시 눈이 먼다."""
    # Arrange
    src = _extract_heredoc()
    module = WORKFLOW.resolve().parents[2] / "scripts/etl/openmeteo_chunks.py"

    # Assert — import 대상이 실존하고, heredoc 이 자체 재시도 루프를 되살리지 않았다.
    assert module.exists(), f"{module} 가 없다 — heredoc import 가 런타임에 죽는다"
    assert "from openmeteo_chunks import" in src
    assert "for attempt in range(" not in src, "heredoc 이 자체 재시도 루프를 되살렸다"


def _fetch_calls_by_label(tree: ast.AST) -> dict[str, ast.Call]:
    """`fetch_chunks("<label>", ...)` 호출을 라벨로 색인한다."""
    found: dict[str, ast.Call] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "fetch_chunks"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            found[node.args[0].value] = node
    return found


def test_aq_host_fetches_are_behind_the_daily_budget_gate():
    """가스·pollen 수집이 AQ_HOST_BUDGET_RUN 게이트 아래에 있는가.

    2026-09-05 발의: Open-Meteo 무료 티어는 요청 수가 아니라 **좌표 수 가중**으로
    미터링한다 — #24 의 청크 통합(100→15 요청)은 예산을 줄이지 못했고, 일일
    사용량 ≈24k 가 한도 ~10k 를 오전에 소진해 온종일 429 였다. 게이트가 사라지면
    같은 사고가 재발하므로 구조로 못박는다. weather/marine 은 다른 호스트라
    게이트 밖이어야 한다 — 거기까지 게이트가 번지면 멀쩡한 수집이 반으로 준다.
    """
    # Arrange
    src = _extract_heredoc()
    tree = ast.parse(src)
    parents = _parents(tree)
    fetches = _fetch_calls_by_label(tree)
    for label in ("Weather", "Marine", "Gas grid", "Pollen"):
        assert label in fetches, f"fetch_chunks({label!r}) 를 못 찾았다 — 추출이 깨졌다"

    def gated_by_budget(call: ast.Call) -> bool:
        node: ast.AST | None = call
        while node is not None:
            if isinstance(node, ast.If) and any(
                isinstance(n, ast.Name) and n.id == "AQ_HOST_BUDGET_RUN"
                for n in ast.walk(node.test)
            ):
                return True
            node = parents.get(node)
        return False

    # Act / Assert — AQ 호스트 두 블록은 게이트 아래, 나머지 두 블록은 게이트 밖.
    assert gated_by_budget(fetches["Gas grid"]), "가스 수집이 예산 게이트 밖이다"
    assert gated_by_budget(fetches["Pollen"]), "pollen 수집이 예산 게이트 밖이다"
    assert not gated_by_budget(fetches["Weather"]), "weather 까지 게이트에 걸렸다"
    assert not gated_by_budget(fetches["Marine"]), "marine 까지 게이트에 걸렸다"



def test_budget_gate_is_decided_by_publication_age_not_wall_clock():
    """AQ_HOST_BUDGET_RUN 이 aq_budget_gate 의 나이 판정에서 오는가.

    2026-09-27 발의: `now.hour % 12 < 3` 벽시계 게이트는 schedule 이벤트가 창을
    빗나가면 수집을 통째로 잃는다(가스·pollen 141h 노후). 누가 벽시계 식으로
    되돌리면 위 테스트(게이트 아래에 있는가)는 그대로 통과하므로 출처를 못박는다.
    """
    # Arrange
    tree = ast.parse(_extract_heredoc())
    # Act — AQ_HOST_BUDGET_RUN 에 값을 넣는 모든 대입
    sources = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(
            isinstance(n, ast.Name) and n.id == "AQ_HOST_BUDGET_RUN"
            for t in node.targets for n in ast.walk(t)
        )
    ]
    # Assert
    assert len(sources) == 1, f"AQ_HOST_BUDGET_RUN 대입이 {len(sources)}개"
    call = sources[0]
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    assert call.func.id == "aq_host_budget_run", "게이트가 발행물 나이 판정을 거치지 않는다"

def test_all_four_grid_families_are_still_written_here():
    # Arrange / Act — 가드를 넣다가 쓰기 자체를 지워버리면 위 테스트는 통과한다.
    # 네 격자군이 여전히 이 스크립트에서 나온다는 것을 못박는다.
    src = _extract_heredoc()
    # Assert
    for name in ("weather-grid.json", "marine-data.json", "pollen-grid.json"):
        assert name in src, f"{name} 쓰기가 사라졌다"
    assert 'f"{file_prefix}.json"' in src, "가스 격자(o3/no2/co) 쓰기가 사라졌다"
