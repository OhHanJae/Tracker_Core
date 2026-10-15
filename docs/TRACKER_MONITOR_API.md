# Tracker Monitor 추가 API

2026-10-15. 기존 UTF-8 JSON Lines 요청/응답과 Core 설정 스키마를 유지한다.
Monitor 연결/종료는 운전 허가, 안전 정지, tracking 시작/정지 조건이 아니다.

## 조회

`monitor.capabilities`는 `schema_version:1`, 지원 `commands`, `map_version:"v4.2"`,
기본 `alarm_retention_days:30`, `endpoints`, `allow_remote_process_control`,
`unsupported`를 반환한다. `endpoints.ptm/vision/plc`는 `enabled`, `tcp:{host,port}`,
`http:{host,port}`다. 실제 process runtime TCP 포트가 알려지면 해당 값을 사용한다.
서버의 loopback 주소는 원격 패널에서 서버 주소로 해석해야 하며 패널 자신에게 연결하지 않는다.
Camera 2 PLC 알람 및 새 Vision frame/calibration 계약은 미지원으로 명시한다.

`monitor.snapshot`은 기존 상태 키에 아래 키를 추가한다. 상태는 캐시에서 읽으며
장비 TCP/HTTP probe나 새 shared-memory 읽기를 수행하지 않는다.

- `monitor`: `schema_version`, UTC `captured_at`, 마지막 운전 주기의 `state_age_ms`, `endpoints`.
- `devices.<id>.age_ms`: 마지막 성공 장비 응답 나이. 미수신은 null.
- `plc_memory`: `connected`, `disabled`, `name`, `byte_order:"little"`, `refresh_ms:500`,
  `header`, `error`, `read`, `write`. 각 방향은 `base_address`, `offset`,
  `word_count`, 전체 `words`, 캐시 `age_ms`다. `read.source_age_ms`는 실제 Gateway
  성공 읽기 시각의 나이다. read는 마지막 성공 입력, write는 마지막 Core commit 값이다.
  이것을 PLC에 실제 전달/ACK된 값이라고 간주하지 않는다. Gateway ACK는 기존 `plc.write`를 확인한다.
- `alarm_history`: 현재 알람과 기록 상태. 상세 형식은 아래와 같다.

운전 worker의 `runtime.snapshot`도 같은 추가 키를 반환하여 관리 서버의 기존
300ms worker polling에서 최신 캐시를 전달한다. Main Core가 오래되거나 정지되면
관리 서버는 `plc_memory.connected:false`와 상태 불가 이유를 반환한다.

## 알람

`alarms.active`는 현재 원인 또는 latch가 남은 사건을 반환한다.
`alarms.history`/`alarms.export`는 동일한 페이지 계약을 사용한다. export 전체를
얻으려면 `next_cursor`가 null이 될 때까지 조회한다. Monitor에서 JSON/CSV 파일로 저장한다.

필터 params: `limit` 1~500(기본100), `cursor`, `device`, `severity` (`Fault`/`Warning`),
`status` (`active`/`latched`/`recovered`/`unknown`), `from`, `to`, `search`.
from/to는 timezone 포함 ISO8601 발생 시각이고 내부 UTC로 변환한다.

응답: `schema_version:1`, worker `session_id`, `entries`, 필터 전체 `total`, `next_cursor`, `recording`.
active의 session_id 변경 시 기존 활성 사건 동기화를 새 발생으로 소리내지 않는다.
`recording`은 `running`, `retention_days`, `last_error`, `dropped_updates`, `pending_updates`다.
관리 서버에서 worker가 정지되어도 디스크 이력을 읽을 수 있다. 이 경우 기록 중이 아니라는
상태와 worker 오류를 명시하며 최신 active 조회는 실패한다.

사건 필드: `occurrence_id`, `alarm_key`, `session_id`, `device`, `severity`,
장치 대표 `code`, `relative_address`, `bit`, `message`, `description`, `clear_policy`,
`map_version`, `occurred_at`, `recovered_at`, `closed_at`, `cause_active`, `latched`,
`suppressed`, `cause_observed`, `status`, `reset_result`. 영속 조회 행에는 페이지용 `cursor`도 있다.
장치 대표 코드는 상세 bit마다 새로 정의한 코드가 아니다.
`reset_result`는 마지막 reset 요청의 `timestamp`, `cause_still_active`, `latch_cleared`다.

PTM Pan/Tilt는 D+62/63 한 사건으로 합산한다. Summary Word는 별도 사건을 만들지 않는다.
바이패스로 기존 내부/출력 word가 0이 되어도 기존 사건의 실제 복구를 추정하지 않는다.
이때 `cause_observed:false`이며 cause/latch는 마지막 확인 값을 보존한다.
원인이 해제된 latch는 `status:latched`, 원인 없는 수동 reset 완료는 `recovered`다.
재발은 새 발생 ID를 갖는다. worker 재시작 이전 미종결 사건은 `unknown`, 원인/latch null로
보존하며 복구 시간을 새로 만들지 않는다. 현재 active 목록과 이전 unknown 이력을 구분한다.

저장 위치는 기존 logs_dir 아래 `alarms.sqlite3`(SQLite WAL)이다. 제어 주기에서는
상태 전이만 비교하여 유한 queue에 넣으며 전용 thread가 쓰기/정리/조회한다.
일시적인 디스크 쓰기 실패는 최대 4096개 사건의 재시도 버퍼에 보존하고 다시 시도한다.
종료 시 queue와 대기 버퍼를 비운 뒤 저장 thread를 종료하며, 지속적인 장애로
제한 시간 안에 종료하지 못하면 pending/loss 오류를 노출한다. read-only 조회 연결은 매 요청 닫는다.
30일 지난 recovered/unknown 사건을 정리하고 현재 active/latched 사건은 유지한다.
디스크/queue 실패는 recording 오류와 누락 횟수로 노출한다. 오류 표시가 있으면
보관 기간과 전체 기록이 충족되었다고 주장하지 않는다.

## 설정 적용

`monitor.config.apply` 요청은 아래와 같다.

```json
{"patch":{"system":{"memory_warn_percent":85}},"expected":{"system.memory_warn_percent":90}}
```

expected는 변경하는 모든 leaf 또는 list의 점 구분 경로와 마지막으로 읽은 값을 포함한다.
worker는 마지막 cycle이 2초 이내이고 실제 loop가 실행 중인지 확인하고,
현재 PLC RUN_ENABLE, PTM 이동 또는 safe-stop 진행 중이면 거절한다.
최신 디스크 설정과 expected를 비교한 뒤 기존 부분 patch validation과 persistence를 사용한다.
JSON 숫자는 정수/실수 표현이 같으면 동등하게 비교하지만 bool과 숫자는 구분한다.
성공 응답 result는 전체 설정이며 클라이언트는 다시 읽어 저장과 반영 상태를 확인한다.
기존 `update_config`의 계약은 바꾸지 않는다.

실패 code: `STATE_UNAVAILABLE`, `RUNNING`, `BAD_REQUEST`(expected 누락 포함),
`CONFLICT`(동시 수정; `paths` 배열), 기존 validation 오류.
관리 서버는 worker에 전달하고 성공 후 자체 설정을 다시 읽는다.
관리 서버 재취합 실패가 나면 worker의 저장 여부가 불명확할 수 있으므로 재조회한다.
이 검사는 요청 시점 상태 검사다. 이후 PLC 신호 변경까지 막는 원자적 interlock이나
새 PLC 설정 트랜잭션을 구현한 것으로 표시하지 않는다.

## 검증

`tests/test_alarm_history.py`, `tests/test_monitor_api.py`는 cache 조회, 완전한 PLC 배열,
장치 probe 부재, 발생/복구/latch/reset/재발, 바이패스, Pan/Tilt 합산, restart unknown,
cursor/filter, storage failure, 설정 stopped-state/conflict 조건을 확인한다.
RDK 및 실제 PLC/PTM와 패널의 운전 검증은 별도 수행한다.
