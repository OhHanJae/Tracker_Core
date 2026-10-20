# PLC v4.2 WakeUp/GPIO 확장 (2026-10-15)

기준 문서는 `트래커 PLC 통신 맵 v4.2 Core 반영.xlsx`이다. 원본 XLSX는 보존하며,
아래 WakeUp 비트와 GPIO 레이저 알람 변경을 확장 계약으로 적용한다. 다른 주소, 명령 번호, 카메라 2 예약 주소는 유지한다.
Core 웹과 Monitor의 내장 주소맵에 이 확장을 반영했다.

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

레이저는 출력 검출 입력이 없는 GPIO ON/OFF 장치다. Controller→PLC `D+66`, `D+67` 전체와
`D+20.5`, 경고/고장 요약의 레이저 비트, 대표 코드 `5000~5099`는 Reserved(항상 0)로 둔다.
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
PTM 서버 TCP·모터 RS485 통신 알람은 기존 PTM 영역에 유지한다.
과거 알람 DB 이력은 삭제하지 않는다.
