"""hf_publish.py prune 로직 시험 (DQSS W5 — 사본 발산 봉합, 2026-09-03).

네트워크 호출 0 — `_stale_files` 순수 함수만 시험한다 (HfApi.list_repo_files 자체는
호출하지 않는다, scripts/test_publish_contracts.py 와 동일한 fake-object 관례).
재시도·batch·--schema auto 는 2026-10 조직 재편에서 추가 (hf_publish.py 유일본화).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import hf_publish as hp

CUTOFF = datetime(2026, 9, 3, tzinfo=timezone.utc)


def test_pm25_timeline_frame_older_than_cutoff_is_stale():
    files = ["aq-data/timeline/pm25-2026082300.json"]
    stale, skipped_bad_ts, skipped_backfill = hp._stale_files(files, "aq-data/timeline/", CUTOFF)
    assert stale == files
    assert skipped_bad_ts == 0
    assert skipped_backfill == 0


def test_pm25_timeline_frame_newer_than_cutoff_is_kept():
    files = ["aq-data/timeline/pm25-2026090400.json"]
    stale, _, _ = hp._stale_files(files, "aq-data/timeline/", CUTOFF)
    assert stale == []


def test_openaq_shadow_gz_slot_older_than_cutoff_is_stale():
    # 기존 정규식(pm25- 접두사, .json 확장자 고정)은 이 shadow 슬롯 파일명을
    # 전혀 매치하지 못해 영구 미삭제(무한 누적) 상태였다 — W5 핵심 수정.
    files = ["openaq-shadow/openaq-2026082300.json.gz"]
    stale, _, _ = hp._stale_files(files, "openaq-shadow/", CUTOFF)
    assert stale == files


def test_sensor_community_gz_slot_matches_generalized_prefix():
    files = ["sensor-community-shadow/sensor-community-2026082300.json.gz"]
    stale, _, _ = hp._stale_files(files, "sensor-community-shadow/", CUTOFF)
    assert stale == files


def test_backfill_file_never_pruned_even_if_pattern_would_match():
    # 실제 tag 포맷은 8자리(YYYYMMDD-YYYYMMDD)라 \d{10} 과 우연히도 안 맞지만,
    # 포맷 드리프트에 대비해 파일명에 "backfill" 이 있으면 무조건 보존한다.
    files = ["openaq-shadow/openaq-backfill-kr-2020010100-2020020100.json.gz"]
    stale, skipped_bad_ts, skipped_backfill = hp._stale_files(files, "openaq-shadow/", CUTOFF)
    assert stale == []
    assert skipped_backfill == 1
    assert skipped_bad_ts == 0


def test_backfill_marker_checked_against_basename_not_full_path():
    # prefix 자체가 우연히 "backfill" 을 포함해도(경로 상 상위 디렉터리) 판정
    # 대상은 파일명(Path(f).name)만 — 모노레포 사본과 판정 기준 동일화.
    files = ["openaq-backfill-archive/openaq-2020010100.json.gz"]
    stale, _, skipped_backfill = hp._stale_files(files, "openaq-backfill-archive/", CUTOFF)
    # 파일명 자체(openaq-2020010100.json.gz)엔 "backfill" 이 없으므로 정상 prune 대상.
    assert stale == files
    assert skipped_backfill == 0


def test_manifest_without_timestamp_is_never_touched():
    files = ["aq-data/timeline/manifest.json"]
    stale, _, _ = hp._stale_files(files, "aq-data/timeline/", CUTOFF)
    assert stale == []


def test_file_outside_prefix_is_ignored():
    files = ["wind-data/wind-2026082300.json"]
    stale, _, _ = hp._stale_files(files, "aq-data/timeline/", CUTOFF)
    assert stale == []


def test_ts_name_re_rejects_invalid_calendar_digits():
    # 10자리 숫자지만 유효한 YYYYMMDDHH 가 아님(월=99) — 일반화된 정규식이
    # 매치는 하되 strptime 이 ValueError 로 죽는 경로. 가드 없으면 prune 전체가
    # 크래시해 일일 정리가 무기한 실패한다 (모노레포 PR #1315 이식).
    files = ["aq-data/timeline/pm25-2026992012.json"]
    stale, skipped_bad_ts, skipped_backfill = hp._stale_files(files, "aq-data/timeline/", CUTOFF)
    assert stale == []
    assert skipped_bad_ts == 1
    assert skipped_backfill == 0


def test_prune_dry_run_lists_candidates_without_deleting(capsys):
    calls = []

    class _FakeApi:
        def list_repo_files(self, repo_id, repo_type):
            return ["aq-data/timeline/pm25-2026010100.json"]

        def create_commit(self, **kwargs):
            calls.append(kwargs)

    hp.prune(_FakeApi(), "aq-data/timeline", keep_days=1, dry_run=True)
    out = capsys.readouterr().out
    assert "would delete 1 files" in out
    assert "pm25-2026010100.json" in out
    assert calls == []  # dry-run 은 create_commit 을 절대 호출하지 않는다


def test_prune_without_dry_run_calls_create_commit_once():
    calls = []

    class _FakeApi:
        def list_repo_files(self, repo_id, repo_type):
            return ["aq-data/timeline/pm25-2026010100.json"]

        def create_commit(self, **kwargs):
            calls.append(kwargs)

    hp.prune(_FakeApi(), "aq-data/timeline", keep_days=1, dry_run=False)
    assert len(calls) == 1
    assert len(calls[0]["operations"]) == 1


def test_prune_with_nothing_stale_does_not_call_create_commit():
    calls = []

    class _FakeApi:
        def list_repo_files(self, repo_id, repo_type):
            return ["aq-data/timeline/pm25-2099010100.json"]  # 미래 — 항상 최신

        def create_commit(self, **kwargs):
            calls.append(kwargs)

    hp.prune(_FakeApi(), "aq-data/timeline", keep_days=30, dry_run=False)
    assert calls == []


def test_prune_with_invalid_calendar_digits_does_not_crash_and_skips(capsys):
    # 통합 경로: prune() 이 ValueError 를 삼키고 skip 카운트를 로깅하는지.
    calls = []

    class _FakeApi:
        def list_repo_files(self, repo_id, repo_type):
            return ["aq-data/timeline/pm25-2026992012.json"]

        def create_commit(self, **kwargs):
            calls.append(kwargs)

    hp.prune(_FakeApi(), "aq-data/timeline", keep_days=30, dry_run=False)
    assert calls == []  # 크래시 없이 조용히 skip
    out = capsys.readouterr().out
    assert "유효 시각이 아님" in out


def test_cli_main_threads_dry_run_flag_through_to_prune(monkeypatch, capsys):
    # 실 main() 배선 검증: sys.argv → argparse → prune(dry_run=...) 까지 실제로
    # 흐르는지. 네트워크는 _api() 를 FakeApi 로 교체해 격리 (모노레포 PR #1315 이식).
    class _FakeApi:
        def list_repo_files(self, repo_id, repo_type):
            return ["aq-data/timeline/pm25-2026010100.json"]

        def create_commit(self, **kwargs):
            raise AssertionError("dry-run 인데 create_commit 이 호출됐다")

    monkeypatch.setattr(hp, "_api", lambda: _FakeApi())
    monkeypatch.setattr(
        "sys.argv",
        ["hf_publish.py", "prune", "--prefix", "aq-data/timeline", "--dry-run"],
    )

    hp.main()

    out = capsys.readouterr().out
    assert "would delete" in out


# ── 계약 게이트 배선 (contracts/current-aq-grid.v1) ──────────────────────────


def test_cli_upload_runs_contract_check_before_touching_the_remote(monkeypatch, tmp_path):
    # 실 main() 배선 검증: --schema 가 argparse → check_contract 로 흐르고,
    # **원격을 건드리기 전에** 돈다는 것. 순서가 뒤집히면 위반본이 올라간 뒤에
    # 죽으므로 "옛 버전이 그대로 남는다" 는 보장이 깨진다.
    calls = []

    class _FakeApi:
        def create_repo(self, **kwargs):
            calls.append("create_repo")

        def list_repo_files(self, **kwargs):
            return ["README.md"]

        def upload_file(self, **kwargs):
            calls.append("upload_file")

    src = tmp_path / "current-pm25-grid.json"
    src.write_text("{}", encoding="utf-8")  # 계약 위반 (필수 필드 전무)

    monkeypatch.setattr(hp, "_api", lambda: _FakeApi())
    monkeypatch.setattr(
        "sys.argv",
        ["hf_publish.py", "upload", "--src", str(src), "--dest", "aq-data/x.json",
         "--schema", "current-aq-grid.v1"],
    )

    with pytest.raises(SystemExit) as e:
        hp.main()

    assert e.value.code == 1
    assert calls == []  # 원격 호출이 하나도 없어야 한다


def test_cli_upload_without_schema_skips_the_contract_gate(monkeypatch, tmp_path):
    # 계약 대상이 아닌 산출물(o3/no2/co/pollen 등)은 --schema 없이 올라간다.
    # 게이트가 모든 업로드를 무조건 막으면 정상 발행이 죽는다.
    uploaded = []

    class _FakeApi:
        def create_repo(self, **kwargs):
            pass

        def list_repo_files(self, **kwargs):
            return ["README.md"]

        def upload_file(self, path_in_repo, **kwargs):
            uploaded.append(path_in_repo)

    src = tmp_path / "current-o3-grid.json"
    src.write_text('{"anything": true}', encoding="utf-8")

    monkeypatch.setattr(hp, "_api", lambda: _FakeApi())
    monkeypatch.setattr(
        "sys.argv",
        ["hf_publish.py", "upload", "--src", str(src), "--dest", "aq-data/current-o3-grid.json"],
    )

    hp.main()

    assert uploaded == ["aq-data/current-o3-grid.json"]


# ── 재시도 (_call) ───────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status: int, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}


class _HttpErr(Exception):
    def __init__(self, status: int, headers: dict | None = None):
        super().__init__(f"HTTP {status}")
        self.response = _Resp(status, headers)


def test_call_retries_transient_status_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(hp, "_sleep", sleeps.append)
    attempts = iter([_HttpErr(503), _HttpErr(429), "ok"])

    def fn():
        v = next(attempts)
        if isinstance(v, Exception):
            raise v
        return v

    assert hp._call("x", fn) == "ok"
    assert len(sleeps) == 2


def test_call_honours_retry_after_header(monkeypatch):
    sleeps = []
    monkeypatch.setattr(hp, "_sleep", sleeps.append)
    attempts = iter([_HttpErr(429, {"Retry-After": "7"}), "ok"])

    def fn():
        v = next(attempts)
        if isinstance(v, Exception):
            raise v
        return v

    hp._call("x", fn)
    assert sleeps == [7.0]


def test_call_does_not_retry_permanent_errors(monkeypatch):
    monkeypatch.setattr(hp, "_sleep", lambda s: pytest.fail("영구 오류는 재시도하면 안 된다"))
    calls = []

    def fn():
        calls.append(1)
        raise _HttpErr(401)

    with pytest.raises(_HttpErr):
        hp._call("x", fn)
    assert len(calls) == 1


def test_call_treats_network_exception_names_as_transient(monkeypatch):
    # requests/httpx 예외를 import 하지 않고 이름으로 판정한다 — 버전 무관.
    monkeypatch.setattr(hp, "_sleep", lambda s: None)

    class ReadTimeout(Exception):
        pass

    attempts = iter([ReadTimeout("slow"), "ok"])

    def fn():
        v = next(attempts)
        if isinstance(v, Exception):
            raise v
        return v

    assert hp._call("x", fn) == "ok"


def test_call_exits_75_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(hp, "_sleep", lambda s: None)
    calls = []

    def fn():
        calls.append(1)
        raise _HttpErr(502)

    sleeps = []
    monkeypatch.setattr(hp, "_sleep", sleeps.append)
    with pytest.raises(SystemExit) as e:
        hp._call("x", fn)
    assert e.value.code == hp.EX_TEMPFAIL == 75
    assert len(calls) == hp.RETRY_ATTEMPTS
    assert len(sleeps) == hp.RETRY_ATTEMPTS - 1
    assert all(s <= hp.RETRY_MAX_DELAY + 1 for s in sleeps)


@pytest.mark.parametrize("header, expected", [
    ("3600", 60.0),      # 상한으로 자른다 — VM 유닛 타임아웃 전에 끝나야 한다
    ("nan", None),       # 비유한값은 무시하고 지수 백오프로
    ("inf", None),
    ("-5", None),
    ("Wed, 21 Oct 2026 07:28:00 GMT", None),  # HTTP-date 형식 — 지수 백오프로
])
def test_retry_after_is_clamped_and_junk_ignored(header, expected):
    assert hp._retry_after(_HttpErr(429, {"Retry-After": header})) == expected


def test_real_hf_http_error_status_is_read(monkeypatch):
    # 손으로 만든 가짜가 아니라 실제 huggingface_hub 예외로 `.response.status_code`
    # 가정을 고정한다 (1.x = httpx.Response).
    errors = pytest.importorskip("huggingface_hub.errors")
    httpx = pytest.importorskip("httpx")
    req = httpx.Request("POST", "https://huggingface.co/api/x")
    transient = errors.HfHubHTTPError("boom", response=httpx.Response(503, request=req))
    permanent = errors.HfHubHTTPError("nope", response=httpx.Response(403, request=req))
    assert hp._is_transient(transient)
    assert not hp._is_transient(permanent)


# ── batch (커밋 1개) ─────────────────────────────────────────────────────────


class _BatchApi:
    def __init__(self):
        self.calls = []

    def create_repo(self, **kwargs):
        self.calls.append("create_repo")

    def list_repo_files(self, **kwargs):
        return ["README.md"]

    def create_commit(self, **kwargs):
        self.calls.append(("create_commit", kwargs))


def test_cli_batch_uploads_all_files_in_one_commit(monkeypatch, tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text('{"x": 1}', encoding="utf-8")
    b.write_text('{"y": 2}', encoding="utf-8")
    api = _BatchApi()
    monkeypatch.setattr(hp, "_api", lambda: api)
    monkeypatch.setattr("sys.argv", [
        "hf_publish.py", "batch", "--map", f"{a}=aq-data/a.json", "--map", f"{b}=aq-data/b.json",
    ])

    hp.main()

    commits = [c for c in api.calls if isinstance(c, tuple)]
    assert len(commits) == 1
    ops = commits[0][1]["operations"]
    assert [op.path_in_repo for op in ops] == ["aq-data/a.json", "aq-data/b.json"]


def test_cli_batch_contract_violation_touches_nothing(monkeypatch, tmp_path):
    # 하나라도 위반이면 아무것도 올리지 않는다 — 원격 호출 0.
    schema = tmp_path / "toy.v1.schema.json"
    schema.write_text('{"type": "object", "required": ["n"]}', encoding="utf-8")
    good = tmp_path / "good.json"
    bad = tmp_path / "bad.json"
    good.write_text('{"n": 1}', encoding="utf-8")
    bad.write_text('{"m": 1}', encoding="utf-8")
    hp.check_contract(str(good), str(schema))  # 정상본은 단독으로 통과한다
    api = _BatchApi()
    monkeypatch.setattr(hp, "_api", lambda: api)
    monkeypatch.setattr("sys.argv", [
        "hf_publish.py", "batch", "--schema", str(schema),
        "--map", f"{good}=a.json", "--map", f"{bad}=b.json",
    ])

    with pytest.raises(SystemExit) as e:
        hp.main()
    assert e.value.code == 1
    assert api.calls == []


@pytest.mark.parametrize("bad_map", ["no-separator", "=dest.json", "src.json="])
def test_cli_batch_rejects_malformed_map(monkeypatch, bad_map):
    api = _BatchApi()
    monkeypatch.setattr(hp, "_api", lambda: api)
    monkeypatch.setattr("sys.argv", ["hf_publish.py", "batch", "--map", bad_map])
    with pytest.raises(SystemExit) as e:
        hp.main()
    assert e.value.code == 1
    assert api.calls == []


def test_cli_batch_rejects_duplicate_dest_and_empty_files(monkeypatch, tmp_path):
    a = tmp_path / "a.json"
    a.write_text("{}", encoding="utf-8")
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    for argv in (
        ["--map", f"{a}=same.json", "--map", f"{a}=same.json"],
        ["--map", f"{empty}=e.json"],
    ):
        api = _BatchApi()
        monkeypatch.setattr(hp, "_api", lambda api=api: api)
        monkeypatch.setattr("sys.argv", ["hf_publish.py", "batch", *argv])
        with pytest.raises(SystemExit):
            hp.main()
        assert api.calls == []


# ── --schema auto ───────────────────────────────────────────────────────────


def test_schema_auto_resolves_contract_from_schema_version(monkeypatch, tmp_path, capsys):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "toy.v1.schema.json").write_text(
        '{"type": "object", "required": ["schema_version", "n"],'
        ' "properties": {"n": {"type": "integer"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(hp._validator(), "CONTRACTS_DIR", contracts)
    ok = tmp_path / "ok.json"
    ok.write_text('{"schema_version": "toy.v1", "n": 3}', encoding="utf-8")
    hp.check_contract(str(ok), "auto")
    assert "contract ok" in capsys.readouterr().out

    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": "toy.v1", "n": "three"}', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        hp.check_contract(str(bad), "auto")
    assert e.value.code == 1


def test_schema_auto_rejects_numeric_schemaVersion(tmp_path):
    # 수집 산출물의 schemaVersion("1.0")은 계약 이름이 아니다 — auto 로 해석하면
    # 안 되고, 이름을 명시하라는 오류로 죽어야 한다.
    payload = tmp_path / "grid.json"
    payload.write_text('{"schemaVersion": "1.0"}', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        hp.check_contract(str(payload), "auto")
    assert e.value.code == 1


@pytest.mark.parametrize("version", ["/etc/passwd", "../contracts/x.v1", "sub/x.v1", ".hidden"])
def test_schema_auto_rejects_path_like_schema_version(tmp_path, version):
    # 페이로드가 경로를 신고해 자기 스키마를 고를 수 없어야 한다.
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"schema_version": version}), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        hp.check_contract(str(payload), "auto")
    assert e.value.code == 1


def test_cli_batch_rejects_bad_dest_before_touching_the_remote(monkeypatch, tmp_path):
    a = tmp_path / "a.json"
    a.write_text("{}", encoding="utf-8")
    api = _BatchApi()
    monkeypatch.setattr(hp, "_api", lambda: api)
    monkeypatch.setattr("sys.argv", ["hf_publish.py", "batch", "--map", f"{a}=../escape.json"])
    with pytest.raises(SystemExit) as e:
        hp.main()
    assert e.value.code == 1
    assert api.calls == []
