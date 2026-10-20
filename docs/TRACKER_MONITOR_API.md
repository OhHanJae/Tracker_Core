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

### 레시피·체결점 컨텍스트 (2026-10-15)

기존 `status`와 `monitor.snapshot`/worker `runtime.snapshot`의 `runtime`에 아래 읽기 전용 필드를 추가한다.
캐시를 읽어 구성하며, 조회 때문에 장치 I/O·이동·Vision 지정/시뮬레이션 명령을 실행하지 않는다.

```json
{
  "instance_id": "1d6adfd11b234d7eb14596ef35b4bfac",
  "target_context": {
    "schema_version": 1,
    "requested": {"recipe_id": 3, "point_id": 4, "command_code": 1, "command_seq": 0},
    "applied": {"recipe_id": 1, "point_id": 2, "ptm_bypassed": false}
  }
}
```

- `instance_id`는 `CoreApplication` 생성 시 한 번 생성한 UUID4 hex다. 같은 인스턴스의 조회에서는 유지되며 Core 재시작 경계를 구분한다. 개별 작업/사이클 ID나 command_seq의 고유성을 보장하지 않는다.
- `requested`는 마지막 캐시 PLC 입력이다. 명령 1(`TARGET_APPLY_MOVE`)/5(`NUTRUNNER_DATA_REQUEST`)이며 recipe/point가 각각 1~65535이면 반환하고, 나머지는 null이다. 수락/검증/완료를 뜻하지 않는다. `command_seq=0`도 유효하며 같은 신호가 유지되면 계속 보인다.
- `applied`는 기존 `runtime.active_recipe_id/active_point_id`가 유효할 때 같은 값을 반환하고, 없으면 null이다. 기존 성공/초기화 시점은 바꾸지 않는다. `ptm_bypassed`는 조회 당시 Core의 PTM 바이패스 캐시다. true이면 실제 이동 확인으로 해석하지 않는다.
- 요청과 적용이 서로 다른 체결점일 수 있다. 처리 결과는 기존 최상위 `command`를, 운전 제약은 `runtime.run_enable/force_stop/tracking_enable/restart_request` 등을 함께 확인한다. 새 상태 머신이나 생산 체결 허가를 추가한 것이 아니다.
- Vision Run에서 이 컨텍스트를 표시하는 후속 작업은 Monitor 구현 지침서를 따른다. 기존 Vision DEV arrival/simulation API를 생산 타깃 설정으로 호출하지 않는다.

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

## GPIO 레이저·절전 추가 (2026-10-15)

- PTM이 RDK X5 GPIO BOARD 물리 핀 31을 소유한다. Core `laser.mode=ptm`은 기존 전송 설정을 유지하고 `gpio`도 PTM 전달 별칭으로 지원한다. 실제 backend/pin/numbering은 레이저 상태에 추가된다.
- `runtime.power_save_timeout_s` 기본 1800초. 새 PLC command_seq의 명령 1/5와 유효 Recipe/Point ID를 작업 요청으로 인정한다. 반복 상태 조회/heartbeat는 타이머를 연장하지 않는다. BUSY/이동 중 진입은 보류한다.
- `power_save.wake` params `{}`: 타이머 초기화·절전 해제. 기존 TCP JSON Lines와 HTTP `/api/command`를 사용한다. 레이저 ON 명령은 아니다.
- `runtime.power_save`: sleeping, timeout_s, idle_seconds, remaining_s, last_plc_request, last_wake_source. 기존 status 및 `monitor.snapshot`에 포함한다.
- `monitor.capabilities.commands`에 wake 명령, `power_save`에 timeout과 PLC wake 주소를 추가했다.
- PLC `D+00.4 WAKE_UP` 상승 에지. 자세한 PLC 확장은 `PLC_WAKEUP_EXTENSION.md` 참조. Gateway 공유메모리 크기/프로토콜 변경은 없다.
- 절전 시 기존 Vision `tracking.stop`으로 분석을 중지한다. 카메라 acquisition/전원 절전은 미구현이며 Vision 후속 계약으로 남겼다.
- `runtime.orchestration_enabled=false`여도 절전으로 실제 활성 추적을 중지했다면 Wake 뒤 PLC RUN_ENABLE/TRACKING_ENABLE 및 기존 Fault/FORCE_STOP/재시작 조건을 통과할 때만 추적을 재개한다. 원래 정지 상태·일반 시작에서는 자동 추적을 시작하지 않는다. 장치 비활성/바이패스는 재개 예약을 취소한다.
- 포인트 dispatch가 OFF 응답 후 PTM을 움직인다. 허용된 move의 실제 위치 완료 후만 GPIO ON. OFF/정지/허가 해제는 예약 ON generation을 무효화한다. Core PLC 완료는 기존 fresh/matching motion ID 검증을 유지한다.

### 임시 수동 ON·GPIO 알람 정정

`laser.on {}` / `laser.off {}`만으로 ON/OFF한다. ARM/DISARM API와 화면 조작은 제거했다.
ON은 자동 OFF/절전 중에도 1800초 한도로 수동 출력을 허용한다. 구형 `manual:true` 추가값은 허용하지만 필요하지 않다.
FORCE_STOP/Fault/재시작/장치 비활성/바이패스는 우선한다. Core 주기 정책과 조회는
수동을 취소/연장하지 않으며, 다음 이동/Jog/홈/사용자 정지 또는 만료 시 OFF·자동 모드로 복귀한다.
`status.laser`에는 control_mode/manual_override/manual_remaining_s가 추가됐다.
PTM 바이패스는 레이저 탭에서 설정한다. 내부 `laser.power_save`의
automatic_off/manual_allowed로 일반 자동 OFF와 강제 차단을 원자적으로 구분한다.
RUN_ENABLE=OFF의 내부 모터 정리만 preserve_laser_manual을 사용한다.
레이저는 출력 검출이 없으며 GPIO 입력/readback을 읽지 않는다.
PLC D+66/D+67, D+20.5, 레이저 경고/고장 요약과 대표 코드 5000~5099는 Reserved/0이다.
`laser.on` 상태값은 마지막 성공한 GPIO 명령을 뜻한다. PLC LASER_ON은 연속 ON 또는 점멸 활성 상태이며 점멸의 OFF 구간도 1을 유지한다. 실제 방출 검출값은 아니다.
`system.status.laser`는 `feedback_available=false`, `on_source=commanded_gpio_level`을 제공하고 armed 필드는 없다.
GPIO 호출 오류는 `laser.error`/Core `command_error`와 API 응답·로그에 남긴다.
PTM TCP·모터 RS485 통신 알람은 기존대로 유지한다. 자세한 계약은 Monitor 지침서 18절을 따른다.
