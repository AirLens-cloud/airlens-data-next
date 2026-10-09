# e2-sidecar — OpenAQ / Sensor.Community 시간별 수집기 (airlens-e2 VM)

GitHub Actions 밖에서 도는 유일한 수집 레인. Oracle E2 VM(`airlens-e2`,
Tailscale 접속, 주소는 private 런북)의 systemd 타이머가 매시 HF dataset
`Robeedau/airlens-live`의 `openaq-shadow/`·`sensor-community-shadow/`로 직접
발행한다. 2026-09-02까지 VM에만 존재하던(소스컨트롤 밖) 실물을 sha256 대조로
회수해 편입했다 — **이 디렉터리가 정본, VM은 배포 대상이다.** 수정은 여기서
하고 `./deploy.sh`로 내보낸다. VM에서 직접 고치지 않는다.

## 구성

| 경로 (repo) | 배포 위치 (VM) | 역할 |
|---|---|---|
| `bin/run-global-shadow.sh` | `/usr/local/bin/` | 엔트리 — `<sc\|openaq>` 인자로 deno 수집 → `hf_publish.py` 업로드 → 7일 로컬 trim |
| `bin/run-shadow.sh` | `/usr/local/bin/` | **legacy** (AirKorea shadow, Wave 1 pivot 이전) — 타이머 없음, 참조용 보존 |
| `sidecar/openaq_shadow.ts` | `/opt/airlens/sidecar/` | OpenAQ pm25 수집 (deno, `--allow-net=api.openaq.org`) |
| `sidecar/sensor_community_shadow.ts` | `/opt/airlens/sidecar/` | Sensor.Community 수집 (`--allow-net=data.sensor.community`) |
| `sidecar/airkorea_shadow.ts` · `sidecar/openaq_backfill.ts` | `/opt/airlens/sidecar/` | legacy / one-off 백필 — 타이머 없음 |
| `systemd/airlens-sc-shadow.{service,timer}` | `/etc/systemd/system/` | 매시 **:05** |
| `systemd/airlens-openaq-shadow.{service,timer}` | `/etc/systemd/system/` | 매시 **:15** (1GB 박스라 메모리 피크 겹침 방지 스태거) |
| `sidecar/chatlog_pull.py` | `/opt/airlens/sidecar/` | Field Assistant 대화 로그 수신 — R2 `airlens-chatlog` → 로컬 SQLite (stdlib만, SigV4 자체 서명) |
| `systemd/airlens-chatlog-pull.{service,timer}` | `/etc/systemd/system/` | 매시 **:35** (세 번째 스태거 슬롯) |

### chatlog_pull 설계 요지

생산자는 다른 레포다 — airlens-web의 `workers/assistant/src/persist.ts`가 엣지에서
개인정보를 마스킹한 턴 1건을 JSON 객체 1개로 R2에 떨어뜨린다. 이 스크립트가 그걸
가져와 `/var/lib/airlens/chatlog/chatlog.db`(0600)에 넣는다.

- **전송은 pull 전용** — 이 VM도 워커 오리진도 인바운드를 열지 않는다.
- **삭제는 행 확인 뒤에만.** 객체가 유일본이라, insert가 트랜잭션 밖에서 실패했는데
  delete만 성공하면 조용히 유실된다. insert → commit → 행 재조회 → 그 다음 delete.
- **성공 판정은 종료코드가 아니라 처리 건수.** 객체를 나열해놓고 하나도 저장하지
  못한 채 0으로 끝나는 실행은 `systemctl status`에서 성공처럼 보인다.
- **`PRAGMA secure_delete`는 파일 속성이 아니라 커넥션 설정**이라 매 실행 켠다.
  생성 시 한 번만 켜면 이후 실행엔 적용되지 않고, 그러면 "90일 뒤 파기"는 NULL
  컬럼에 대한 서술일 뿐 옛 텍스트는 해제된 페이지에 남는다. WAL도 쓰지 않는다
  (파기한 텍스트가 체크포인트 전까지 `-wal`에 남는다).
- 보존: 원문 90일 후 NULL + `VACUUM`, 메타데이터는 존치. R2 버퍼는 버킷
  라이프사이클 `expire-7d`(prefix `turn/`)로 자동 소멸 — 타이머가 며칠 죽어도 그
  안에 복구되면 무손실이고, 7일 초과분은 유실을 허용한다(로그이지 원장이 아니다).

## VM 측 전제 (deploy.sh가 만들지 않는 것)

- **시크릿**: `/etc/airlens/hf_token`, `/etc/airlens/openaq_api_key`,
  `/etc/airlens/cf_r2_chatlog` — systemd `LoadCredential`로 주입. **레포에 절대
  편입 금지.** 로테이션은 VM에서 파일 교체만 하면 된다(유닛 재시작 불요 —
  oneshot이 매 슬롯 다시 읽음).
- `cf_r2_chatlog` 형식 (3줄, 0600 root:root):
  ```
  account_id=<Cloudflare 계정 ID>
  access_key_id=<R2 API 토큰의 Access Key ID>
  secret_access_key=<R2 API 토큰의 Secret Access Key>
  ```
  토큰은 **`airlens-chatlog` 버킷 한정 Object Read & Write** 스코프여야 한다.
  같은 계정에 `airlens-models`·`airlens-captures`가 있어서, 계정 스코프 토큰이면
  이 VM이 그것들까지 갖게 된다.
- `/opt/airlens/lib` — `deploy.sh`가 이 레포의 `scripts/etl/hf_publish.py`와 `contracts/`를
  같은 상대 구조로 설치한다. 운영 타이머(`run-global-shadow.sh`)는 레포 클론이 필요 없다.
- `/opt/airlens/repo` — 예전 모노레포 클론. 이제 legacy `run-shadow.sh` + `airkorea_shadow.ts`
  (타이머 없음)만 의존한다. 지우면 그 둘이 깨진다 — 레거시를 버릴 때 함께 제거한다.
- `/opt/airlens/venv` — huggingface_hub 설치된 파이썬 venv.
- deno (`/usr/local/bin` PATH), `/var/lib/airlens/{shadow,hf-home}` 쓰기 경로.

## 접속 / 배포 / 검증

```bash
# 접속 (Tailscale SSH — 로컬 rtk 훅 회피를 위해 command ssh 권장)
command ssh -i "$E2_SSH_KEY" "$E2_HOST"

# 배포 (멱등) — E2_HOST·E2_SSH_KEY 는 private 런북(AirLens-cloud/.github-private) 참조
./deploy.sh

# 검증 1 — 타이머 살아있나
command ssh -i "$E2_SSH_KEY" "$E2_HOST" 'systemctl list-timers airlens-*'

# 검증 2 — 실제 발행됐나 (green≠working: 산출물로 본다)
# 다음 정시 슬롯 후 HF openaq-shadow/ 최신 파일 타임스탬프 확인.
# 상시 감시는 .github/workflows/e2-shadow-freshness.yml (hosted, 6h) 담당.
```

## 감시 독립성

freshness probe(`e2-shadow-freshness.yml`)는 **hosted 러너에 남긴다** — E2가
죽으면 E2 위의 무엇도 그 사실을 보고할 수 없기 때문. 이 원칙은 이 VM으로
잡을 더 옮길 때도 유지한다.
