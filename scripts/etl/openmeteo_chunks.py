"""Open-Meteo 청크 수집 정책 — 커버리지 공시 + 스로틀 시 조기 포기.

**왜 워크플로 heredoc 밖 tracked 모듈인가.** 이 정책은 `data-collect-hourly.yml`
안에서 네 번(weather/marine/gas/pollen) 복붙돼 있었고, 네 사본이 같은 두 결함을
공유했다 (2026-09-04 실측, run 33862867992):

① **부분이 완전본으로 위장한다.** 실패한 청크가 조용히 사라져 발행물에는
   "완전한 격자" 한 장만 남는다. 같은 09:18:08Z run 에서 pollen 은 0 점으로
   *붕괴*해 `if pts:` 가드가 잡았지만, 가스 3종은 2196→1980 점(−10%)으로
   *감쇠*해 그대로 발행됐다. truthy 검사는 1 점만 살아도 통과한다 —
   붕괴는 잡히고 감쇠는 통과한다.

② **429 에 즉시 재시도한다.** 스로틀에 대한 재시도는 완화가 아니라 **가중**이고,
   청크마다 3회 × ~2.4s 가 스텝 예산 10분을 전부 태운다. 그러면 뒤에 오는 블록은
   구조적으로 매번 시도조차 못 한다 — 희생자는 "느린 제공자"가 아니라
   "마지막 블록"이다(pollen 이 가스 뒤라 매번 굶었다).

그래서 이 모듈은 세 가지만 한다: 청크를 가져오고, **몇 개가 실패했는지 세어
돌려주고**(호출자가 그대로 발행물에 싣는다), 스로틀이 이어지면 남은 청크를
포기한다.

**커버리지 하한은 걸지 않는다.** 근거가 없고, 임계값을 지어내면 그 자체가 새
거짓말이 된다. 부분 격자는 발행하되 *부분이라고 말한다*. 공시가 먼저고 차단은
그 다음이다 — 순서를 뒤집으면(공시 없이 조기 중단만) 부분 격자를 완전본으로
발행하는 쪽으로 오히려 악화된다.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

# 스로틀 신호로 취급하는 HTTP 코드. 429 = Too Many Requests(실측 원인),
# 503 = 업스트림이 과부하를 알리는 코드 — 둘 다 "빨리 다시 오라"는 뜻이 아니다.
THROTTLE_CODES = frozenset({429, 503})

# 연속 몇 청크가 스로틀이면 이 블록을 접는가. 1 로 잡으면 일시적 429 한 번에
# 격자 전체를 버리고, 무한이면 예산을 다 태운다(그게 지금까지 벌어진 일이다).
# 실측 사고에서는 청크 33~41 이 **연속 9개** 스로틀이었다 — 3 은 그 아래이면서
# 단발 스로틀은 통과시키는 값이다.
GIVE_UP_AFTER_CONSECUTIVE_THROTTLES = 3

# 분당 한도(coord 가중 ~600/분)에 걸린 429 만은 예외적으로 기다렸다 재시도한다 —
# 2026-09-05 force run 실측: 오늘 사용량 0 에서 3청크(600좌표) 직후부터 429,
# 본문이 "Minutely API request limit exceeded. Please try again in one minute."
# 라고 직접 말했다. 일일 소진(Hourly/Daily/무본문)은 기다려도 안 풀리므로 기존
# 포기 로직 그대로다 — 본문이 Minutely 라고 말할 때만 61s 대기가 완화가 된다.
# 블록당 상한: gas 2,376좌표 ≈ 분당창 4개 = 대기 3회가 실측 최악. 4 로 잡으면
# 실측 경로를 덮으면서 스텝 타임아웃(15분) 안에 남는다.
MINUTELY_WAIT_SECONDS = 61
MINUTELY_WAIT_BUDGET_PER_BLOCK = 4


def _default_opener(url: str) -> bytes:
    return urllib.request.urlopen(url, timeout=15).read()


def _throttle_body(exc) -> str:
    """스로틀 응답 본문 — 어느 한도인지(Minutely/Hourly/Daily) 판정 근거."""
    try:
        return exc.read().decode("utf-8", "replace")[:160].strip()
    except Exception:  # noqa: BLE001 — 본문은 보너스, 실패해도 판정 불변
        return ""


def fetch_chunks(
    label: str,
    urls,
    *,
    attempts: int = 3,
    delay: float = 0.5,
    opener=_default_opener,
    sleep=time.sleep,
    log=None,
):
    """청크 URL 을 순회하며 파싱된 payload 목록과 커버리지를 함께 돌려준다.

    Returns:
        (payloads, coverage) — coverage 는 발행물에 그대로 싣는 dict:
        `nChunksTotal` / `nChunksOk` / `nChunksFailed` / `throttled`.
        실패를 *세기만* 하면 안 되고 소비자가 **보게** 해야 부분이 완전본으로
        위장하지 못한다.
    """
    urls = list(urls)
    if log is None:
        def log(message: str) -> None:
            print(message, file=sys.stderr)

    payloads = []
    ok = 0
    failed = 0
    consecutive_throttles = 0
    gave_up = False
    minutely_waits = 0

    for index, url in enumerate(urls):
        if gave_up:
            # 포기한 뒤의 청크도 "실패"다 — 시도조차 안 했다는 사실이
            # 커버리지에서 사라지면 그게 다시 무음이 된다.
            failed += 1
            continue

        attempt = 0
        while attempt < attempts:
            try:
                payloads.append(json.loads(opener(url)))
                ok += 1
                consecutive_throttles = 0
                break
            except Exception as exc:  # noqa: BLE001 — 어떤 실패든 커버리지에 남긴다
                code = getattr(exc, "code", None)
                if code in THROTTLE_CODES:
                    # 응답 본문을 남긴다 — Open-Meteo 429 본문이 어느 한도인지
                    # (Minutely/Hourly/Daily) 말해 준다. 2026-09-04 사고에서 이게
                    # 없어서 "요청 수 축소"라는 빗나간 수리(#24)가 나왔다.
                    body = _throttle_body(exc)
                    if "Minutely" in body and minutely_waits < MINUTELY_WAIT_BUDGET_PER_BLOCK:
                        # 분당 한도만은 기다리면 풀린다 — 같은 청크를 재시도하고
                        # attempt 도 소모하지 않는다 (스로틀 카운터도 무변).
                        minutely_waits += 1
                        log(
                            f"  {label} chunk {index}: HTTP {code} — 분당 한도, "
                            f"{MINUTELY_WAIT_SECONDS}s 대기 후 같은 청크 재시도 "
                            f"({minutely_waits}/{MINUTELY_WAIT_BUDGET_PER_BLOCK})"
                        )
                        sleep(MINUTELY_WAIT_SECONDS)
                        continue
                    consecutive_throttles += 1
                    failed += 1
                    suffix = f" · body: {body}" if body else ""
                    log(f"  {label} chunk {index}: HTTP {code} — 스로틀, 재시도하지 않는다(가중){suffix}")
                    break
                log(f"  {label} chunk {index} attempt {attempt}: {exc}")
                attempt += 1
                if attempt == attempts:
                    failed += 1
                    consecutive_throttles = 0
                else:
                    sleep(2 * attempt)

        if consecutive_throttles >= GIVE_UP_AFTER_CONSECUTIVE_THROTTLES:
            gave_up = True
            log(
                f"  {label}: 연속 {consecutive_throttles} 청크 스로틀 — 남은 "
                f"{len(urls) - index - 1} 청크를 포기하고 예산을 뒤 블록에 넘긴다"
            )
            continue

        if index + 1 < len(urls):
            sleep(delay)

    coverage = {
        "nChunksTotal": len(urls),
        "nChunksOk": ok,
        "nChunksFailed": failed,
        "throttled": gave_up,
    }
    return payloads, coverage


def coverage_note(label: str, coverage: dict) -> str | None:
    """부분 수집을 GitHub annotation 한 줄로 드러낸다.

    격리가 침묵이 되면 안 된다(#20 과 같은 이유). 전량 성공이면 `None` —
    정상까지 경고로 만들면 경고가 배경 소음이 되어 아무도 안 본다.
    """
    if coverage["nChunksFailed"] == 0:
        return None
    reason = " (스로틀로 조기 중단)" if coverage["throttled"] else ""
    return (
        f"::warning title={label} partial::"
        f"{coverage['nChunksOk']}/{coverage['nChunksTotal']} 청크만 수집됐다{reason} — "
        f"발행물의 nChunksFailed 를 보라. 부분 격자가 완전본을 덮었을 수 있다"
    )
