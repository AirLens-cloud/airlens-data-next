"""build_mac_index.py 단위 테스트 (AAA) + 워크플로 배선 계약.

이 로직은 `mac-data-publish.yml` 안의 인라인 heredoc 이었다 — 테스트 불가라 영원히
회귀 미보호였다. 스크립트로 뺐으니 이제 계약을 건다.

핵심 회귀: `available: true` 만으로는 "이번 run 에 새로 받았다" 와 "수집기가 일주일째
죽었지만 지난 파일을 계속 내보내는 중이다" 가 구분되지 않는다. 그 구분이 없어서
CAMS 만성 실패가 초록불 뒤에 묻혀 있었다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import build_mac_index as m

UTC = timezone.utc
NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/mac-data-publish.yml"

_ENVELOPE = {
    "kind": "grid",
    "source": "cams",
    "generatedAt": "2026-07-28T09:00:00Z",
    "validAt": "2026-07-28T00:00:00Z",
    "expiresAt": "2026-07-28T12:00:00Z",
    "quality": {"grade": "b"},
}


# ────────────────────────── index 조립 ──────────────────────────

def test_index_covers_every_source_even_when_absent():
    # Arrange — 아무 소스도 없는 최악의 run
    # Act
    index = m.build_index({}, {}, {}, NOW)

    # Assert — 빠진 소스를 index 에서 아예 빼면 "없다"가 "몰랐다"와 구분되지 않는다
    assert set(index["sources"]) == set(m.SOURCE_NAMES)
    assert all(e["available"] is False for e in index["sources"].values())


def test_records_why_a_source_is_missing():
    # Arrange — 자격증명 부재로 건너뛴 것과 수집기가 죽은 것은 다른 사건이다
    outcomes = {"cams": "failure", "airkorea": "skipped", "gefs-chem": "success"}

    # Act
    index = m.build_index({}, outcomes, {}, NOW)

    # Assert
    assert index["sources"]["cams"]["unavailableReason"] == "failure"
    assert index["sources"]["airkorea"]["unavailableReason"] == "skipped"
    assert "unavailableReason" not in index["sources"]["gefs-chem"]


def test_distinguishes_fresh_from_stale_baseline():
    # Arrange — 둘 다 available:true 지만 전혀 다른 상태다. cams 는 수집기가 죽었고
    # 지난 발행분으로 버티는 중 — 그걸 "정상"으로 읽으면 만성 실패가 묻힌다.
    envelopes = {"cams": _ENVELOPE, "gefs-chem": _ENVELOPE}

    # Act
    index = m.build_index(
        envelopes,
        {"cams": "failure", "gefs-chem": "success"},
        {"cams": "baseline", "gefs-chem": "fresh"},
        NOW,
    )

    # Assert
    cams, gefs = index["sources"]["cams"], index["sources"]["gefs-chem"]
    assert cams["available"] is True and cams["servedFrom"] == "baseline"
    assert cams["unavailableReason"] == "failure"   # 살아있는 척하지 않는다
    assert gefs["servedFrom"] == "fresh" and "unavailableReason" not in gefs


def test_last_attempt_is_stamped_even_for_failed_sources():
    # Arrange / Act — 실패해도 "언제 시도했는지" 는 남아야 발행물 나이를 잴 수 있다
    index = m.build_index({}, {"cams": "failure"}, {}, NOW)

    # Assert
    assert index["sources"]["cams"]["lastAttemptAt"] == "2026-07-28T10:00:00Z"


def test_envelope_fields_are_carried_through_unchanged():
    # Arrange / Act
    index = m.build_index({"cams": _ENVELOPE}, {}, {"cams": "fresh"}, NOW)

    # Assert — 소비자(mac 클라이언트)가 읽는 필드가 그대로 실려야 한다
    entry = index["sources"]["cams"]
    for key in ("kind", "source", "generatedAt", "validAt", "expiresAt", "quality"):
        assert entry[key] == _ENVELOPE[key]


def test_read_envelope_unwraps_list_documents(tmp_path):
    # Arrange — 어떤 소스는 envelope 배열로 쓴다
    p = tmp_path / "cams.json"
    p.write_text(json.dumps([_ENVELOPE]), encoding="utf-8")

    # Act / Assert
    assert m.read_envelope(str(p)) == _ENVELOPE
    assert m.read_envelope(str(tmp_path / "missing.json")) is None


# ────────────────────────── job summary ──────────────────────────

def test_summary_flags_degraded_runs():
    # Arrange — 이 run 은 성공으로 끝난다(1 소스 실패로 전체 발행을 죽이지 않는 게
    # 옳다). 그래도 summary 가 "정상"이라고 말하면 그게 초록불 거짓말이다.
    index = m.build_index({"gefs-chem": _ENVELOPE}, {"cams": "failure"}, {}, NOW)

    # Act
    out = m.render_summary(index)

    # Assert
    assert "degraded" in out
    assert "cams" in out


def test_summary_has_no_degraded_banner_when_all_sources_are_fresh():
    # Arrange
    envelopes = {name: _ENVELOPE for name in m.SOURCE_NAMES}
    served = {name: "fresh" for name in m.SOURCE_NAMES}
    outcomes = {name: "success" for name in m.SOURCE_NAMES}

    # Act
    out = m.render_summary(m.build_index(envelopes, outcomes, served, NOW))

    # Assert
    assert "degraded" not in out


# ────────────────────────── 워크플로 배선 계약 ──────────────────────────
#
# 스크립트만 고치고 워크플로는 여전히 옛 인라인 heredoc 을 돌리는 반쪽 수정을 막는다.
# 워크플로 YAML 을 직접 읽는다 — fixture 에 복사하면 실물이 바뀌어도 테스트가 모른다.

def test_workflow_invokes_the_extracted_builder():
    # Arrange / Act
    text = WORKFLOW.read_text(encoding="utf-8")

    # Assert
    assert "scripts/etl/build_mac_index.py" in text
    # 옛 인라인 조립이 남아 있으면 두 곳이 index 를 쓰게 된다
    assert 'index["sources"][name] = {' not in text


def test_workflow_passes_outcome_and_served_from_paths():
    # Arrange / Act
    text = WORKFLOW.read_text(encoding="utf-8")

    # Assert — 둘 중 하나라도 안 넘기면 index 는 다시 "왜 없는지 모르는" 상태로 돌아간다
    assert "MAC_INDEX_OUTCOMES=" in text
    assert "MAC_INDEX_SERVED_FROM=" in text


# `continue-on-error: true` + `id:` 없음 = 그 스텝의 실패가 **어디에도 남지 않는다**.
# job 은 초록불이고, outcome 을 참조할 수단조차 없다. CAMS 가 정확히 그 상태로 며칠을
# 죽어 있었다. 아래는 의도된 fire-and-forget 만 허용하는 화이트리스트 — 전수 실측
# 결과 전 워크플로 통틀어 continue-on-error 9건 중 3건이고, 3건 모두 사유가 분명하다.
_NO_ID_ALLOWED = {
    # in-place 병합. 실패해도 이번 run 의 fresh-only 출력이 그대로 발행된다
    # (병합 전 동작) — 별도로 기록할 상태가 없다.
    ("mac-data-publish.yml", "Carry over unexpired stations the rotation did not visit this run (EEA)"),
}


def test_continue_on_error_steps_are_observable():
    # Arrange — 한 파일이 아니라 전 워크플로를 훑는다. 이 결함은 mac 전용이 아니다.
    workflow_dir = REPO_ROOT / ".github/workflows"

    # Act
    missing = []
    for path in sorted(workflow_dir.glob("*.yml")):
        for step in re.split(r"\n\s+- (?=name:|uses:)", path.read_text(encoding="utf-8")):
            if not re.search(r"^\s*continue-on-error:\s*true", step, re.M):
                continue
            if re.search(r"^\s*id:\s*\S", step, re.M):
                continue
            first = step.splitlines()[0].strip()
            label = first.split(":", 1)[1].strip() if ":" in first else first
            missing.append((path.name, label))

    # Assert — 새 fire-and-forget 스텝은 사유를 주석에 남기고 위 목록에 올릴 것
    assert not (set(missing) - _NO_ID_ALLOWED), (
        f"id 없는 continue-on-error 스텝 — 실패가 어디에도 안 남는다: "
        f"{sorted(set(missing) - _NO_ID_ALLOWED)}"
    )


def test_no_id_allowlist_has_no_stale_entries():
    # Arrange — 화이트리스트가 실물보다 오래 살아남으면 게이트가 조용히 넓어진다.
    workflow_dir = REPO_ROOT / ".github/workflows"
    existing = {p.name for p in workflow_dir.glob("*.yml")}

    # Act
    orphans = {name for name, _ in _NO_ID_ALLOWED if name not in existing}

    # Assert
    assert not orphans, f"화이트리스트가 사라진 워크플로를 가리킨다: {sorted(orphans)}"


# ────────────────────────── 부분 수집 커버리지 ──────────────────────────
#
# `available: true` 는 "파일이 있다" 만 말한다. EEA 는 매 run 국가 일부만 도는 게
# 정상이라, 그 상태가 완전 수집과 같은 ✅ 로 뭉치면 "6개국 중 2개국만 신선" 이
# "정상" 으로 읽힌다.

_COVERAGE_FULL = {
    "budgetSeconds": 600.0,
    "countries": {
        "DE": {"status": "visited", "stations": 400, "files_total": 9, "files_dropped_budget": 0,
               "download_failures": 0},
        "FR": {"status": "visited", "stations": 300, "files_total": 7, "files_dropped_budget": 0,
               "download_failures": 0},
    },
}


def test_coverage_rides_along_when_the_collector_reported_it():
    # Arrange / Act
    index = m.build_index({"eea-utd": _ENVELOPE}, {}, {"eea-utd": "fresh"}, NOW,
                          coverage={"eea-utd": _COVERAGE_FULL})

    # Assert — 수집기가 쓴 문서를 그대로 싣는다 (index 가 shape 를 규정하지 않는다)
    assert index["sources"]["eea-utd"]["coverage"] == _COVERAGE_FULL


def test_sources_without_a_coverage_report_get_no_coverage_key():
    # Arrange / Act — 없는 걸 지어내지 않는다
    index = m.build_index({"cams": _ENVELOPE}, {}, {}, NOW, coverage={})

    # Assert
    assert "coverage" not in index["sources"]["cams"]


def test_summary_marks_partial_country_coverage_as_degraded():
    # Arrange — 예산이 끊겨 절반만 돈 run. 파일은 신선하므로 available 은 true 다.
    partial = {
        "countries": {
            "DE": {"status": "visited", "files_dropped_budget": 0, "download_failures": 0},
            "PL": {"status": "skipped_budget", "files_dropped_budget": 0, "download_failures": 0},
        },
    }
    index = m.build_index({"eea-utd": _ENVELOPE}, {"eea-utd": "success"}, {"eea-utd": "fresh"},
                          NOW, coverage={"eea-utd": partial})

    # Act
    out = m.render_summary(index)

    # Assert
    assert "1/2 countries" in out
    assert "degraded" in out


def test_summary_does_not_flag_a_fully_covered_run():
    # Arrange
    envelopes = {name: _ENVELOPE for name in m.SOURCE_NAMES}
    served = {name: "fresh" for name in m.SOURCE_NAMES}
    outcomes = {name: "success" for name in m.SOURCE_NAMES}
    index = m.build_index(envelopes, outcomes, served, NOW, coverage={"eea-utd": _COVERAGE_FULL})

    # Act
    out = m.render_summary(index)

    # Assert
    assert "full 2" in out
    assert "degraded" not in out


def test_partial_downloads_are_visible_even_when_every_country_was_visited():
    # Arrange — 6개국을 다 돌았어도 파일을 흘렸으면 완전 수집이 아니다.
    # FR 의 `urlopen error timed out` 다발이 정확히 이 모양이다.
    flaky = {
        "countries": {
            "FR": {"status": "visited", "files_dropped_budget": 0, "download_failures": 12},
        },
    }

    # Act
    label = m.format_coverage(flaky)

    # Assert
    assert "partial" in label
    assert not label.startswith("full")


def test_format_coverage_reports_nothing_when_there_is_nothing_to_report():
    # Arrange / Act / Assert
    assert m.format_coverage(None) == "—"
    assert m.format_coverage({}) == "—"
    assert m.format_coverage({"countries": {}}) == "—"


def test_read_coverage_picks_up_the_sidecar_the_collector_wrote(tmp_path, monkeypatch):
    # Arrange — 수집기는 <dir>/<source>.json 에 쓰고, index 는 여기서 읽는다.
    # 이 연결이 끊기면 사이드카는 써지지만 발행물에는 안 실린다(조용한 반쪽 수정).
    (tmp_path / "eea-utd.json").write_text(json.dumps(_COVERAGE_FULL), encoding="utf-8")
    monkeypatch.setenv("MAC_INDEX_COVERAGE_DIR", str(tmp_path))

    # Act / Assert
    assert m.read_coverage("eea-utd") == _COVERAGE_FULL
    assert m.read_coverage("cams") is None          # 사이드카 안 쓴 소스
    monkeypatch.delenv("MAC_INDEX_COVERAGE_DIR")
    assert m.read_coverage("eea-utd") is None       # 디렉터리 미설정
