#!/usr/bin/env python3
"""mac 스냅샷 `index.json` 조립 + job summary 렌더.

**왜 워크플로 heredoc 에서 빼왔나.** 이 조립 로직은 `mac-data-publish.yml` 안의
인라인 Python 이었다. 워크플로 안의 인라인 코드는 테스트가 불가능해서 영원히 회귀
미보호 상태로 남는다 — 그리고 이 파일이 하는 일은 "각 소스가 지금 어떤 상태인가" 를
공개 발행물에 적는 것이라, 틀리면 조용히 거짓을 발행한다.

**무엇을 더 적나.** 기존 index 는 소스별로 `available` 참/거짓만 적었다. 그래서
`cams: {"available": false}` 를 보고도 *왜* 없는지 — 자격증명이 없어 건너뛴 건지,
수집기가 죽은 건지, 죽었지만 지난 스냅샷으로 버티는 중인지 — 알 수 없었다.
실제로 CAMS 는 `continue-on-error: true` 뒤에서 만성적으로 죽는 동안 워크플로가 계속
초록불이었고, 인벤토리는 그걸 "키 미설정" 으로 잘못 분류했다.

그래서 3 필드를 더한다:
- `servedFrom` — `"fresh"`(이번 run 산출) / `"baseline"`(지난 발행분 유지) / `null`
- `lastAttemptAt` — 이번 run 이 이 소스를 시도한 시각 (실패해도 기록)
- `unavailableReason` — 수집 step 의 outcome (`failure` / `skipped` / …)

**하위호환**: mac 클라이언트 `IndexEntry`(AirLensSnapshotClient.swift:136) 는
`available`/`expiresAt` 만 디코딩하고, Swift 합성 Decodable 은 미지 키를 무시한다.
필드 추가는 안전하다 — 제거·개명은 아니다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# 발행 대상 소스 — 워크플로의 수집 step 과 1:1. 여기가 정본이고, 워크플로는 이
# 목록을 자기 쪽에 복사하지 않는다(복사하면 소스 추가 시 한쪽만 늘어난다).
SOURCE_NAMES: tuple[str, ...] = ("cams", "gefs-chem", "airkorea", "eea-utd")

INDEX_NOTE = (
    "Interim per-source index — NOT the design SOT's geocell-tiled "
    "current.json/manifest contract (build_mac_aq_tiles.py does not exist "
    "yet). Each sources/<name>.json is a single QA'd/provenance'd/quantized "
    "envelope (mac_aq_adapter schema); staleness is self-described by its "
    "own generatedAt/validAt/expiresAt fields, not a separate flag here."
)


# ────────────────────────── pure helpers (테스트 대상) ──────────────────────────

def read_envelope(path: str) -> dict | None:
    """발행된 소스 파일 하나를 읽어 envelope dict 로. 없으면 None."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc[0] if isinstance(doc, list) else doc


def read_coverage(name: str) -> dict | None:
    """소스 하나의 커버리지 사이드카. `MAC_INDEX_COVERAGE_DIR/<name>.json`.

    소스별로 "얼마나 덮었나" 는 모양이 제각각이라(EEA=국가별, CAMS=변수별이 될 수도)
    index 가 shape 를 규정하지 않고 수집기가 쓴 문서를 그대로 싣는다. 파일이 없으면
    그 소스는 커버리지를 보고하지 않는 것 — 없는 걸 지어내지 않는다.
    """
    base = os.environ.get("MAC_INDEX_COVERAGE_DIR", "")
    if not base:
        return None
    path = os.path.join(base, f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_index(
    envelopes: dict[str, dict | None],
    outcomes: dict[str, str],
    served_from: dict[str, str],
    now: datetime,
    coverage: dict[str, dict] | None = None,
) -> dict:
    """소스별 상태를 index 문서로. 없는 소스도 *이유와 함께* 적는다.

    `outcomes` = 수집 step 의 GitHub Actions outcome
    (`success`/`failure`/`skipped`/`cancelled`). `served_from` = assemble 단계가
    실제로 무엇을 발행했는지(`fresh`/`baseline`). `coverage` = 수집기가 남긴
    부분수집 계측(EEA 는 국가별) — `available:true` 여도 **얼마나** 덮었는지는
    별개 사실이라, 없으면 부분 수집이 완전 수집처럼 보인다.
    """
    coverage = coverage or {}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    index: dict = {"generatedAt": stamp, "note": INDEX_NOTE, "sources": {}}

    for name in SOURCE_NAMES:
        outcome = outcomes.get(name)
        entry: dict = {
            "available": envelopes.get(name) is not None,
            "lastAttemptAt": stamp,
            "servedFrom": served_from.get(name),
        }
        envelope = envelopes.get(name)
        if envelope is not None:
            entry.update(
                kind=envelope.get("kind"),
                source=envelope.get("source"),
                generatedAt=envelope.get("generatedAt"),
                validAt=envelope.get("validAt"),
                expiresAt=envelope.get("expiresAt"),
                quality=envelope.get("quality"),
            )
        # 실패했는데 지난 스냅샷으로 버티는 중인 경우도 이유를 남긴다 — available:true
        # 만 보고 "정상" 이라 읽으면 만성 실패가 그대로 묻힌다.
        if outcome in ("failure", "skipped", "cancelled"):
            entry["unavailableReason"] = outcome
        if coverage.get(name):
            entry["coverage"] = coverage[name]
        index["sources"][name] = entry

    return index


def format_coverage(coverage: dict | None) -> str:
    """커버리지 문서를 summary 한 칸으로. 문서가 없으면 `—`(보고 안 함).

    `full N` 이 아닌 값은 전부 부분 수집 신호로 취급된다(render_summary 참조) —
    그래서 "다 덮었다" 는 접두사는 실제로 다 덮었을 때만 붙인다.
    """
    if not coverage:
        return "—"
    countries = coverage.get("countries") or {}
    if not countries:
        return "—"
    visited = sum(1 for c in countries.values() if c.get("status") == "visited")
    total = len(countries)
    partial = sum(
        1 for c in countries.values()
        if c.get("files_dropped_budget") or c.get("download_failures")
    )
    if visited == total and not partial:
        return f"full {total}"
    return f"{visited}/{total} countries" + (f", {partial} partial" if partial else "")


def render_summary(index: dict) -> str:
    """job summary 용 마크다운 표.

    초록불 run 을 열었을 때 **첫 화면에** 죽은 소스가 보여야 한다. 지금은 로그
    깊숙한 곳의 `::warning::` 한 줄이라 아무도 안 본다.
    """
    rows = [
        "| source | available | servedFrom | coverage | reason | expiresAt |",
        "|---|---|---|---|---|---|",
    ]
    degraded = []
    for name, e in index.get("sources", {}).items():
        mark = "✅" if e.get("available") else "❌"
        reason = e.get("unavailableReason") or "—"
        if reason != "—" or not e.get("available"):
            degraded.append(name)
        cov = format_coverage(e.get("coverage"))
        # 부분 수집은 실패가 아니지만 완전 수집도 아니다. 같은 ✅ 로 뭉치면
        # "6개국 중 2개국만 신선" 이 "정상" 으로 읽힌다.
        if cov != "—" and not cov.startswith("full"):
            degraded.append(name)
        rows.append(
            f"| `{name}` | {mark} | {e.get('servedFrom') or '—'} | {cov} | {reason} "
            f"| {e.get('expiresAt') or '—'} |"
        )

    head = "## mac snapshot publish\n\n"
    if degraded:
        # 이 run 은 성공으로 끝난다(1 소스 실패로 전체 발행을 죽이지 않는 게 옳다).
        # 그래도 상태는 성공이라고 말하지 않는다.
        head += f"> ⚠️ **degraded** — {', '.join(sorted(set(degraded)))}\n\n"
    return head + "\n".join(rows) + "\n"


def load_json_env(var: str) -> dict[str, str]:
    """`{name: value}` JSON 을 담은 파일 경로 env. 미설정/부재면 빈 dict."""
    path = os.environ.get(var, "")
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ────────────────────────────── IO ──────────────────────────────

def main() -> int:
    base = sys.argv[1]
    src_dir = os.path.join(base, "sources")

    envelopes = {
        name: read_envelope(os.path.join(src_dir, f"{name}.json"))
        for name in SOURCE_NAMES
    }
    index = build_index(
        envelopes,
        load_json_env("MAC_INDEX_OUTCOMES"),
        load_json_env("MAC_INDEX_SERVED_FROM"),
        datetime.now(timezone.utc),
        coverage={
            name: cov
            for name in SOURCE_NAMES
            if (cov := read_coverage(name)) is not None
        },
    )

    with open(os.path.join(base, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, separators=(",", ":"))

    summary = render_summary(index)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
