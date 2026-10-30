# PLC 알람 v5.2 복원 규격

알람 통신 형식만 `../1. 계획,설계/트래커 PLC 통신 맵 v5.2.xlsx` 기준으로 복원했습니다. 명령·상태·RUN_ENABLE 출력 쓰기 허가와 내부 통신 타임아웃 판정·재시도·자동 복구·장비 Fault 래치는 현행 동작을 유지합니다. 방향별 100 WORD/200 BYTE와 리틀엔디언 전송도 유지합니다.

| 상대 주소 | 내용 |
| --- | --- |
| D+20 | PROCESS_STOP 및 기능별 Fault/Warning 요약 비트 |
| D+21 | 대표 Fault 코드: 1001 Controller, 1101 PLC, 2001 Vision, 2101 Camera 1, 3001 Tracker, 4001 PTM, 6001 Recipe, 6101 Calibration |
| D+22 | 대표 Warning 코드: 1000, 1100, 2000, 2100, 3000, 4000, 6000, 6100 |
| D+23 | 마지막 Fault 코드 (바이패스 장치 이력은 PLC 표시에서 제외) |
| D+24 | 알람 순번; 최근 원인 발생 감지 정책과 16비트 순환 유지 |
| D+25 / D+26 | 장치별 Fault / Warning Summary 비트맵 |
| D+27 / D+28 | PLC로 표현되는 활성 Fault / Warning 비트 개수 |
| D+50 / D+51 | Camera 1 Warning / Fault |
| D+52~D+57 | 예약, 항상 0; Camera 2~4 내부 진단은 유지 |
| D+58 / D+59 | Vision Common Warning / Fault |
| D+60 / D+61 | Tracker Warning / Fault |
| D+62 / D+63 | Pan/Tilt 통합 Warning / Fault, 같은 비트는 OR 후 1회 계산 |
| D+64~D+67 | 예약 또는 GPIO Laser 진단 미지원, 항상 0 |
| D+68 / D+69 | Controller Warning / Fault |
| D+70 / D+71 | PLC Communication Warning / Fault |
| D+72 / D+73 | Recipe Warning / Fault |
| D+74 / D+75 | Calibration Warning / Fault |

## 최근 통신 진단과의 호환

- PTM 내부 타임아웃 bit 8은 기존 `D+63.0 PTM_COMM_LOST`로 통합합니다.
- Vision 내부 통신 타임아웃 bit 6, 상태 지연 bit 7, 연결 끊김 bit 8은 기존 `D+59.5 VISION_RESULT_TIMEOUT`으로 통합합니다. 결과 생성 타임아웃 판정도 유지합니다.
- 복구 시 통신 알람은 자동 해제하며 Motor/Driver, 호밍·이동 제한시간, 설정·보정 Fault는 기존 래치/수동 리셋 동작을 유지합니다.
- 내부 진단 비트는 변경하지 않습니다. 알람 이력은 `map_version: v5.2`, 실제 PLC 주소·비트 및 `diagnostic_bit`를 기록합니다. PLC 할당이 없는 내부 알람은 주소를 비워 둡니다. 기존 v5/v6/v7 이력은 변환하거나 삭제하지 않습니다.
- `alarms.plc_alarm_format`은 `bitmap_v5_2`입니다. `plc_fault_code`, `plc_warning_code`, `plc_last_fault_code`는 각각 실제 D+21/22/23 값입니다. v7 모듈별 BYTE WORD 출력은 사용하지 않습니다.
- 원본 v5.2 파일의 출력 시트에서 Camera 2~4가 Reserved이므로 상세 시트에 남아 있는 Camera 2 정의도 활성 맵에서는 Reserved로 처리합니다. Laser는 현행 GPIO 명령 출력 전용으로, 피드백 알람을 생성하지 않습니다.

Core와 Monitor의 활성 맵은 `python tools/sync_plc_alarm_map.py --check`로 검사합니다. 구버전 v6/v7 문서는 이력으로만 보존합니다. 실제 PLC/HMI도 위 비트맵 해석으로 맞춘 후 Core와 Monitor를 함께 갱신해야 합니다.
