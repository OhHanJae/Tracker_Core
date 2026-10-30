# PLC 오류 및 경고 코드 v6

> 현재 알람 통신 형식은 **v5.2 비트맵**입니다. 최근 통신 진단·자동 복구는 유지하며, 이 문서의 v6/v7 알람 BYTE/WORD 설명은 이전 규격입니다. 현재 주소·코드는 [v5.2 복원 규격](PLC_ALARM_CODES_V5_2.md)을 따릅니다.

> 이전 규격 안내: 이 문서는 v6 과거 자료입니다. 현재 규격은 [모듈별 알람 v7](PLC_ALARM_CODES_V7.md)입니다. v6 코드를 현재 PLC WORD로 사용하지 않습니다.

2026-10-29부터 사용하는 소스 계약이다. 기준 파일은 `1. 계획,설계/트래커 PLC 통신 맵 v6.xlsx`이며 v5.2 이하 파일은 과거 자료로 보존한다. PLC 프로그램과 HMI의 알람 해석을 Core v6과 함께 갱신해야 한다. 이 문서는 로컬 구현 규격이며 현장 적용·검증 완료를 뜻하지 않는다.

| Controller → PLC | 의미 |
| --- | --- |
| D+21 (기본 D1121) | 현재 오류 대표 코드 1 WORD |
| D+22 (기본 D1122) | 현재 경고 대표 코드 1 WORD |
| D+23 | 마지막 신규 오류 코드. 같은 packed 형식, 원인 복구 후에도 유지 |
| D+24 | 신규 오류·경고 원인 보고 시 순번 +1. 65535 다음 0 |
| D+27 / D+28 | 전체 오류 / 경고 원인 개수 |
| D+20, D+25, D+26, D+50~79 | Reserved, 항상 0. 장치별 비트맵 폐지 |

`D+00.7 FAULT_ACTIVE`, `D+00.8 WARNING_ACTIVE`는 발생 유무만 나타낸다. 종류·상세는 D+21/22에서 읽는다. 기존 D+20.0 PROCESS_STOP 대신 기존 시스템 정지 D+00.B, 강제정지 D+00.F, Fault D+00.7과 기존 운전 인터록을 판단한다. D+21/22가 0인 것만으로 운전·체결 가능을 승인하지 않는다.

양방향 100 WORD / 200 BYTE, little-endian uint16과 명령·좌표·바이패스 주소는 유지한다. 이번 절약은 알람이 점유하던 주소를 예약으로 반환하는 것이다. Gateway의 read/write.byte_count나 공유메모리 크기를 자동 축소하지 않는다. 반환한 영역을 실제로 재할당하려면 새 맵에서 용도를 정의한다.

## 워드 조합과 해석

`WORD = (상세 코드 << 8) | 종류 코드`

- 하위 1 BYTE: 장치 또는 오류 종류 코드 1~255.
- 상위 1 BYTE: 종류 내 상세 코드 1~255. 현재 1~16을 사용하며 기존 내부 진단 bit 번호에 1을 더한다.
- 전체 WORD=0: 오류·경고 없음. 종류=0 또는 상세=0인 비정상 조합과 미정의 코드는 정상으로 처리하지 않고 미정의 코드로 표시한다.
- 오류·경고 등급은 워드 주소(D+21 Fault / D+22 Warning)로 구분한다. 같은 숫자라도 주소에 따라 의미가 다르다.
- PLC 해석: `종류 = WORD AND 16#00FF`, `상세 = (WORD >> 8) AND 16#00FF`.
- PTM 통신 끊김: 종류 9, 상세 1 → `0x0109` (DEC 265). 전송 바이트는 `09 01` 순서다.
- Camera 2 연결 오류: `D+21=0x0105` (DEC 261). Camera 2 FPS 경고: `D+22=0x0105`.

| 종류 코드 | 대상 |
| --- | --- |
| 1 | Controller |
| 2 | PLC Communication |
| 3 | Vision Common |
| 4 | Camera 1 |
| 5 | Camera 2 (camera_count=2일 때 필수) |
| 6 / 7 | Camera 3 / Camera 4. 현재 설비 미사용 |
| 8 | Tracker |
| 9 | PTM Pan/Tilt 공통 |
| 12 | Recipe |
| 13 | Calibration |
| 0 / 10 / 11 / 14~255 | Reserved. GPIO 레이저 오류·경고를 만들지 않음 |

## 동시 발생과 복구

오류와 경고는 각각 독립적으로 대표 1건을 선택한다. 장치 우선순위는 Controller → PLC → Vision → Camera 1~4 → Tracker → PTM → Recipe → Calibration이다. 같은 종류에서는 가장 낮은 상세 번호를 먼저 보낸다. 대표 원인이 해제되거나 latch가 reset되면 다음 원인이 표시된다. 코드를 순환 전송하지 않으므로 PLC는 모든 동시 원인을 이 두 워드로 열거할 수 없다. 전체 목록은 `alarms.active`, `alarms.history`, `alarms.export`에서 확인한다.

전체 개수는 대표 코드 수가 아닌 원인 수다. PTM Pan/Tilt의 동일 상세 원인은 OR 후 한 번 집계한다. 자동 복구·수동 latch/reset 정책과 바이패스 제외 정책은 기존과 같다. 전송 형식 변경 때문에 고장 원인을 조기에 해제하지 않는다.

새 원인이 0→1로 보고되면 D+24가 증가하고 같은 상태 반복 보고에는 증가하지 않는다. 한 장치의 한 상태 보고에 여러 신규 원인이 있어도 한 번 증가한다. 신규 Fault의 가장 낮은 상세 번호가 D+23에 저장된다. D+23은 대표 현재 코드와 다를 수 있다. 바이패스한 종류의 마지막 코드는 출력에서 0으로 숨기고 저장된 값과 과거 이력을 삭제하지 않는다.

## Core API 및 이력 호환성

TCP/HTTP 장치 API의 `warning_word`, `fault_word`는 모든 동시 원인을 보존하기 위한 내부 진단 비트맵이다. 이를 PLC 영역으로 그대로 전송하지 않는다. 기존 `alarms.primary_fault_code`, `primary_warning_code`, `last_fault_code`와 이력 `code`는 종전 장치 대표 번호를 유지한다. PLC 값에는 새 `plc_fault_code`, `plc_warning_code`, `plc_last_fault_code`를 사용한다.

v6 이력 사건은 `map_version:v6`, `relative_address:D+21 또는 D+22`, `plc_code`, `kind_code`, `detail_code`를 포함한다. `bit`는 내부 원인 식별용 필드이며 PLC 주소 뒤에 붙이는 bit 번호가 아니다. 이전 v5 DB 사건은 기존 map_version·주소·bit를 유지한다. 동일 워드 주소를 공유해도 장치·등급·상세별 사건과 발생 ID를 각각 유지한다.

## 적용과 검증

1. Core, PLC 프로그램과 HMI/Monitor를 v6 기준으로 함께 적용한다. 구형 비트 주소 또는 1101/4001 같은 십진 대표 번호를 PLC에서 비교하는 로직을 packed 코드 비교로 바꾼다.
2. PLC에서 0, PTM 통신 오류 0x0109, Camera 2 오류/경고 0x0105를 방향·주소·바이트 순서와 함께 대조한다.
3. 여러 오류, 같은 종류의 여러 상세 원인, 자동 복구, latch/reset, 바이패스 후 대표 코드 전환·전체 개수·이력을 확인한다.
4. D+20/25/26/50~79가 0이고 기존 명령·좌표·정지·레이저·heartbeat/ACK가 유지되는지 실제 PLC/RDK에서 확인한다. 이 저장소의 자동 테스트는 현장 검증을 대신하지 않는다.

## 통신 타임아웃 구분

| D+21 코드 | 의미 | 판정 및 해제 |
| --- | --- | --- |
| 0x0109 | PTM 통신 끊김 | 연결 거절·연결 종료 또는 RS485 미연결. 정상 통신 복구 시 자동 해제 |
| 0x0909 | PTM 통신 타임아웃 | 상태 조회는 motor.timeout_s 기준으로 status_retries+1회 모두 실패한 뒤 시간 초과인 경우 발생. 명령 응답 시간 초과도 포함. 정상 조회 복구 시 자동 해제 |
| 0x0703 | Vision 통신 타임아웃 | vision.timeout_s 내 TCP 연결/응답이 없을 때. 정상 상태 조회 복구 시 자동 해제 |
| 0x0803 | Vision 상태 수신 만료 | 마지막 정상 상태 수신 이후 runtime.vision_status_max_age_ms 초과. 새 정상 상태 수신 시 자동 해제 |
| 0x0903 | Vision 통신 끊김 | 연결 거절·연결 종료 등 시간 초과 이외의 조회 실패. 정상 상태 조회 복구 시 자동 해제 |
| 0x0603 | Vision 결과 타임아웃 | TCP 상태와 필수 카메라가 정상이고 대상이 존재하며 추적을 요청/실행하는 동안 결과 생성·결과 나이가 허용 시간을 초과. 결과 복구 또는 대상 부재 시 자동 해제 |

내부 원인은 예외 타입과 원인 체인으로 판정한다. 오류 문구에 timeout이라는 단어가 있다는 이유만으로 통신 타임아웃을 발생시키지 않는다. `devices.<id>.communication_timeout`은 마지막 상태 조회 또는 실행 명령에서 기록한 통신 실패가 시간 초과인지 나타낸다. 명령 타임아웃도 다음 정상 상태 조회까지 유지한다. 시작 직후 첫 조회 대기와 바이패스에서는 타임아웃을 만들지 않는다. PTM 상태 조회의 재시도 중에는 마지막 정상 상태를 유지한다. 이동·HOME 등 실행 명령을 타임아웃 때문에 자동 재전송하지 않는다.

통신 타임아웃의 자동 해제는 모터 보호·호밍·이동 시간 초과 같은 수동 리셋 고장을 해제하지 않는다. 동시 발생 시 기존 종류·상세 우선순위를 적용하므로 다른 보호 고장이 대표 코드로 먼저 표시될 수 있으며, 전체 원인은 알람 목록에서 확인한다.

## 코드표 유지관리

코드표 원본은 `core/web/assets/js/plc_map_data.js`다. `src/common/status.py`의 종류·명명된 고장 마스크·자동 해제 마스크와 함께 검증한다. 현재 116개 오류·경고 정의를 사용한다. `Auto`는 원인 복구 시 자동 해제, `Latch`는 `runtime.fault_latch_enabled=true`일 때 원인 복구 후에도 Reset까지 유지한다. latch가 꺼져 있어도 현재 활성 원인은 계속 오류로 표시한다. 과거 이력의 해제 문구는 다시 쓰지 않는다.

Core 루트에서 `python tools/sync_plc_alarm_map.py`를 실행하면 Monitor 내장 맵과 아래 코드표를 동기화한다. `--check`는 파일을 쓰지 않고 코드·중복 정의·예약 주소·해제 정책·생성본 일치 여부를 검사한다. Excel 통신 맵의 `03_Device_Alarm_Detail`도 같은 정의로 갱신하고 변경 안내서를 검토한다. 코드나 맵 변경을 실제 PLC에 적용하기 전 Core Worker와 PLC/HMI의 버전을 함께 맞춘다.

변경 전후와 PLC 적용 순서는 `1. 계획,설계/03_PLC_알람_v6_변경_안내서.docx`를 따른다.

## 상세 코드 목록

| 장치 | 등급 | 주소 | 종류 | 상세 | WORD (HEX) | 항목 | 해제 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Camera 1 | Warning | D+22 | 4 | 1 | 0x0104 | FPS_LOW | Auto |
| Camera 1 | Warning | D+22 | 4 | 2 | 0x0204 | FRAME_DROP_HIGH | Auto |
| Camera 1 | Warning | D+22 | 4 | 3 | 0x0304 | FRAME_LATENCY_HIGH | Auto |
| Camera 1 | Warning | D+22 | 4 | 4 | 0x0404 | EXPOSURE_BAD | Auto |
| Camera 1 | Warning | D+22 | 4 | 5 | 0x0504 | IMAGE_QUALITY_LOW | Auto |
| Camera 1 | Warning | D+22 | 4 | 6 | 0x0604 | CAMERA_TEMP_HIGH | Auto |
| Camera 1 | Warning | D+22 | 4 | 7 | 0x0704 | VISION_CONFIDENCE_LOW | Auto |
| Camera 1 | Warning | D+22 | 4 | 8 | 0x0804 | FRAME_TIMEOUT_TRANSIENT | Auto |
| Camera 1 | Fault | D+21 | 4 | 1 | 0x0104 | CAMERA_DISCONNECTED | Auto |
| Camera 1 | Fault | D+21 | 4 | 2 | 0x0204 | FRAME_TIMEOUT | Auto |
| Camera 1 | Fault | D+21 | 4 | 3 | 0x0304 | CAMERA_INIT_FAIL | Latch |
| Camera 1 | Fault | D+21 | 4 | 4 | 0x0404 | CALIBRATION_DATA_MISSING | Latch |
| Camera 1 | Fault | D+21 | 4 | 5 | 0x0504 | CALIBRATION_DATA_INVALID | Latch |
| Camera 1 | Fault | D+21 | 4 | 6 | 0x0604 | FORMAT_INVALID | Latch |
| Camera 1 | Fault | D+21 | 4 | 7 | 0x0704 | TIMESTAMP_INVALID | Auto |
| Camera 1 | Fault | D+21 | 4 | 8 | 0x0804 | CAMERA_DRIVER_FAULT | Latch |
| Camera 2 | Warning | D+22 | 5 | 1 | 0x0105 | FPS_LOW | Auto |
| Camera 2 | Warning | D+22 | 5 | 2 | 0x0205 | FRAME_DROP_HIGH | Auto |
| Camera 2 | Warning | D+22 | 5 | 3 | 0x0305 | FRAME_LATENCY_HIGH | Auto |
| Camera 2 | Warning | D+22 | 5 | 4 | 0x0405 | EXPOSURE_BAD | Auto |
| Camera 2 | Warning | D+22 | 5 | 5 | 0x0505 | IMAGE_QUALITY_LOW | Auto |
| Camera 2 | Warning | D+22 | 5 | 6 | 0x0605 | CAMERA_TEMP_HIGH | Auto |
| Camera 2 | Warning | D+22 | 5 | 7 | 0x0705 | VISION_CONFIDENCE_LOW | Auto |
| Camera 2 | Warning | D+22 | 5 | 8 | 0x0805 | FRAME_TIMEOUT_TRANSIENT | Auto |
| Camera 2 | Fault | D+21 | 5 | 1 | 0x0105 | CAMERA_DISCONNECTED | Auto |
| Camera 2 | Fault | D+21 | 5 | 2 | 0x0205 | FRAME_TIMEOUT | Auto |
| Camera 2 | Fault | D+21 | 5 | 3 | 0x0305 | CAMERA_INIT_FAIL | Latch |
| Camera 2 | Fault | D+21 | 5 | 4 | 0x0405 | CALIBRATION_DATA_MISSING | Latch |
| Camera 2 | Fault | D+21 | 5 | 5 | 0x0505 | CALIBRATION_DATA_INVALID | Latch |
| Camera 2 | Fault | D+21 | 5 | 6 | 0x0605 | FORMAT_INVALID | Latch |
| Camera 2 | Fault | D+21 | 5 | 7 | 0x0705 | TIMESTAMP_INVALID | Auto |
| Camera 2 | Fault | D+21 | 5 | 8 | 0x0805 | CAMERA_DRIVER_FAULT | Latch |
| Vision Common | Warning | D+22 | 3 | 2 | 0x0203 | VISION_PROCESSING_SLOW | Auto |
| Vision Common | Warning | D+22 | 3 | 3 | 0x0303 | VISION_DEGRADED | Auto |
| Vision Common | Warning | D+22 | 3 | 5 | 0x0503 | COORDINATE_JITTER_HIGH | Auto |
| Vision Common | Fault | D+21 | 3 | 1 | 0x0103 | REQUIRED_CAMERA_UNAVAILABLE | Auto |
| Vision Common | Fault | D+21 | 3 | 3 | 0x0303 | VISION_PIPELINE_STOPPED | Latch |
| Vision Common | Fault | D+21 | 3 | 4 | 0x0403 | COORD_TRANSFORM_FAIL | Latch |
| Vision Common | Fault | D+21 | 3 | 5 | 0x0503 | CALIBRATION_SET_MISMATCH | Latch |
| Vision Common | Fault | D+21 | 3 | 6 | 0x0603 | VISION_RESULT_TIMEOUT | Auto |
| Vision Common | Fault | D+21 | 3 | 7 | 0x0703 | VISION_COMM_TIMEOUT | Auto |
| Vision Common | Fault | D+21 | 3 | 8 | 0x0803 | VISION_STATUS_STALE | Auto |
| Vision Common | Fault | D+21 | 3 | 9 | 0x0903 | VISION_COMM_LOST | Auto |
| Tracker | Warning | D+22 | 8 | 1 | 0x0108 | TRACK_CONFIDENCE_LOW | Auto |
| Tracker | Warning | D+22 | 8 | 2 | 0x0208 | MARKER_COUNT_LOW | Auto |
| Tracker | Warning | D+22 | 8 | 3 | 0x0308 | POSITION_JITTER_HIGH | Auto |
| Tracker | Warning | D+22 | 8 | 4 | 0x0408 | TRACKING_LATENCY_HIGH | Auto |
| Tracker | Warning | D+22 | 8 | 5 | 0x0508 | MARKER_LOST_TRANSIENT | Auto |
| Tracker | Warning | D+22 | 8 | 6 | 0x0608 | POSE_ERROR_HIGH | Auto |
| Tracker | Fault | D+21 | 8 | 2 | 0x0208 | POSITION_CALC_FAIL | Auto |
| Tracker | Fault | D+21 | 8 | 3 | 0x0308 | POSE_CALC_FAIL | Auto |
| Tracker | Fault | D+21 | 8 | 4 | 0x0408 | TRACKER_CALIB_INVALID | Latch |
| Tracker | Fault | D+21 | 8 | 5 | 0x0508 | TRACKING_DATA_TIMEOUT | Auto |
| Tracker | Fault | D+21 | 8 | 6 | 0x0608 | COORDINATE_INVALID | Latch |
| Tracker | Fault | D+21 | 8 | 7 | 0x0708 | POSITION_JUMP_INVALID | Auto |
| Pan/Tilt | Warning | D+22 | 9 | 1 | 0x0109 | POSITION_ERROR_HIGH | Auto |
| Pan/Tilt | Warning | D+22 | 9 | 2 | 0x0209 | MOVE_TIME_HIGH | Auto |
| Pan/Tilt | Warning | D+22 | 9 | 3 | 0x0309 | LIMIT_NEAR | Auto |
| Pan/Tilt | Warning | D+22 | 9 | 4 | 0x0409 | MOTOR_TEMP_HIGH | Auto |
| Pan/Tilt | Warning | D+22 | 9 | 5 | 0x0509 | COMM_RETRY_HIGH | Auto |
| Pan/Tilt | Warning | D+22 | 9 | 6 | 0x0609 | ENCODER_JITTER_HIGH | Auto |
| Pan/Tilt | Fault | D+21 | 9 | 1 | 0x0109 | PTM_COMM_LOST | Auto |
| Pan/Tilt | Fault | D+21 | 9 | 2 | 0x0209 | MOTOR_DRIVER_FAULT | Latch |
| Pan/Tilt | Fault | D+21 | 9 | 3 | 0x0309 | HOMING_FAIL | Latch |
| Pan/Tilt | Fault | D+21 | 9 | 4 | 0x0409 | MOVE_TIMEOUT | Latch |
| Pan/Tilt | Fault | D+21 | 9 | 5 | 0x0509 | ENCODER_FAULT | Latch |
| Pan/Tilt | Fault | D+21 | 9 | 6 | 0x0609 | LIMIT_FAULT | Latch |
| Pan/Tilt | Fault | D+21 | 9 | 7 | 0x0709 | TARGET_NOT_REACHED | Latch |
| Pan/Tilt | Fault | D+21 | 9 | 8 | 0x0809 | DRIVER_PROTECTION | Latch |
| Pan/Tilt | Fault | D+21 | 9 | 9 | 0x0909 | PTM_COMM_TIMEOUT | Auto |
| Controller | Warning | D+22 | 1 | 1 | 0x0101 | CPU_USAGE_HIGH | Auto |
| Controller | Warning | D+22 | 1 | 2 | 0x0201 | CPU_TEMP_HIGH | Auto |
| Controller | Warning | D+22 | 1 | 3 | 0x0301 | MEMORY_USAGE_HIGH | Auto |
| Controller | Warning | D+22 | 1 | 4 | 0x0401 | DISK_SPACE_LOW | Auto |
| Controller | Warning | D+22 | 1 | 5 | 0x0501 | MAIN_LOOP_SLOW | Auto |
| Controller | Warning | D+22 | 1 | 6 | 0x0601 | VISION_CYCLE_SLOW | Auto |
| Controller | Warning | D+22 | 1 | 7 | 0x0701 | LOG_WRITE_WARN | Auto |
| Controller | Warning | D+22 | 1 | 8 | 0x0801 | QUEUE_BACKLOG | Auto |
| Controller | Fault | D+21 | 1 | 1 | 0x0101 | MAIN_TASK_FAULT | Latch |
| Controller | Fault | D+21 | 1 | 2 | 0x0201 | VISION_TASK_STOPPED | Latch |
| Controller | Fault | D+21 | 1 | 3 | 0x0301 | MOTION_TASK_STOPPED | Latch |
| Controller | Fault | D+21 | 1 | 4 | 0x0401 | CONFIG_LOAD_FAIL | Latch |
| Controller | Fault | D+21 | 1 | 5 | 0x0501 | RECIPE_DATA_ACCESS_FAIL | Latch |
| Controller | Fault | D+21 | 1 | 6 | 0x0601 | SHARED_MEMORY_FAULT | Latch |
| Controller | Fault | D+21 | 1 | 7 | 0x0701 | INTERNAL_IPC_FAULT | Latch |
| Controller | Fault | D+21 | 1 | 8 | 0x0801 | CONTROLLER_INIT_FAIL | Latch |
| Controller | Fault | D+21 | 1 | 9 | 0x0901 | WATCHDOG_FAULT | Latch |
| PLC Communication | Warning | D+22 | 2 | 1 | 0x0102 | PLC_RESPONSE_SLOW | Auto |
| PLC Communication | Warning | D+22 | 2 | 2 | 0x0202 | PLC_COMM_RETRY_HIGH | Auto |
| PLC Communication | Warning | D+22 | 2 | 3 | 0x0302 | PLC_TIMEOUT_TRANSIENT | Auto |
| PLC Communication | Warning | D+22 | 2 | 4 | 0x0402 | PLC_HEARTBEAT_DELAY | Auto |
| PLC Communication | Fault | D+21 | 2 | 1 | 0x0102 | PLC_DISCONNECTED | Auto |
| PLC Communication | Fault | D+21 | 2 | 2 | 0x0202 | PLC_HEARTBEAT_TIMEOUT | Auto |
| PLC Communication | Fault | D+21 | 2 | 3 | 0x0302 | XGT_COMM_FAIL | Auto |
| PLC Communication | Fault | D+21 | 2 | 4 | 0x0402 | PLC_READ_FAIL | Auto |
| PLC Communication | Fault | D+21 | 2 | 5 | 0x0502 | PLC_WRITE_FAIL | Auto |
| PLC Communication | Fault | D+21 | 2 | 6 | 0x0602 | PROTOCOL_RESPONSE_ERROR | Auto |
| Recipe | Warning | D+22 | 12 | 1 | 0x010C | UNUSED_POINT_EXISTS | Auto |
| Recipe | Warning | D+22 | 12 | 2 | 0x020C | DEFAULT_VALUE_APPLIED | Auto |
| Recipe | Warning | D+22 | 12 | 3 | 0x030C | RECIPE_VERSION_DIFF | Auto |
| Recipe | Fault | D+21 | 12 | 1 | 0x010C | RECIPE_NOT_FOUND | Latch |
| Recipe | Fault | D+21 | 12 | 2 | 0x020C | RECIPE_LOAD_FAIL | Latch |
| Recipe | Fault | D+21 | 12 | 3 | 0x030C | POINT_NOT_FOUND | Latch |
| Recipe | Fault | D+21 | 12 | 4 | 0x040C | POINT_DATA_INVALID | Latch |
| Recipe | Fault | D+21 | 12 | 5 | 0x050C | CAMERA_CONFIG_MISMATCH | Latch |
| Recipe | Fault | D+21 | 12 | 6 | 0x060C | TARGET_COORDINATE_INVALID | Latch |
| Calibration | Warning | D+22 | 13 | 1 | 0x010D | CALIBRATION_ERROR_HIGH | Auto |
| Calibration | Warning | D+22 | 13 | 2 | 0x020D | CALIBRATION_AGED | Auto |
| Calibration | Warning | D+22 | 13 | 3 | 0x030D | CAL_DEFAULT_VALUE_USED | Auto |
| Calibration | Fault | D+21 | 13 | 1 | 0x010D | CAMERA_CAL_MISSING | Latch |
| Calibration | Fault | D+21 | 13 | 2 | 0x020D | TRACKER_CAL_MISSING | Latch |
| Calibration | Fault | D+21 | 13 | 3 | 0x030D | PANTILT_CAL_MISSING | Latch |
| Calibration | Fault | D+21 | 13 | 4 | 0x040D | LASER_CAL_MISSING | Latch |
| Calibration | Fault | D+21 | 13 | 5 | 0x050D | COORD_TRANSFORM_INVALID | Latch |
| Calibration | Fault | D+21 | 13 | 6 | 0x060D | CALIBRATION_LOAD_FAIL | Latch |
| Calibration | Fault | D+21 | 13 | 7 | 0x070D | CALIBRATION_VERSION_MISMATCH | Latch |
