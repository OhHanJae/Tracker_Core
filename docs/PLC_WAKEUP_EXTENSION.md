# PLC v4.2 WakeUp 확장 (2026-10-15)

기준 문서는 `트래커 PLC 통신 맵 v4.2 Core 반영.xlsx`이다. 원본 XLSX는 보존하며,
아래 한 비트만 확장 계약으로 추가한다. 기존 주소, 명령 번호, 카메라 2 예약 주소는 유지한다.
Core 웹과 Monitor의 내장 주소맵에 이 확장을 반영했다.

| 방향 | 상대 주소 | 기본 read D1000 기준 | 이름 | 동작 |
| --- | --- | --- | --- | --- |
| PLC → Controller | D+00.4 | D1000.4 | WAKE_UP | 0→1 상승 에지에서 Vision/레이저 절전 해제 |

- PLC는 새 해제 요청마다 LOW→HIGH로 바꾼다. 다음 요청 전에는 Core가 LOW를 한 번 이상 읽도록 유지한다. Core 주기 기본값은 50ms이며, 실제 PLC/Gateway 전달 지연까지 고려한다.
- HIGH 유지와 heartbeat 변경은 무요청 타이머를 연장하지 않는다. Core 시작 시 최초 HIGH는 한 번의 해제로 취급한다.
- 새 `TARGET_APPLY_MOVE(1)` 또는 `NUTRUNNER_DATA_REQUEST(5)`의 새 command_seq와 유효 Recipe/Point ID도 절전을 해제한다. 보류 중인 명령은 기존 완료/순번 규칙을 따른다.
- 해제는 RUN_ENABLE, FORCE_STOP, Fault, LASER_ENABLE 및 바이패스를 변경하지 않는다. 해제 자체로 레이저는 켜지지 않는다.
- Controller → PLC에 새로운 비트를 할당하지 않았다. 절전 상태는 Core TCP/HTTP `get_status` / `monitor.snapshot`의 `runtime.power_save`로 확인한다.
- Gateway는 기존 100 WORD를 그대로 전달하므로 Gateway 코드나 공유메모리 프로토콜 변경은 필요 없다.

PLC 프로젝트 적용 시 이 확장 계약을 반영해야 WakeUp 비트를 사용할 수 있다.
