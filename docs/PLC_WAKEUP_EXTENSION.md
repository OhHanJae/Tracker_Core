# PLC v7 WakeUp 및 GPIO 계약

현재 규격은 v7이다. D+21 코어, D+22 PTM, D+23 비전, D+24 PLC가 각각 1 WORD이며 상위 BYTE=오류, 하위 BYTE=경고다. 모듈 안에서 오류·경고를 각각 우선순위 1건씩 표시한다. D+25는 알람 순번, D+27/28은 전체 원인 개수, D+20/26/50~79는 Reserved(0)다. 전체 규격은 [PLC 모듈별 알람 v7](PLC_ALARM_CODES_V7.md)을 따른다.

기준은 `트래커 PLC 통신 맵 v7.xlsx`이다. 아래 WakeUp·GPIO 동작과 D+01 레이저 모드를 유지하며, 알람은 v7의 모듈별 상위 오류·하위 경고 WORD로 보고한다. Camera 2는 camera_count=2일 때 사용한다.

| 방향 | 상대 주소 | 기본 read D1000 기준 | 이름 | 동작 |
| --- | --- | --- | --- | --- |
| PLC → Controller | D+00.4 | D1000.4 | WAKE_UP | 0→1 상승 에지에서 Vision/레이저 절전 해제 |

- PLC는 새 해제 요청마다 LOW→HIGH로 바꾼다. 다음 요청 전에는 Core가 LOW를 한 번 이상 읽도록 유지한다. Core 주기 기본값은 50ms이며, 실제 PLC/Gateway 전달 지연까지 고려한다.
- HIGH 유지와 heartbeat 변경은 무요청 타이머를 연장하지 않는다. Core 시작 시 최초 HIGH는 한 번의 해제로 취급한다.
- 새 `TARGET_APPLY_MOVE(1)` 또는 `NUTRUNNER_DATA_REQUEST(5)`의 새 command_seq와 유효 Recipe/Point ID도 절전을 해제한다. 보류 중인 명령은 기존 완료/순번 규칙을 따른다.
- 해제는 RUN_ENABLE, FORCE_STOP, Fault, LASER_MODE 및 바이패스를 변경하지 않는다. 해제 자체로 레이저는 켜지지 않는다.
- Controller → PLC에 새로운 비트를 할당하지 않았다. 절전 상태는 Core TCP/HTTP `get_status` / `monitor.snapshot`의 `runtime.power_save`로 확인한다.
- Gateway는 기존 100 WORD를 그대로 전달하므로 Gateway 코드나 공유메모리 프로토콜 변경은 필요 없다.

PLC 프로젝트 적용 시 이 확장 계약을 반영해야 WakeUp 비트를 사용할 수 있다.

## GPIO 레이저 알람 변경

레이저는 출력 검출 입력이 없는 GPIO ON/OFF 장치다. v7의 Controller→PLC `D+20/26`과 `D+50~79` 전체는 Reserved(항상 0)다. v7에서는 GPIO 레이저 BYTE 코드를 배정하지 않으며 GPIO 레이저 경고·고장을 PLC 코드로 생성하지 않는다. 기존 API 대표 번호 5000~5099도 사용하지 않는다.
GPIO 입력/readback을 읽거나 통신·출력 검출·온도·보호 알람을 생성하지 않는다.
GPIO 초기화/쓰기 예외는 API 오류와 로그의 소프트웨어 진단으로만 보고한다.
OFF 호출이 실패하면 이동은 실행하지 않으며, 모터 STOP은 GPIO 예외와 별개로 시도한다.
`LASER_ON` 비트는 연속 ON 및 점멸 활성 시 1을 유지한다. 점멸의 OFF 구간도 1이며, 레이저 OFF 상태는 0이다. 실제 광 방출 검출값이 아니다.

## 레이저 모드 변경 (2026-10-20)

PLC → Controller `D+00.3`은 Reserved이며, `D+01 LASER_MODE` WORD를 사용한다.
0=강제 OFF, 1=자동(Controller 제어), 2=상시 ON, 3=500ms 간격 점멸,
4=250ms 간격 점멸, 5=100ms 간격 점멸이다. 간격은 ON/OFF 전환 간격이다.
6~65535는 Reserved이며 강제 OFF로 처리한다. 2~5는 자동 공정 및 절전 조건과 무관하게 적용하되,
장치 고장·바이패스·Core 재시작의 출력 차단은 유지한다. 점멸은 PTM 내부에서 처리한다.
`D+00.F FORCE_STOP`은 PTM 정지만 수행하며 레이저 상태를 변경하지 않는다.
Core와 PTM 서버를 함께 갱신하고, 기존 자동 운전에는 PLC에서 `D+01=1`을 설정해야 한다.
PTM 통신 끊김은 D+22 상위 BYTE=1, Core→PTM TCP 연결/응답 타임아웃은 D+22 상위 BYTE=9로 구분한다. 경고가 없을 때의 WORD는 각각 0x0100, 0x0900이다. PTM은 상태 조회 재시도를 모두 소진한 뒤 통신 오류를 보고한다.
과거 알람 DB 이력은 삭제하지 않는다.

## v7 모듈별 WORD와 통신 코드

| WORD | 상위 BYTE 오류 | 하위 BYTE 경고 |
| --- | --- | --- |
| D+21 Core | Controller → Recipe → Calibration의 대표 1건 | 같은 모듈의 대표 1건 |
| D+22 PTM | Pan/Tilt 공통 대표 1건 | Pan/Tilt 공통 대표 1건 |
| D+23 Vision | Vision Common → Camera 1~4 → Tracker의 대표 1건 | 같은 모듈의 대표 1건 |
| D+24 PLC | PLC 통신 대표 1건 | PLC 통신 대표 1건 |

WORD=(오류<<8)|경고이며 각 BYTE 0은 해당 등급 없음이다. PTM 타임아웃은 D+22 상위=9, Vision 통신 타임아웃/상태 만료/통신 끊김/결과 지연은 D+23 상위=7/8/9/6이다. 예를 들어 PTM 오류 9·경고 5 동시 발생은 D+22=0x0905다. 네 모듈을 독립적으로 동시에 표시하며 전체 원인은 알람 API/이력에 남긴다.

v6의 전체 오류·경고·마지막 오류 WORD는 현재 해석에 사용하지 않는다. 알람 순번은 D+24에서 D+25로 이동했고 마지막 오류는 API/이력으로 조회한다. 신규 API는 alarms.plc_alarm_format=module_bytes_v7 및 alarms.plc_module_alarms다. 기존 plc_fault_code/plc_warning_code/plc_last_fault_code는 v6 호환용이며 현재 PLC WORD가 아니다.

PTM 상태 조회는 timeout_s와 status_retries를 모두 적용하며 이동·HOME 명령을 자동 재전송하지 않는다. 통신 복구 시 Auto, 보호 고장은 기존 latch/reset 조건을 유지한다. 이력 bit는 PLC의 bit 주소가 아니다. 방향별 100 WORD 및 명령·좌표·Heartbeat 주소는 유지한다.
