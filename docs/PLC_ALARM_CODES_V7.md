# PLC 모듈별 오류 및 경고 v7

2026-10-29 현재 규격이다. 기준은 `1. 계획,설계/트래커 PLC 통신 맵 v7.xlsx`다. v6의 전체 오류/경고 대표 워드를 폐지하고 코어·PTM·비전·PLC별 WORD를 사용한다. Core·Monitor·PLC/HMI를 v7로 함께 갱신한다. 실물 설비 적용 완료를 뜻하지 않는다.

## 워드 배치와 해석

| Controller → PLC | 모듈 또는 값 |
| --- | --- |
| D+21 (기본 D1121) | 코어 오류/경고 |
| D+22 (기본 D1122) | PTM 오류/경고 |
| D+23 (기본 D1123) | 비전 오류/경고 |
| D+24 (기본 D1124) | PLC 통신 오류/경고 |
| D+25 | 알람 순번. v6 D+24에서 이동 |
| D+27 / D+28 | 전체 오류 / 경고 원인 개수 |
| D+20 / D+26 / D+50~79 | Reserved(0) |

각 모듈의 `WORD = (ERROR_CODE << 8) | WARNING_CODE`다. 상위 BYTE는 오류, 하위 BYTE는 경고다. `오류=(WORD >> 8) AND 255`, `경고=WORD AND 255`로 분리한다. 각 BYTE의 0은 해당 등급 알람 없음이고 전체 WORD=0은 그 모듈의 오류·경고 없음이다. 모듈을 나타내는 BYTE는 없다. 주소가 모듈을 결정한다.

예: PTM 통신 타임아웃(오류 9)과 통신 재시도 경고(경고 5)가 함께 있으면 D+22=0x0905(DEC 2309)다. little-endian 바이트는 05 09다. 비전 오류 7만 있으면 D+23=0x0700, 코어 오류 1만 있으면 D+21=0x0100이다. 각 모듈의 오류와 경고는 동시에 보고할 수 있다.

알람 할당은 v5.2의 27 WORD에서 v7의 7 WORD(모듈 4, 순번 1, 개수 2)로 줄어 20 WORD/40 BYTE를 확보한다. v6 대비로는 1 WORD 증가한다. 전체 전송 길이는 방향별 100 WORD/200 BYTE로 유지하므로 패킷 크기를 자동 축소하지 않는다. 명령·좌표·Heartbeat·바이패스 주소는 유지한다.

## 모듈 내부 코드와 우선순위

오류와 경고는 서로 독립적인 코드 공간이다. 같은 숫자도 모듈과 오류/경고 BYTE에 따라 의미가 다르다. 각 등급에서 모듈 내부 우선순위 1건씩 표시하고 네 모듈은 동시에 표시한다.

| 모듈 | 원인 장치의 우선순위 | BYTE 코드 범위 |
| --- | --- | --- |
| core | Controller → Recipe → Calibration | 1~16 / 17~32 / 33~48 |
| ptm | Pan/Tilt 공통 | 1~16 |
| vision | Vision Common → Camera 1 → Camera 2 → Camera 3 → Camera 4 → Tracker | 1~16 / 17~32 / 33~48 / 49~64 / 65~80 / 81~96 |
| plc | PLC Communication | 1~16 |

같은 원인 장치 안에서는 상세 번호가 낮은 항목이 우선이다. BYTE 코드는 장치별 기준값(0/16/32/…)에 내부 상세 번호(bit+1)를 더한 값이다. Camera 3/4의 49~80은 현재 설비 미사용이며 Camera 2는 camera_count=2일 때 필수다. GPIO 레이저 알람은 생성하지 않는다. 미정의 코드는 정상으로 해석하지 않는다.

대표 원인이 복구되거나 latch가 Reset되면 다음 원인이 표시된다. PTM Pan/Tilt의 같은 상세 원인은 OR 후 한 번 집계한다. 전체 원인은 alarms.active/history/export에 각각 남으며 D+27/28은 대표 코드 수가 아닌 전체 원인 수다. D+00.7/.8의 전체 오류/경고 유무도 유지한다.

## 통신 타임아웃과 복구

| WORD / BYTE | 코드 | 의미 | 해제 |
| --- | --- | --- | --- |
| PTM D+22 오류 | 1 | TCP 연결 거절·종료 또는 RS485 미연결 | 정상 조회 후 Auto |
| PTM D+22 오류 | 9 | TCP 연결/응답 타임아웃 | 정상 조회 후 Auto |
| Vision D+23 오류 | 6 | 결과 생성/결과 나이 타임아웃 | 결과 복구 또는 대상 부재 후 Auto |
| Vision D+23 오류 | 7 | TCP 연결/응답 타임아웃 | 정상 조회 후 Auto |
| Vision D+23 오류 | 8 | 마지막 정상 상태 수신 만료 | 새 정상 상태 후 Auto |
| Vision D+23 오류 | 9 | 연결 거절·종료 등 통신 실패 | 정상 조회 후 Auto |
| PLC D+24 오류 | 2 | PLC Heartbeat 만료 | Heartbeat 복구 후 Auto |

PTM 상태 조회는 motor.timeout_s마다 motor.status_retries+1회 시도하고 모두 실패한 뒤 통신 오류를 보고한다. 재시도 중에는 마지막 정상 상태를 유지한다. 실행 명령은 자동 재전송하지 않는다. Vision TCP 제한은 vision.timeout_s이고 상태·결과의 유효 시간은 runtime.vision_status_max_age_ms다. 결과 타임아웃에는 필수 카메라 정상·대상 존재·추적 요청/실행 조건이 필요하다.

예외 타입과 명시적 원인 체인으로 타임아웃을 판정한다. `devices.<id>.communication_timeout`은 조회 또는 실행 명령의 마지막 통신 실패를 나타내며 다음 정상 조회까지 유지한다. 첫 조회 대기나 바이패스는 타임아웃을 만들지 않는다.

`Auto`는 원인 복구 시 자동 해제한다. `Latch`는 runtime.fault_latch_enabled=true일 때 원인 해소 후 수동 Reset까지 유지한다. Latch가 꺼져도 현재 활성 원인은 계속 오류다. 통신 복구로 모터 보호·호밍·이동 타임아웃 같은 보호 고장을 지우지 않는다. 바이패스는 해당 모듈/원인을 현재 코드·개수에서 제외하며 과거 이력은 보존한다.

## 순번과 API 및 이력

새 원인 보고마다 D+25가 증가하고 같은 상태 반복은 유지하며 65535 다음 0이다. 한 장치·등급 보고에 새 원인이 여러 개여도 1회 증가하고 여러 보고는 여러 번 증가할 수 있다. COMMAND_SEQ와 별개다. v6 D+23의 마지막 오류 WORD는 폐지하고 마지막 오류는 API/이력에서 확인한다.

`alarms.plc_alarm_format=module_bytes_v7`와 `alarms.plc_module_alarms`가 현재 PLC 형식이다. 각 core/ptm/vision/plc 항목에 word_offset, word, fault_code, warning_code를 반환한다. 기존 primary_fault_code/primary_warning_code/last_fault_code는 장치 대표 번호로 유지한다. 기존 plc_fault_code/plc_warning_code/plc_last_fault_code는 v6 호환용 진단값이며 v7 PLC WORD가 아니다. PLC 표시는 plc_module_alarms를 사용한다.

v7 이력은 map_version=v7, module, byte(high=오류/low=경고), byte_code, relative_address를 기록한다. plc_code는 해당 사건만 조합한 WORD 기여값(오류 BYTE<<8 또는 경고 BYTE)이며 현재 모듈의 오류·경고 조합 WORD와 다를 수 있다. device와 bit는 내부 원인 식별용이다. 과거 v5/v6 사건의 버전·주소·필드는 다시 쓰지 않는다.

## 파일과 유지관리

코드표 원본은 core/web/assets/js/plc_map_data.js다. Core 루트에서 `python tools/sync_plc_alarm_map.py`로 Monitor 맵과 아래 목록을 동기화하고 `--check`로 파일 변경 없이 인코더·주소·중복·해제 정책·생성본을 검사한다. Excel의 03_Device_Alarm_Detail도 같은 정의를 사용한다. 변경 전후와 PLC 적용 순서는 `1. 계획,설계/03_PLC_알람_v7_변경_안내서.docx`를 따른다. v6 이하 문서는 이전 규격이다.

## 상세 코드 목록

| 모듈 | 등급 | WORD | BYTE 코드 | 원인 장치 | 단독 WORD | 항목 | 해제 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| core | Fault | D+21 | 1 | Controller | 0x0100 | MAIN_TASK_FAULT | Latch |
| core | Fault | D+21 | 2 | Controller | 0x0200 | VISION_TASK_STOPPED | Latch |
| core | Fault | D+21 | 3 | Controller | 0x0300 | MOTION_TASK_STOPPED | Latch |
| core | Fault | D+21 | 4 | Controller | 0x0400 | CONFIG_LOAD_FAIL | Latch |
| core | Fault | D+21 | 5 | Controller | 0x0500 | RECIPE_DATA_ACCESS_FAIL | Latch |
| core | Fault | D+21 | 6 | Controller | 0x0600 | SHARED_MEMORY_FAULT | Latch |
| core | Fault | D+21 | 7 | Controller | 0x0700 | INTERNAL_IPC_FAULT | Latch |
| core | Fault | D+21 | 8 | Controller | 0x0800 | CONTROLLER_INIT_FAIL | Latch |
| core | Fault | D+21 | 9 | Controller | 0x0900 | WATCHDOG_FAULT | Latch |
| core | Fault | D+21 | 17 | Recipe | 0x1100 | RECIPE_NOT_FOUND | Latch |
| core | Fault | D+21 | 18 | Recipe | 0x1200 | RECIPE_LOAD_FAIL | Latch |
| core | Fault | D+21 | 19 | Recipe | 0x1300 | POINT_NOT_FOUND | Latch |
| core | Fault | D+21 | 20 | Recipe | 0x1400 | POINT_DATA_INVALID | Latch |
| core | Fault | D+21 | 21 | Recipe | 0x1500 | CAMERA_CONFIG_MISMATCH | Latch |
| core | Fault | D+21 | 22 | Recipe | 0x1600 | TARGET_COORDINATE_INVALID | Latch |
| core | Fault | D+21 | 33 | Calibration | 0x2100 | CAMERA_CAL_MISSING | Latch |
| core | Fault | D+21 | 34 | Calibration | 0x2200 | TRACKER_CAL_MISSING | Latch |
| core | Fault | D+21 | 35 | Calibration | 0x2300 | PANTILT_CAL_MISSING | Latch |
| core | Fault | D+21 | 36 | Calibration | 0x2400 | LASER_CAL_MISSING | Latch |
| core | Fault | D+21 | 37 | Calibration | 0x2500 | COORD_TRANSFORM_INVALID | Latch |
| core | Fault | D+21 | 38 | Calibration | 0x2600 | CALIBRATION_LOAD_FAIL | Latch |
| core | Fault | D+21 | 39 | Calibration | 0x2700 | CALIBRATION_VERSION_MISMATCH | Latch |
| core | Warning | D+21 | 1 | Controller | 0x0001 | CPU_USAGE_HIGH | Auto |
| core | Warning | D+21 | 2 | Controller | 0x0002 | CPU_TEMP_HIGH | Auto |
| core | Warning | D+21 | 3 | Controller | 0x0003 | MEMORY_USAGE_HIGH | Auto |
| core | Warning | D+21 | 4 | Controller | 0x0004 | DISK_SPACE_LOW | Auto |
| core | Warning | D+21 | 5 | Controller | 0x0005 | MAIN_LOOP_SLOW | Auto |
| core | Warning | D+21 | 6 | Controller | 0x0006 | VISION_CYCLE_SLOW | Auto |
| core | Warning | D+21 | 7 | Controller | 0x0007 | LOG_WRITE_WARN | Auto |
| core | Warning | D+21 | 8 | Controller | 0x0008 | QUEUE_BACKLOG | Auto |
| core | Warning | D+21 | 17 | Recipe | 0x0011 | UNUSED_POINT_EXISTS | Auto |
| core | Warning | D+21 | 18 | Recipe | 0x0012 | DEFAULT_VALUE_APPLIED | Auto |
| core | Warning | D+21 | 19 | Recipe | 0x0013 | RECIPE_VERSION_DIFF | Auto |
| core | Warning | D+21 | 33 | Calibration | 0x0021 | CALIBRATION_ERROR_HIGH | Auto |
| core | Warning | D+21 | 34 | Calibration | 0x0022 | CALIBRATION_AGED | Auto |
| core | Warning | D+21 | 35 | Calibration | 0x0023 | CAL_DEFAULT_VALUE_USED | Auto |
| ptm | Fault | D+22 | 1 | Pan/Tilt | 0x0100 | PTM_COMM_LOST | Auto |
| ptm | Fault | D+22 | 2 | Pan/Tilt | 0x0200 | MOTOR_DRIVER_FAULT | Latch |
| ptm | Fault | D+22 | 3 | Pan/Tilt | 0x0300 | HOMING_FAIL | Latch |
| ptm | Fault | D+22 | 4 | Pan/Tilt | 0x0400 | MOVE_TIMEOUT | Latch |
| ptm | Fault | D+22 | 5 | Pan/Tilt | 0x0500 | ENCODER_FAULT | Latch |
| ptm | Fault | D+22 | 6 | Pan/Tilt | 0x0600 | LIMIT_FAULT | Latch |
| ptm | Fault | D+22 | 7 | Pan/Tilt | 0x0700 | TARGET_NOT_REACHED | Latch |
| ptm | Fault | D+22 | 8 | Pan/Tilt | 0x0800 | DRIVER_PROTECTION | Latch |
| ptm | Fault | D+22 | 9 | Pan/Tilt | 0x0900 | PTM_COMM_TIMEOUT | Auto |
| ptm | Warning | D+22 | 1 | Pan/Tilt | 0x0001 | POSITION_ERROR_HIGH | Auto |
| ptm | Warning | D+22 | 2 | Pan/Tilt | 0x0002 | MOVE_TIME_HIGH | Auto |
| ptm | Warning | D+22 | 3 | Pan/Tilt | 0x0003 | LIMIT_NEAR | Auto |
| ptm | Warning | D+22 | 4 | Pan/Tilt | 0x0004 | MOTOR_TEMP_HIGH | Auto |
| ptm | Warning | D+22 | 5 | Pan/Tilt | 0x0005 | COMM_RETRY_HIGH | Auto |
| ptm | Warning | D+22 | 6 | Pan/Tilt | 0x0006 | ENCODER_JITTER_HIGH | Auto |
| vision | Fault | D+23 | 1 | Vision Common | 0x0100 | REQUIRED_CAMERA_UNAVAILABLE | Auto |
| vision | Fault | D+23 | 3 | Vision Common | 0x0300 | VISION_PIPELINE_STOPPED | Latch |
| vision | Fault | D+23 | 4 | Vision Common | 0x0400 | COORD_TRANSFORM_FAIL | Latch |
| vision | Fault | D+23 | 5 | Vision Common | 0x0500 | CALIBRATION_SET_MISMATCH | Latch |
| vision | Fault | D+23 | 6 | Vision Common | 0x0600 | VISION_RESULT_TIMEOUT | Auto |
| vision | Fault | D+23 | 7 | Vision Common | 0x0700 | VISION_COMM_TIMEOUT | Auto |
| vision | Fault | D+23 | 8 | Vision Common | 0x0800 | VISION_STATUS_STALE | Auto |
| vision | Fault | D+23 | 9 | Vision Common | 0x0900 | VISION_COMM_LOST | Auto |
| vision | Fault | D+23 | 17 | Camera 1 | 0x1100 | CAMERA_DISCONNECTED | Auto |
| vision | Fault | D+23 | 18 | Camera 1 | 0x1200 | FRAME_TIMEOUT | Auto |
| vision | Fault | D+23 | 19 | Camera 1 | 0x1300 | CAMERA_INIT_FAIL | Latch |
| vision | Fault | D+23 | 20 | Camera 1 | 0x1400 | CALIBRATION_DATA_MISSING | Latch |
| vision | Fault | D+23 | 21 | Camera 1 | 0x1500 | CALIBRATION_DATA_INVALID | Latch |
| vision | Fault | D+23 | 22 | Camera 1 | 0x1600 | FORMAT_INVALID | Latch |
| vision | Fault | D+23 | 23 | Camera 1 | 0x1700 | TIMESTAMP_INVALID | Auto |
| vision | Fault | D+23 | 24 | Camera 1 | 0x1800 | CAMERA_DRIVER_FAULT | Latch |
| vision | Fault | D+23 | 33 | Camera 2 | 0x2100 | CAMERA_DISCONNECTED | Auto |
| vision | Fault | D+23 | 34 | Camera 2 | 0x2200 | FRAME_TIMEOUT | Auto |
| vision | Fault | D+23 | 35 | Camera 2 | 0x2300 | CAMERA_INIT_FAIL | Latch |
| vision | Fault | D+23 | 36 | Camera 2 | 0x2400 | CALIBRATION_DATA_MISSING | Latch |
| vision | Fault | D+23 | 37 | Camera 2 | 0x2500 | CALIBRATION_DATA_INVALID | Latch |
| vision | Fault | D+23 | 38 | Camera 2 | 0x2600 | FORMAT_INVALID | Latch |
| vision | Fault | D+23 | 39 | Camera 2 | 0x2700 | TIMESTAMP_INVALID | Auto |
| vision | Fault | D+23 | 40 | Camera 2 | 0x2800 | CAMERA_DRIVER_FAULT | Latch |
| vision | Fault | D+23 | 82 | Tracker | 0x5200 | POSITION_CALC_FAIL | Auto |
| vision | Fault | D+23 | 83 | Tracker | 0x5300 | POSE_CALC_FAIL | Auto |
| vision | Fault | D+23 | 84 | Tracker | 0x5400 | TRACKER_CALIB_INVALID | Latch |
| vision | Fault | D+23 | 85 | Tracker | 0x5500 | TRACKING_DATA_TIMEOUT | Auto |
| vision | Fault | D+23 | 86 | Tracker | 0x5600 | COORDINATE_INVALID | Latch |
| vision | Fault | D+23 | 87 | Tracker | 0x5700 | POSITION_JUMP_INVALID | Auto |
| vision | Warning | D+23 | 2 | Vision Common | 0x0002 | VISION_PROCESSING_SLOW | Auto |
| vision | Warning | D+23 | 3 | Vision Common | 0x0003 | VISION_DEGRADED | Auto |
| vision | Warning | D+23 | 5 | Vision Common | 0x0005 | COORDINATE_JITTER_HIGH | Auto |
| vision | Warning | D+23 | 17 | Camera 1 | 0x0011 | FPS_LOW | Auto |
| vision | Warning | D+23 | 18 | Camera 1 | 0x0012 | FRAME_DROP_HIGH | Auto |
| vision | Warning | D+23 | 19 | Camera 1 | 0x0013 | FRAME_LATENCY_HIGH | Auto |
| vision | Warning | D+23 | 20 | Camera 1 | 0x0014 | EXPOSURE_BAD | Auto |
| vision | Warning | D+23 | 21 | Camera 1 | 0x0015 | IMAGE_QUALITY_LOW | Auto |
| vision | Warning | D+23 | 22 | Camera 1 | 0x0016 | CAMERA_TEMP_HIGH | Auto |
| vision | Warning | D+23 | 23 | Camera 1 | 0x0017 | VISION_CONFIDENCE_LOW | Auto |
| vision | Warning | D+23 | 24 | Camera 1 | 0x0018 | FRAME_TIMEOUT_TRANSIENT | Auto |
| vision | Warning | D+23 | 33 | Camera 2 | 0x0021 | FPS_LOW | Auto |
| vision | Warning | D+23 | 34 | Camera 2 | 0x0022 | FRAME_DROP_HIGH | Auto |
| vision | Warning | D+23 | 35 | Camera 2 | 0x0023 | FRAME_LATENCY_HIGH | Auto |
| vision | Warning | D+23 | 36 | Camera 2 | 0x0024 | EXPOSURE_BAD | Auto |
| vision | Warning | D+23 | 37 | Camera 2 | 0x0025 | IMAGE_QUALITY_LOW | Auto |
| vision | Warning | D+23 | 38 | Camera 2 | 0x0026 | CAMERA_TEMP_HIGH | Auto |
| vision | Warning | D+23 | 39 | Camera 2 | 0x0027 | VISION_CONFIDENCE_LOW | Auto |
| vision | Warning | D+23 | 40 | Camera 2 | 0x0028 | FRAME_TIMEOUT_TRANSIENT | Auto |
| vision | Warning | D+23 | 81 | Tracker | 0x0051 | TRACK_CONFIDENCE_LOW | Auto |
| vision | Warning | D+23 | 82 | Tracker | 0x0052 | MARKER_COUNT_LOW | Auto |
| vision | Warning | D+23 | 83 | Tracker | 0x0053 | POSITION_JITTER_HIGH | Auto |
| vision | Warning | D+23 | 84 | Tracker | 0x0054 | TRACKING_LATENCY_HIGH | Auto |
| vision | Warning | D+23 | 85 | Tracker | 0x0055 | MARKER_LOST_TRANSIENT | Auto |
| vision | Warning | D+23 | 86 | Tracker | 0x0056 | POSE_ERROR_HIGH | Auto |
| plc | Fault | D+24 | 1 | PLC Communication | 0x0100 | PLC_DISCONNECTED | Auto |
| plc | Fault | D+24 | 2 | PLC Communication | 0x0200 | PLC_HEARTBEAT_TIMEOUT | Auto |
| plc | Fault | D+24 | 3 | PLC Communication | 0x0300 | XGT_COMM_FAIL | Auto |
| plc | Fault | D+24 | 4 | PLC Communication | 0x0400 | PLC_READ_FAIL | Auto |
| plc | Fault | D+24 | 5 | PLC Communication | 0x0500 | PLC_WRITE_FAIL | Auto |
| plc | Fault | D+24 | 6 | PLC Communication | 0x0600 | PROTOCOL_RESPONSE_ERROR | Auto |
| plc | Warning | D+24 | 1 | PLC Communication | 0x0001 | PLC_RESPONSE_SLOW | Auto |
| plc | Warning | D+24 | 2 | PLC Communication | 0x0002 | PLC_COMM_RETRY_HIGH | Auto |
| plc | Warning | D+24 | 3 | PLC Communication | 0x0003 | PLC_TIMEOUT_TRANSIENT | Auto |
| plc | Warning | D+24 | 4 | PLC Communication | 0x0004 | PLC_HEARTBEAT_DELAY | Auto |
