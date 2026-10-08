window.TRACK_EYE_PLC_MAP = {
  "sourceFile": "트래커 PLC 통신 맵 v3.xlsx",
  "sourceTitle": "Tracker Project PLC ↔ Controller Communication Map v3.0",
  "note": "웹에서는 PLC 기준 주소를 적용하여 Dnnn 형식으로 표시합니다.",
  "sheets": [
    {
      "key": "01_PLC_to_Controller",
      "label": "1. PLC → Controller",
      "title": "PLC → Controller 전체 주소맵",
      "subtitle": "PLC가 Controller에 운전 허용, 설정값, Recipe/Point, 일회성 명령을 전달",
      "type": "table",
      "base": "read",
      "headers": [
        "영역",
        "주소",
        "Bit",
        "항목",
        "설명",
        "비고",
        "Data Type",
        "신호 성격",
        "조건",
        "설정/값 범위"
      ],
      "rows": [
        [
          "시스템",
          "D+00",
          "0",
          "RUN_ENABLE",
          "Controller 정상 운전 허용",
          "0이면 Standby/Safe 상태",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템",
          "D+00",
          "1",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "2",
          "TRACKING_ENABLE",
          "Vision/Tracker 활성 요구",
          "정상운전 시 1 권장",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "시스템",
          "D+00",
          "3",
          "LASER_ENABLE",
          "Laser/Motor 출력 기능 허용",
          "실제 출력은 Controller 조건과 AND",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "시스템",
          "D+00",
          "4",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "5",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "6",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "7",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "8",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "9",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "A",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "B",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "C",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "D",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "E",
          "Reserved",
          "예약",
          "항상 0",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+00",
          "F",
          "FORCE_STOP",
          "Pan/Tilt/Laser 등 동작 강제 중단",
          "최우선 공정 정지. 안전 E-Stop 대체 금지",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템",
          "D+01",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+02",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+03",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+04",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+05",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "시스템",
          "D+06",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "응답/통신",
          "D+07",
          "-",
          "PLC_HEARTBEAT",
          "PLC가 주기마다 +1",
          "65535 다음 0",
          "UINT16",
          "Counter",
          "전체",
          "0~65535"
        ],
        [
          "설정",
          "D+08",
          "-",
          "TRACKING_TOLERANCE",
          "Tracking 위치 판단 허용오차",
          "1 = 0.01 mm, 범위 밖이면 기본값",
          "UINT16",
          "Parameter",
          "자동",
          "1~10000"
        ],
        [
          "설정",
          "D+09",
          "-",
          "LASER_TOLERANCE",
          "Pan/Tilt 목표 허용오차",
          "1 = 0.01°, 범위 밖이면 기본값",
          "UINT16",
          "Parameter",
          "자동",
          "1~10000"
        ],
        [
          "일반 명령",
          "D+10",
          "-",
          "REQ_RECIPE_ID",
          "PLC 요구 Recipe ID",
          "차종/작업 Recipe",
          "UINT16",
          "Parameter",
          "자동",
          "1~65535"
        ],
        [
          "일반 명령",
          "D+11",
          "-",
          "REQ_POINT_ID",
          "PLC 요구 체결 Point ID",
          "현재 목표 Point",
          "UINT16",
          "Parameter",
          "자동",
          "1~65535"
        ],
        [
          "일반 명령",
          "D+12",
          "-",
          "COMMAND_CODE",
          "일회성 명령 코드",
          "명령표 참조",
          "UINT16",
          "Command",
          "자동",
          "0~65535"
        ],
        [
          "일반 명령",
          "D+13",
          "-",
          "COMMAND_SEQ",
          "새 명령마다 +1",
          "동일 명령 중복실행 방지",
          "UINT16",
          "Counter",
          "자동",
          "0~65535"
        ],
        [
          "일반 명령",
          "D+14",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "일반 명령",
          "D+15",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "일반 명령",
          "D+16",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "일반 명령",
          "D+17",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "일반 명령",
          "D+18",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "일반 명령",
          "D+19",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+20",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+21",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+22",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+23",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+24",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+25",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+26",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+27",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+28",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+29",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+30",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+31",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+32",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+33",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+34",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+35",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+36",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+37",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+38",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+39",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "관리자",
          "D+40",
          "-",
          "ADMIN_COMMAND",
          "관리자/Calibration 명령",
          "일반 운전 명령과 분리",
          "UINT16",
          "Command",
          "비운전/관리자",
          "0~65535"
        ],
        [
          "관리자",
          "D+41",
          "-",
          "ADMIN_SEQ",
          "관리자 명령 Sequence",
          "새 명령마다 +1",
          "UINT16",
          "Counter",
          "비운전/관리자",
          "0~65535"
        ],
        [
          "관리자",
          "D+42",
          "-",
          "ADMIN_PARAM1",
          "관리자 Parameter 1",
          "명령별 정의",
          "INT16/UINT16",
          "Parameter",
          "관리자",
          "-32768~65535"
        ],
        [
          "관리자",
          "D+43",
          "-",
          "ADMIN_PARAM2",
          "관리자 Parameter 2",
          "명령별 정의",
          "INT16/UINT16",
          "Parameter",
          "관리자",
          "-32768~65535"
        ],
        [
          "관리자",
          "D+44",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자",
          "D+45",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자",
          "D+46",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자",
          "D+47",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자",
          "D+48",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자",
          "D+49",
          "-",
          "TEACH_ENABLE_KEY",
          "Teach/Calibration 진입 허용 키",
          "보안 Password가 아닌 오조작 방지용",
          "UINT16",
          "Interlock Key",
          "관리자",
          "0~65535"
        ]
      ]
    },
    {
      "key": "02_Controller_to_PLC",
      "label": "2. Controller → PLC",
      "title": "Controller → PLC 전체 주소맵",
      "subtitle": "Controller가 실제 상태, Tracking/Position 결과, 명령 응답, 알람 요약 및 상세 진단을 PLC에 전달",
      "type": "table",
      "base": "write",
      "headers": [
        "영역",
        "주소",
        "Bit",
        "항목",
        "설명",
        "비고",
        "Data Type",
        "신호 성격",
        "조건",
        "설정/값 범위"
      ],
      "rows": [
        [
          "시스템 상태",
          "D+00",
          "0",
          "CONTROLLER_ONLINE",
          "Controller Application 정상 동작",
          "Heartbeat와 함께 판단",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "1",
          "SYSTEM_READY",
          "정상 운전 가능 상태",
          "치명 Fault 없음/필수 초기화 완료",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "2",
          "TRACKING_ACTIVE",
          "Tracking 실제 실행 중",
          "PLC 요구가 아닌 실제 상태",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "3",
          "HOMING",
          "Homing 진행 중",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "4",
          "MOTION_MOVING",
          "Pan/Tilt 이동 중",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "5",
          "COMMAND_BUSY",
          "PLC 명령 처리 중",
          "D+14 상태와 연계",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "6",
          "LASER_ON",
          "Laser 실제 출력 상태",
          "가능하면 Feedback 기준",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "7",
          "FAULT_ACTIVE",
          "하나 이상의 Fault 존재",
          "D+20.1과 의미 동일/요약",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "8",
          "WARNING_ACTIVE",
          "하나 이상의 Warning 존재",
          "D+20.0과 의미 동일/요약",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "9",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템 상태",
          "D+00",
          "A",
          "HOMED",
          "원점 정보 유효",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "B",
          "STOP_ACTIVE",
          "Controller 동작정지 상태",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "시스템 상태",
          "D+00",
          "C",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템 상태",
          "D+00",
          "D",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템 상태",
          "D+00",
          "E",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "시스템 상태",
          "D+00",
          "F",
          "FORCE_STOP_ACTIVE",
          "PLC FORCE_STOP 적용 상태",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "공정 상태",
          "D+01",
          "0",
          "CAMERA_READY",
          "Camera 준비 완료",
          "Required Camera",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "공정 상태",
          "D+01",
          "1",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          0
        ],
        [
          "공정 상태",
          "D+01",
          "2",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          0
        ],
        [
          "공정 상태",
          "D+01",
          "3",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          0
        ],
        [
          "공정 상태",
          "D+01",
          "4",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          0
        ],
        [
          "공정 상태",
          "D+01",
          "5",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          0
        ],
        [
          "공정 상태",
          "D+01",
          "6",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          0
        ],
        [
          "공정 상태",
          "D+01",
          "7",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          0
        ],
        [
          "공정 상태",
          "D+01",
          "8",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "공정 상태",
          "D+01",
          "9",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "공정 상태",
          "D+01",
          "A",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "공정 상태",
          "D+01",
          "B",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "공정 상태",
          "D+01",
          "C",
          "LASER_TARGET_READY",
          "Pan/Tilt 목표 위치 도달 및 Settling 완료",
          "",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "공정 상태",
          "D+01",
          "D",
          "POSITION_OK",
          "Nutrunner가 목표 체결 영역 안",
          "최종 체결허가는 PLC가 공정조건과 AND",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "공정 상태",
          "D+01",
          "E",
          "POSITION_STABLE",
          "일정 시간 POSITION_OK 유지",
          "순간 진입 방지",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "공정 상태",
          "D+01",
          "F",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          0
        ],
        [
          "운전 상태",
          "D+02",
          "-",
          "CONTROLLER_STATE",
          "Controller 전체 상태",
          "상태 코드표 참조",
          "ENUM",
          "State",
          "전체",
          "0~8"
        ],
        [
          "운전 상태",
          "D+03",
          "-",
          "TRACKING_STATE",
          "Tracking 상태",
          "상태 코드표 참조",
          "ENUM",
          "State",
          "전체",
          "0~6"
        ],
        [
          "운전 상태",
          "D+04",
          "-",
          "POSITION_ERROR",
          "현재 체결 목표와 Tracker 거리 오차",
          "1 = 0.01 mm",
          "UINT16",
          "Value",
          "자동",
          "0~65535"
        ],
        [
          "운전 상태",
          "D+05",
          "-",
          "PAN_ERROR",
          "Pan 목표 위치 오차",
          "1 = 0.01°",
          "UINT16",
          "Value",
          "자동",
          "0~65535"
        ],
        [
          "운전 상태",
          "D+06",
          "-",
          "TILT_ERROR",
          "Tilt 목표 위치 오차",
          "1 = 0.01°",
          "UINT16",
          "Value",
          "자동",
          "0~65535"
        ],
        [
          "응답/통신",
          "D+07",
          "-",
          "CONTROLLER_HEARTBEAT_RETURN",
          "PLC에서 받은 HeartBeat 값 리턴",
          "65535 다음 0",
          "UINT16",
          "Counter",
          "전체",
          "0~65535"
        ],
        [
          "응답/설정",
          "D+08",
          "-",
          "ACTIVE_TRACKING_TOLERANCE",
          "실제 적용된 Tracking 허용오차",
          "1 = 0.01 mm",
          "UINT16",
          "Response",
          "전체",
          "1~10000"
        ],
        [
          "응답/설정",
          "D+09",
          "-",
          "ACTIVE_LASER_TOLERANCE",
          "실제 적용된 Laser/PanTilt 허용오차",
          "1 = 0.01°",
          "UINT16",
          "Response",
          "전체",
          "1~10000"
        ],
        [
          "명령 응답",
          "D+10",
          "-",
          "ACTIVE_RECIPE_ID",
          "실제 적용 중인 Recipe",
          "",
          "UINT16",
          "Response",
          "자동",
          "1~65535"
        ],
        [
          "명령 응답",
          "D+11",
          "-",
          "ACTIVE_POINT_ID",
          "실제 적용 중인 Point",
          "",
          "UINT16",
          "Response",
          "자동",
          "1~65535"
        ],
        [
          "명령 응답",
          "D+12",
          "-",
          "LAST_COMMAND_CODE",
          "마지막 수신/처리 명령 코드",
          "",
          "UINT16",
          "Response",
          "자동",
          "0~65535"
        ],
        [
          "명령 응답",
          "D+13",
          "-",
          "LAST_COMMAND_SEQ",
          "마지막 처리 Sequence",
          "PLC ACK 확인용",
          "UINT16",
          "Response",
          "자동",
          "0~65535"
        ],
        [
          "명령 응답",
          "D+14",
          "-",
          "COMMAND_STATUS",
          "명령 처리 상태",
          "상태 코드표 참조",
          "ENUM",
          "Response",
          "자동",
          "0~5"
        ],
        [
          "명령 응답",
          "D+15",
          "-",
          "COMMAND_RESULT",
          "명령 처리 결과",
          "결과 코드표 참조",
          "ENUM",
          "Response",
          "자동",
          "0~65535"
        ],
        [
          "Camera 상태",
          "D+16",
          "0",
          "CAMERA_REQUIRED_MASK",
          "고정 사용 Camera 1 Mask",
          "bit0 = Camera 1; bit1~3 = 0",
          "BITMAP",
          "Level",
          "자동",
          "0x0001"
        ],
        [
          "Camera 상태",
          "D+17",
          "0",
          "CAMERA_ONLINE_MASK",
          "현재 Online Camera Mask",
          "bit0 = Camera 1; bit1~3 = 0",
          "BITMAP",
          "Level",
          "전체",
          "0x0000~0x0001"
        ],
        [
          "Camera 상태",
          "D+18",
          "0",
          "CAMERA_VALID_MASK",
          "Vision 계산에 사용 가능한 Camera Mask",
          "bit0 = Camera 1; bit1~3 = 0",
          "BITMAP",
          "Level",
          "자동",
          "0x0000~0x0001"
        ],
        [
          "명령 응답",
          "D+19",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "알람 요약",
          "D+20",
          "0",
          "WARNING_ACTIVE",
          "Warning 하나 이상",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "1",
          "FAULT_ACTIVE",
          "Fault 하나 이상",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "2",
          "PROCESS_STOP",
          "현재 공정 진행 불가",
          "PLC 체결허가 차단 핵심",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "3",
          "VISION_DEGRADED",
          "Vision 일부 성능 저하",
          "운전 가능 여부는 Controller 판단",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "4",
          "VISION_FAULT",
          "Vision 기능 수행 불가",
          "",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "5",
          "TRACKING_FAULT",
          "Tracker 기능 수행 불가",
          "",
          "BOOL",
          "Level",
          "자동",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "6",
          "MOTION_FAULT",
          "Pan/Tilt 정상 동작 불가",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "7",
          "LASER_FAULT",
          "Laser 기능 이상",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "8",
          "CONTROLLER_FAULT",
          "Controller 자체 이상",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "9",
          "PLC_COMM_FAULT",
          "PLC 통신 이상",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "A",
          "CONFIG_FAULT",
          "Recipe/설정 이상",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "B",
          "CALIBRATION_FAULT",
          "Calibration 이상",
          "",
          "BOOL",
          "Level",
          "전체",
          "0/1"
        ],
        [
          "알람 요약",
          "D+20",
          "C",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "알람 요약",
          "D+20",
          "D",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "알람 요약",
          "D+20",
          "E",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "알람 요약",
          "D+20",
          "F",
          "Reserved",
          "예약",
          "",
          "-",
          "-",
          "-",
          "0"
        ],
        [
          "알람 정보",
          "D+21",
          "-",
          "PRIMARY_FAULT_CODE",
          "현재 가장 우선순위 높은 Fault Code",
          "0 = 없음",
          "UINT16",
          "Code",
          "전체",
          "0~65535"
        ],
        [
          "알람 정보",
          "D+22",
          "-",
          "PRIMARY_WARNING_CODE",
          "현재 가장 우선순위 높은 Warning Code",
          "0 = 없음",
          "UINT16",
          "Code",
          "전체",
          "0~65535"
        ],
        [
          "알람 정보",
          "D+23",
          "-",
          "LAST_FAULT_CODE",
          "마지막 발생 Fault Code",
          "이력/HMI 표시용",
          "UINT16",
          "Code",
          "전체",
          "0~65535"
        ],
        [
          "알람 정보",
          "D+24",
          "-",
          "ALARM_SEQUENCE",
          "새로운 Alarm 발생 시 +1",
          "새 알람 감지용",
          "UINT16",
          "Counter",
          "전체",
          "0~65535"
        ],
        [
          "알람 정보",
          "D+25",
          "0~C",
          "DEVICE_FAULT_SUMMARY",
          "Fault 발생 장치 Summary",
          "장치 비트맵표 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "알람 정보",
          "D+26",
          "0~C",
          "DEVICE_WARNING_SUMMARY",
          "Warning 발생 장치 Summary",
          "장치 비트맵표 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "알람 정보",
          "D+27",
          "-",
          "ACTIVE_FAULT_COUNT",
          "현재 Active Fault 개수",
          "",
          "UINT16",
          "Value",
          "전체",
          "0~65535"
        ],
        [
          "알람 정보",
          "D+28",
          "-",
          "ACTIVE_WARNING_COUNT",
          "현재 Active Warning 개수",
          "",
          "UINT16",
          "Value",
          "전체",
          "0~65535"
        ],
        [
          "알람 정보",
          "D+29",
          "-",
          "Reserved",
          "예약",
          "",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+30",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+31",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+32",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+33",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+34",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+35",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+36",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+37",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+38",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "예약",
          "D+39",
          "-",
          "Reserved",
          "향후 Controller→PLC 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "관리자 응답",
          "D+40",
          "-",
          "ADMIN_STATUS",
          "관리자 명령 상태",
          "",
          "ENUM",
          "Response",
          "관리자",
          "0~65535"
        ],
        [
          "관리자 응답",
          "D+41",
          "-",
          "ADMIN_RESULT",
          "관리자 명령 결과",
          "",
          "ENUM",
          "Response",
          "관리자",
          "0~65535"
        ],
        [
          "관리자 응답",
          "D+42",
          "-",
          "ADMIN_ACK_SEQ",
          "마지막 처리 관리자 Sequence",
          "",
          "UINT16",
          "Response",
          "관리자",
          "0~65535"
        ],
        [
          "관리자 응답",
          "D+43",
          "-",
          "TEACH_STATUS",
          "Teach/Calibration 상태",
          "",
          "ENUM",
          "Response",
          "관리자",
          "0~65535"
        ],
        [
          "관리자 응답",
          "D+44",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자 응답",
          "D+45",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자 응답",
          "D+46",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자 응답",
          "D+47",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자 응답",
          "D+48",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "관리자 응답",
          "D+49",
          "-",
          "Reserved",
          "예약",
          "사용 금지",
          "WORD",
          "Reserve",
          "관리자",
          "0"
        ],
        [
          "상세 진단",
          "D+50",
          "0~F",
          "CAMERA_1_WARNING",
          "Camera 1 Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+51",
          "0~F",
          "CAMERA_1_FAULT",
          "Camera 1 Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+52",
          "-",
          "Reserved",
          "Camera 2~4 미사용 (항상 0)",
          "PLC 주소 유지",
          "BITMAP",
          "Reserved",
          "전체",
          "0"
        ],
        [
          "상세 진단",
          "D+53",
          "-",
          "Reserved",
          "Camera 2~4 미사용 (항상 0)",
          "PLC 주소 유지",
          "BITMAP",
          "Reserved",
          "전체",
          "0"
        ],
        [
          "상세 진단",
          "D+54",
          "-",
          "Reserved",
          "Camera 2~4 미사용 (항상 0)",
          "PLC 주소 유지",
          "BITMAP",
          "Reserved",
          "전체",
          "0"
        ],
        [
          "상세 진단",
          "D+55",
          "-",
          "Reserved",
          "Camera 2~4 미사용 (항상 0)",
          "PLC 주소 유지",
          "BITMAP",
          "Reserved",
          "전체",
          "0"
        ],
        [
          "상세 진단",
          "D+56",
          "-",
          "Reserved",
          "Camera 2~4 미사용 (항상 0)",
          "PLC 주소 유지",
          "BITMAP",
          "Reserved",
          "전체",
          "0"
        ],
        [
          "상세 진단",
          "D+57",
          "-",
          "Reserved",
          "Camera 2~4 미사용 (항상 0)",
          "PLC 주소 유지",
          "BITMAP",
          "Reserved",
          "전체",
          "0"
        ],
        [
          "상세 진단",
          "D+58",
          "0~F",
          "VISION_COMMON_WARNING",
          "Vision Common Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+59",
          "0~F",
          "VISION_COMMON_FAULT",
          "Vision Common Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+60",
          "0~F",
          "TRACKER_WARNING",
          "Tracker Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+61",
          "0~F",
          "TRACKER_FAULT",
          "Tracker Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+62",
          "0~F",
          "PAN_WARNING",
          "Pan Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+63",
          "0~F",
          "PAN_FAULT",
          "Pan Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+64",
          "0~F",
          "TILT_WARNING",
          "Tilt Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+65",
          "0~F",
          "TILT_FAULT",
          "Tilt Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+66",
          "0~F",
          "LASER_WARNING",
          "Laser Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+67",
          "0~F",
          "LASER_FAULT",
          "Laser Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+68",
          "0~F",
          "CONTROLLER_WARNING",
          "Controller Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+69",
          "0~F",
          "CONTROLLER_FAULT",
          "Controller Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+70",
          "0~F",
          "PLC_COMMUNICATION_WARNING",
          "PLC Communication Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+71",
          "0~F",
          "PLC_COMMUNICATION_FAULT",
          "PLC Communication Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+72",
          "0~F",
          "RECIPE_WARNING",
          "Recipe Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+73",
          "0~F",
          "RECIPE_FAULT",
          "Recipe Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+74",
          "0~F",
          "CALIBRATION_WARNING",
          "Calibration Warning",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+75",
          "0~F",
          "CALIBRATION_FAULT",
          "Calibration Fault",
          "03_Device_Alarm_Detail 시트 참조",
          "BITMAP",
          "Level",
          "전체",
          "Bit Map"
        ],
        [
          "상세 진단",
          "D+76",
          "-",
          "Reserved",
          "향후 장치 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "상세 진단",
          "D+77",
          "-",
          "Reserved",
          "향후 장치 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "상세 진단",
          "D+78",
          "-",
          "Reserved",
          "향후 장치 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ],
        [
          "상세 진단",
          "D+79",
          "-",
          "Reserved",
          "향후 장치 확장",
          "사용 금지",
          "WORD",
          "Reserve",
          "-",
          "0"
        ]
      ]
    },
    {
      "key": "03_Device_Alarm_Detail",
      "label": "3. Device Alarm Detail",
      "title": "장치별 Warning / Fault 상세 비트맵",
      "subtitle": "같은 장치군은 주소만 다르고 Bit 의미를 동일하게 유지한다. 검출할 수 없는 기능은 Reserved 처리 또는 항상 0.",
      "type": "table",
      "base": "write",
      "headers": [
        "장치군",
        "구분",
        "적용 Word",
        "Bit",
        "알람 항목",
        "권장 등급",
        "Clear 방식",
        "Process Stop 권장",
        "설명/판단 기준"
      ],
      "rows": [
        [
          "Camera 1",
          "Warning",
          "D+50",
          "0",
          "FPS_LOW",
          "Warning",
          "Auto",
          "아니오",
          "설정 FPS 대비 지속 저하"
        ],
        [
          "Camera 1",
          "Warning",
          "D+50",
          "1",
          "FRAME_DROP_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "Frame Drop 비율 증가"
        ],
        [
          "Camera 1",
          "Warning",
          "D+50",
          "2",
          "FRAME_LATENCY_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "Frame 처리/수신 지연 증가"
        ],
        [
          "Camera 1",
          "Warning",
          "D+50",
          "3",
          "EXPOSURE_BAD",
          "Warning",
          "Auto",
          "아니오",
          "과노출/저노출 등 영상 상태 불량"
        ],
        [
          "Camera 1",
          "Warning",
          "D+50",
          "4",
          "IMAGE_QUALITY_LOW",
          "Warning",
          "Auto",
          "아니오",
          "Blur/Contrast 등 영상 품질 저하"
        ],
        [
          "Camera 1",
          "Warning",
          "D+50",
          "5",
          "CAMERA_TEMP_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "온도 정보 지원 시 사용"
        ],
        [
          "Camera 1",
          "Warning",
          "D+50",
          "6",
          "VISION_CONFIDENCE_LOW",
          "Warning",
          "Auto",
          "아니오",
          "Vision Confidence 기준 미달"
        ],
        [
          "Camera 1",
          "Warning",
          "D+50",
          "7",
          "FRAME_TIMEOUT_TRANSIENT",
          "Warning",
          "Auto",
          "아니오",
          "순간적 Frame Timeout"
        ],
        [
          "Camera 1",
          "Fault",
          "D+51",
          "0",
          "CAMERA_DISCONNECTED",
          "Fault",
          "Latch/Auto 선택",
          "예",
          "Camera 연결 끊김"
        ],
        [
          "Camera 1",
          "Fault",
          "D+51",
          "1",
          "FRAME_TIMEOUT",
          "Fault",
          "Auto 또는 Latch",
          "예",
          "설정 시간 이상 Frame 미수신"
        ],
        [
          "Camera 1",
          "Fault",
          "D+51",
          "2",
          "CAMERA_INIT_FAIL",
          "Fault",
          "Latch",
          "예",
          "Camera 초기화 실패"
        ],
        [
          "Camera 1",
          "Fault",
          "D+51",
          "3",
          "CALIBRATION_DATA_MISSING",
          "Fault",
          "Latch",
          "예",
          "해당 Camera Calibration 없음"
        ],
        [
          "Camera 1",
          "Fault",
          "D+51",
          "4",
          "CALIBRATION_DATA_INVALID",
          "Fault",
          "Latch",
          "예",
          "Calibration 데이터 오류"
        ],
        [
          "Camera 1",
          "Fault",
          "D+51",
          "5",
          "FORMAT_INVALID",
          "Fault",
          "Latch",
          "예",
          "해상도/Pixel Format 불일치"
        ],
        [
          "Camera 1",
          "Fault",
          "D+51",
          "6",
          "TIMESTAMP_INVALID",
          "Fault",
          "Auto/Latch",
          "조건부",
          "Timestamp 비정상"
        ],
        [
          "Camera 1",
          "Fault",
          "D+51",
          "7",
          "CAMERA_DRIVER_FAULT",
          "Fault",
          "Latch",
          "예",
          "Driver/SDK 오류"
        ],
        [
          "Vision Common",
          "Warning",
          "D+58",
          "0",
          "Reserved",
          "Reserved",
          "Auto",
          "아니오",
          "Camera 1대 고정; 항상 0"
        ],
        [
          "Vision Common",
          "Warning",
          "D+58",
          "1",
          "VISION_PROCESSING_SLOW",
          "Warning",
          "Auto",
          "아니오",
          "Vision 처리시간 증가"
        ],
        [
          "Vision Common",
          "Warning",
          "D+58",
          "2",
          "VISION_DEGRADED",
          "Warning",
          "Auto",
          "아니오",
          "Camera 1 처리 품질 저하"
        ],
        [
          "Vision Common",
          "Warning",
          "D+58",
          "3",
          "Reserved",
          "Reserved",
          "Auto",
          "아니오",
          "Camera 1대 고정; 항상 0"
        ],
        [
          "Vision Common",
          "Warning",
          "D+58",
          "4",
          "COORDINATE_JITTER_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "계산 좌표 흔들림 증가"
        ],
        [
          "Vision Common",
          "Fault",
          "D+59",
          "0",
          "CAMERA_1_UNAVAILABLE",
          "Fault",
          "Auto/Latch",
          "예",
          "Camera 1 영상 사용 불가"
        ],
        [
          "Vision Common",
          "Fault",
          "D+59",
          "1",
          "Reserved",
          "Reserved",
          "Latch",
          "예",
          "Camera 1대 고정; 항상 0"
        ],
        [
          "Vision Common",
          "Fault",
          "D+59",
          "2",
          "VISION_PIPELINE_STOPPED",
          "Fault",
          "Latch",
          "예",
          "Vision Pipeline 정지"
        ],
        [
          "Vision Common",
          "Fault",
          "D+59",
          "3",
          "COORD_TRANSFORM_FAIL",
          "Fault",
          "Latch",
          "예",
          "좌표계 변환 실패"
        ],
        [
          "Vision Common",
          "Fault",
          "D+59",
          "4",
          "CALIBRATION_SET_MISMATCH",
          "Fault",
          "Latch",
          "예",
          "Camera Calibration 조합 불일치"
        ],
        [
          "Vision Common",
          "Fault",
          "D+59",
          "5",
          "VISION_RESULT_TIMEOUT",
          "Fault",
          "Auto/Latch",
          "예",
          "Vision 결과 생성 Timeout"
        ],
        [
          "Tracker",
          "Warning",
          "D+60",
          "0",
          "TRACK_CONFIDENCE_LOW",
          "Warning",
          "Auto",
          "아니오",
          "Tracking Confidence 낮음"
        ],
        [
          "Tracker",
          "Warning",
          "D+60",
          "1",
          "MARKER_COUNT_LOW",
          "Warning",
          "Auto",
          "아니오",
          "필요 Marker 검출 수 부족"
        ],
        [
          "Tracker",
          "Warning",
          "D+60",
          "2",
          "POSITION_JITTER_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "Tracking 위치 흔들림 증가"
        ],
        [
          "Tracker",
          "Warning",
          "D+60",
          "3",
          "TRACKING_LATENCY_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "Tracking 처리 지연 증가"
        ],
        [
          "Tracker",
          "Warning",
          "D+60",
          "4",
          "MARKER_LOST_TRANSIENT",
          "Warning",
          "Auto",
          "아니오",
          "순간 Marker 유실"
        ],
        [
          "Tracker",
          "Warning",
          "D+60",
          "5",
          "POSE_ERROR_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "Pose 오차 증가"
        ],
        [
          "Tracker",
          "Fault",
          "D+61",
          "0",
          "TRACKER_LOST",
          "Fault",
          "Auto/Latch",
          "예",
          "설정 시간 이상 Tracker 유실"
        ],
        [
          "Tracker",
          "Fault",
          "D+61",
          "1",
          "POSITION_CALC_FAIL",
          "Fault",
          "Auto/Latch",
          "예",
          "위치 계산 불가"
        ],
        [
          "Tracker",
          "Fault",
          "D+61",
          "2",
          "POSE_CALC_FAIL",
          "Fault",
          "Auto/Latch",
          "예",
          "Pose 계산 불가"
        ],
        [
          "Tracker",
          "Fault",
          "D+61",
          "3",
          "TRACKER_CALIB_INVALID",
          "Fault",
          "Latch",
          "예",
          "Tracker Calibration 오류"
        ],
        [
          "Tracker",
          "Fault",
          "D+61",
          "4",
          "TRACKING_DATA_TIMEOUT",
          "Fault",
          "Auto/Latch",
          "예",
          "Tracking Data Timeout"
        ],
        [
          "Tracker",
          "Fault",
          "D+61",
          "5",
          "COORDINATE_INVALID",
          "Fault",
          "Latch",
          "예",
          "NaN/범위 밖 등 비정상 좌표"
        ],
        [
          "Tracker",
          "Fault",
          "D+61",
          "6",
          "POSITION_JUMP_INVALID",
          "Fault",
          "Auto/Latch",
          "예",
          "비정상 Position Jump"
        ],
        [
          "Pan/Tilt",
          "Warning",
          "D+62 / D+64",
          "0",
          "POSITION_ERROR_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "목표 대비 위치오차 증가"
        ],
        [
          "Pan/Tilt",
          "Warning",
          "D+62 / D+64",
          "1",
          "MOVE_TIME_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "평균 이동시간 증가"
        ],
        [
          "Pan/Tilt",
          "Warning",
          "D+62 / D+64",
          "2",
          "LIMIT_NEAR",
          "Warning",
          "Auto",
          "아니오",
          "소프트/하드 Limit 근접"
        ],
        [
          "Pan/Tilt",
          "Warning",
          "D+62 / D+64",
          "3",
          "MOTOR_TEMP_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "온도 정보 지원 시 사용"
        ],
        [
          "Pan/Tilt",
          "Warning",
          "D+62 / D+64",
          "4",
          "COMM_RETRY_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "모터 통신 Retry 증가"
        ],
        [
          "Pan/Tilt",
          "Warning",
          "D+62 / D+64",
          "5",
          "ENCODER_JITTER_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "Encoder 값 흔들림"
        ],
        [
          "Pan/Tilt",
          "Fault",
          "D+63 / D+65",
          "0",
          "MOTOR_COMM_LOST",
          "Fault",
          "Latch/Auto",
          "예",
          "모터/드라이버 통신 끊김"
        ],
        [
          "Pan/Tilt",
          "Fault",
          "D+63 / D+65",
          "1",
          "MOTOR_DRIVER_FAULT",
          "Fault",
          "Latch",
          "예",
          "Motor/Driver Fault"
        ],
        [
          "Pan/Tilt",
          "Fault",
          "D+63 / D+65",
          "2",
          "HOMING_FAIL",
          "Fault",
          "Latch",
          "예",
          "Homing 실패/Timeout"
        ],
        [
          "Pan/Tilt",
          "Fault",
          "D+63 / D+65",
          "3",
          "MOVE_TIMEOUT",
          "Fault",
          "Latch",
          "예",
          "목표 위치 이동 Timeout"
        ],
        [
          "Pan/Tilt",
          "Fault",
          "D+63 / D+65",
          "4",
          "ENCODER_FAULT",
          "Fault",
          "Latch",
          "예",
          "Encoder 이상"
        ],
        [
          "Pan/Tilt",
          "Fault",
          "D+63 / D+65",
          "5",
          "LIMIT_FAULT",
          "Fault",
          "Latch",
          "예",
          "Limit 비정상"
        ],
        [
          "Pan/Tilt",
          "Fault",
          "D+63 / D+65",
          "6",
          "TARGET_NOT_REACHED",
          "Fault",
          "Latch",
          "예",
          "목표 위치 도달 실패"
        ],
        [
          "Pan/Tilt",
          "Fault",
          "D+63 / D+65",
          "7",
          "DRIVER_PROTECTION",
          "Fault",
          "Latch",
          "예",
          "과전류 등 Driver 보호동작 지원 시 사용"
        ],
        [
          "Laser",
          "Warning",
          "D+66",
          "0",
          "LASER_LIFETIME_WARN",
          "Warning",
          "Auto",
          "아니오",
          "사용시간 기준이 있을 때 사용"
        ],
        [
          "Laser",
          "Warning",
          "D+66",
          "1",
          "LASER_TEMP_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "온도 정보 지원 시 사용"
        ],
        [
          "Laser",
          "Fault",
          "D+67",
          "0",
          "LASER_COMM_FAULT",
          "Fault",
          "Latch/Auto",
          "조건부",
          "Laser Controller 통신형인 경우"
        ],
        [
          "Laser",
          "Fault",
          "D+67",
          "1",
          "LASER_FEEDBACK_MISMATCH",
          "Fault",
          "Latch",
          "예",
          "출력 명령과 Feedback 불일치"
        ],
        [
          "Laser",
          "Fault",
          "D+67",
          "2",
          "LASER_OUTPUT_FAULT",
          "Fault",
          "Latch",
          "예",
          "출력 검출 기능이 있을 때 사용"
        ],
        [
          "Laser",
          "Fault",
          "D+67",
          "3",
          "LASER_INTERLOCK",
          "Fault",
          "Auto/Latch",
          "예",
          "Laser Interlock 활성"
        ],
        [
          "Controller",
          "Warning",
          "D+68",
          "0",
          "CPU_USAGE_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "CPU 사용률 지속 상승"
        ],
        [
          "Controller",
          "Warning",
          "D+68",
          "1",
          "CPU_TEMP_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "CPU 온도 경고"
        ],
        [
          "Controller",
          "Warning",
          "D+68",
          "2",
          "MEMORY_USAGE_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "Memory 사용률 높음"
        ],
        [
          "Controller",
          "Warning",
          "D+68",
          "3",
          "DISK_SPACE_LOW",
          "Warning",
          "Auto",
          "아니오",
          "Disk 여유공간 부족"
        ],
        [
          "Controller",
          "Warning",
          "D+68",
          "4",
          "MAIN_LOOP_SLOW",
          "Warning",
          "Auto",
          "아니오",
          "Main Loop 주기 증가"
        ],
        [
          "Controller",
          "Warning",
          "D+68",
          "5",
          "VISION_CYCLE_SLOW",
          "Warning",
          "Auto",
          "아니오",
          "Vision 처리주기 증가"
        ],
        [
          "Controller",
          "Warning",
          "D+68",
          "6",
          "LOG_WRITE_WARN",
          "Warning",
          "Auto",
          "아니오",
          "로그 저장 지연/일시 실패"
        ],
        [
          "Controller",
          "Warning",
          "D+68",
          "7",
          "QUEUE_BACKLOG",
          "Warning",
          "Auto",
          "아니오",
          "내부 Queue 적체"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "0",
          "MAIN_TASK_FAULT",
          "Fault",
          "Latch",
          "예",
          "Main Task 이상"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "1",
          "VISION_TASK_STOPPED",
          "Fault",
          "Latch",
          "예",
          "Vision Task 정지"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "2",
          "MOTION_TASK_STOPPED",
          "Fault",
          "Latch",
          "예",
          "Motion Task 정지"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "3",
          "CONFIG_LOAD_FAIL",
          "Fault",
          "Latch",
          "예",
          "설정파일 Load 실패"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "4",
          "RECIPE_DATA_ACCESS_FAIL",
          "Fault",
          "Latch",
          "예",
          "Recipe/DB 데이터 접근 실패"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "5",
          "SHARED_MEMORY_FAULT",
          "Fault",
          "Latch",
          "예",
          "Shared Memory 이상"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "6",
          "INTERNAL_IPC_FAULT",
          "Fault",
          "Latch",
          "예",
          "내부 IPC 이상"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "7",
          "CONTROLLER_INIT_FAIL",
          "Fault",
          "Latch",
          "예",
          "Controller 초기화 실패"
        ],
        [
          "Controller",
          "Fault",
          "D+69",
          "8",
          "WATCHDOG_FAULT",
          "Fault",
          "Latch",
          "예",
          "Task/System Watchdog Fault"
        ],
        [
          "PLC Communication",
          "Warning",
          "D+70",
          "0",
          "PLC_RESPONSE_SLOW",
          "Warning",
          "Auto",
          "아니오",
          "PLC 응답시간 증가"
        ],
        [
          "PLC Communication",
          "Warning",
          "D+70",
          "1",
          "PLC_COMM_RETRY_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "통신 Retry 증가"
        ],
        [
          "PLC Communication",
          "Warning",
          "D+70",
          "2",
          "PLC_TIMEOUT_TRANSIENT",
          "Warning",
          "Auto",
          "아니오",
          "간헐적 Timeout"
        ],
        [
          "PLC Communication",
          "Warning",
          "D+70",
          "3",
          "PLC_HEARTBEAT_DELAY",
          "Warning",
          "Auto",
          "아니오",
          "Heartbeat 갱신 지연"
        ],
        [
          "PLC Communication",
          "Fault",
          "D+71",
          "0",
          "PLC_DISCONNECTED",
          "Fault",
          "Auto/Latch",
          "예",
          "PLC 연결 끊김"
        ],
        [
          "PLC Communication",
          "Fault",
          "D+71",
          "1",
          "PLC_HEARTBEAT_TIMEOUT",
          "Fault",
          "Auto/Latch",
          "예",
          "Heartbeat Timeout"
        ],
        [
          "PLC Communication",
          "Fault",
          "D+71",
          "2",
          "XGT_COMM_FAIL",
          "Fault",
          "Auto/Latch",
          "예",
          "XGT 통신 지속 실패"
        ],
        [
          "PLC Communication",
          "Fault",
          "D+71",
          "3",
          "PLC_READ_FAIL",
          "Fault",
          "Auto/Latch",
          "예",
          "PLC Read 지속 실패"
        ],
        [
          "PLC Communication",
          "Fault",
          "D+71",
          "4",
          "PLC_WRITE_FAIL",
          "Fault",
          "Auto/Latch",
          "예",
          "PLC Write 지속 실패"
        ],
        [
          "PLC Communication",
          "Fault",
          "D+71",
          "5",
          "PROTOCOL_RESPONSE_ERROR",
          "Fault",
          "Auto/Latch",
          "예",
          "Protocol Response 오류"
        ],
        [
          "Recipe",
          "Warning",
          "D+72",
          "0",
          "UNUSED_POINT_EXISTS",
          "Warning",
          "Auto",
          "아니오",
          "비사용 Point 존재"
        ],
        [
          "Recipe",
          "Warning",
          "D+72",
          "1",
          "DEFAULT_VALUE_APPLIED",
          "Warning",
          "Auto",
          "아니오",
          "일부 선택항목 기본값 적용"
        ],
        [
          "Recipe",
          "Warning",
          "D+72",
          "2",
          "RECIPE_VERSION_DIFF",
          "Warning",
          "Auto",
          "아니오",
          "Recipe 버전 차이"
        ],
        [
          "Recipe",
          "Fault",
          "D+73",
          "0",
          "RECIPE_NOT_FOUND",
          "Fault",
          "Latch",
          "예",
          "Recipe 없음"
        ],
        [
          "Recipe",
          "Fault",
          "D+73",
          "1",
          "RECIPE_LOAD_FAIL",
          "Fault",
          "Latch",
          "예",
          "Recipe Load 실패"
        ],
        [
          "Recipe",
          "Fault",
          "D+73",
          "2",
          "POINT_NOT_FOUND",
          "Fault",
          "Latch",
          "예",
          "Point 없음"
        ],
        [
          "Recipe",
          "Fault",
          "D+73",
          "3",
          "POINT_DATA_INVALID",
          "Fault",
          "Latch",
          "예",
          "Point Data Invalid"
        ],
        [
          "Recipe",
          "Fault",
          "D+73",
          "4",
          "CAMERA_CONFIG_MISMATCH",
          "Fault",
          "Latch",
          "예",
          "Recipe Camera 구성 불일치"
        ],
        [
          "Recipe",
          "Fault",
          "D+73",
          "5",
          "TARGET_COORDINATE_INVALID",
          "Fault",
          "Latch",
          "예",
          "좌표 데이터 오류"
        ],
        [
          "Calibration",
          "Warning",
          "D+74",
          "0",
          "CALIBRATION_ERROR_HIGH",
          "Warning",
          "Auto",
          "아니오",
          "Calibration 오차 증가"
        ],
        [
          "Calibration",
          "Warning",
          "D+74",
          "1",
          "CALIBRATION_AGED",
          "Warning",
          "Auto",
          "아니오",
          "Calibration 유효기간/사용기간 경고"
        ],
        [
          "Calibration",
          "Warning",
          "D+74",
          "2",
          "CAL_DEFAULT_VALUE_USED",
          "Warning",
          "Auto",
          "아니오",
          "일부 Calibration 기본값 사용"
        ],
        [
          "Calibration",
          "Fault",
          "D+75",
          "0",
          "CAMERA_CAL_MISSING",
          "Fault",
          "Latch",
          "예",
          "Camera Calibration 없음"
        ],
        [
          "Calibration",
          "Fault",
          "D+75",
          "1",
          "TRACKER_CAL_MISSING",
          "Fault",
          "Latch",
          "예",
          "Tracker Calibration 없음"
        ],
        [
          "Calibration",
          "Fault",
          "D+75",
          "2",
          "PANTILT_CAL_MISSING",
          "Fault",
          "Latch",
          "예",
          "Pan/Tilt Calibration 없음"
        ],
        [
          "Calibration",
          "Fault",
          "D+75",
          "3",
          "LASER_CAL_MISSING",
          "Fault",
          "Latch",
          "예",
          "Laser Calibration 없음"
        ],
        [
          "Calibration",
          "Fault",
          "D+75",
          "4",
          "COORD_TRANSFORM_INVALID",
          "Fault",
          "Latch",
          "예",
          "Coordinate Transform Invalid"
        ],
        [
          "Calibration",
          "Fault",
          "D+75",
          "5",
          "CALIBRATION_LOAD_FAIL",
          "Fault",
          "Latch",
          "예",
          "Calibration Data Load 실패"
        ],
        [
          "Calibration",
          "Fault",
          "D+75",
          "6",
          "CALIBRATION_VERSION_MISMATCH",
          "Fault",
          "Latch",
          "예",
          "Calibration Version 불일치"
        ]
      ]
    },
    {
      "key": "04_Code_Definitions",
      "label": "4. Code Definitions",
      "title": "Command / State / Result / Error Code 정의",
      "subtitle": "PLC와 Controller 양쪽 구현에서 동일한 상수를 사용하기 위한 코드 정의 시트",
      "type": "sections",
      "base": null,
      "sections": [
        {
          "title": "COMMAND_CODE",
          "headers": [
            "값",
            "이름",
            "설명"
          ],
          "rows": [
            [
              0,
              "NONE",
              "명령 없음"
            ],
            [
              1,
              "TARGET_APPLY_MOVE",
              "REQ_RECIPE_ID/REQ_POINT_ID 적용 후 Pan/Tilt 목표 이동"
            ],
            [
              2,
              "HOME",
              "Pan/Tilt 원점복귀"
            ],
            [
              3,
              "ERROR_RESET",
              "해제 가능한 Latch Fault Reset"
            ],
            [
              4,
              "CONTROLLER_RESTART",
              "비운전중 Controller 재시작"
            ],
            [
              5,
              "RELOAD_CONFIG",
              "설정/Recipe 재로드"
            ],
            [
              "6~65535",
              "Reserved",
              "향후 확장"
            ]
          ]
        },
        {
          "title": "CONTROLLER_STATE",
          "headers": [
            "값",
            "상태",
            "설명"
          ],
          "rows": [
            [
              0,
              "INIT",
              "초기화"
            ],
            [
              1,
              "STANDBY",
              "운전 대기"
            ],
            [
              2,
              "READY",
              "운전 준비완료"
            ],
            [
              3,
              "HOMING",
              "원점복귀 중"
            ],
            [
              4,
              "TARGET_MOVING",
              "목표 위치 이동 중"
            ],
            [
              5,
              "TRACKING",
              "Tracking 운전 중"
            ],
            [
              6,
              "STOPPED",
              "정지 상태"
            ],
            [
              7,
              "FAULT",
              "Fault 상태"
            ],
            [
              8,
              "TEACH/CALIBRATION",
              "관리자/Calibration 상태"
            ]
          ]
        },
        {
          "title": "TRACKING_STATE",
          "headers": [
            "값",
            "상태",
            "설명"
          ],
          "rows": [
            [
              0,
              "OFF",
              "Tracking 비활성"
            ],
            [
              1,
              "INITIALIZING",
              "Tracking 초기화"
            ],
            [
              2,
              "SEARCHING",
              "Marker/Tracker 탐색"
            ],
            [
              3,
              "TRACKING",
              "정상 Tracking"
            ],
            [
              4,
              "DEGRADED",
              "성능 저하"
            ],
            [
              5,
              "LOST",
              "Tracker 유실"
            ],
            [
              6,
              "FAULT",
              "Tracking 처리 Fault"
            ]
          ]
        },
        {
          "title": "COMMAND_STATUS",
          "headers": [
            "값",
            "상태",
            "설명"
          ],
          "rows": [
            [
              0,
              "IDLE",
              "대기"
            ],
            [
              1,
              "RECEIVED",
              "명령 수신"
            ],
            [
              2,
              "BUSY",
              "처리 중"
            ],
            [
              3,
              "COMPLETE",
              "정상 완료"
            ],
            [
              4,
              "REJECTED",
              "조건 불충족으로 명령 거부"
            ],
            [
              5,
              "ERROR",
              "처리 중 오류"
            ]
          ]
        },
        {
          "title": "COMMAND_RESULT",
          "headers": [
            "값",
            "이름",
            "설명"
          ],
          "rows": [
            [
              0,
              "OK",
              "정상"
            ],
            [
              1,
              "INVALID_COMMAND",
              "잘못된 Command Code"
            ],
            [
              2,
              "RECIPE_NOT_FOUND",
              "Recipe 없음"
            ],
            [
              3,
              "POINT_NOT_FOUND",
              "Point 없음"
            ],
            [
              4,
              "SYSTEM_NOT_READY",
              "System Ready 아님"
            ],
            [
              5,
              "HOMING_REQUIRED",
              "Homing 필요"
            ],
            [
              6,
              "FAULT_ACTIVE",
              "Fault 상태"
            ],
            [
              7,
              "FORCE_STOP_ACTIVE",
              "강제중단 상태"
            ],
            [
              8,
              "TRACKING_UNAVAILABLE",
              "Tracking 사용 불가"
            ],
            [
              9,
              "MOTION_TIMEOUT",
              "Pan/Tilt 이동 Timeout"
            ],
            [
              10,
              "CONFIG_OR_CAL_ERROR",
              "설정/Calibration 오류"
            ],
            [
              11,
              "COMMAND_BUSY",
              "다른 명령 처리 중"
            ],
            [
              12,
              "INTERNAL_ERROR",
              "Controller 내부 오류"
            ]
          ]
        },
        {
          "title": "DEVICE_FAULT/WARNING_SUMMARY Bit",
          "headers": [
            "Bit",
            "장치",
            "설명"
          ],
          "rows": [
            [
              "0",
              "Controller",
              "Controller 자체"
            ],
            [
              "1",
              "PLC Communication",
              "PLC/XGT 통신"
            ],
            [
              "2",
              "Vision Common",
              "Vision 공통 (Camera 1)"
            ],
            [
              "3",
              "Camera 1",
              "Camera 1"
            ],
            [
              "4",
              "Reserved",
              "Camera 2~4 미사용"
            ],
            [
              "5",
              "Reserved",
              "Camera 2~4 미사용"
            ],
            [
              "6",
              "Reserved",
              "Camera 2~4 미사용"
            ],
            [
              "7",
              "Tracker",
              "Nutrunner Tracker"
            ],
            [
              "8",
              "Pan Motor",
              "Pan 축"
            ],
            [
              "9",
              "Tilt Motor",
              "Tilt 축"
            ],
            [
              "A",
              "Laser",
              "Laser"
            ],
            [
              "B",
              "Recipe",
              "Recipe/Point Data"
            ],
            [
              "C",
              "Calibration",
              "Calibration"
            ],
            [
              "D~F",
              "Reserved",
              "향후 확장"
            ]
          ]
        },
        {
          "title": "대표 Fault Code 범위",
          "headers": [
            "범위",
            "장치/범주",
            "예시"
          ],
          "rows": [
            [
              "1000~1099",
              "Controller",
              "1001 Main Task Fault"
            ],
            [
              "1100~1199",
              "PLC Communication",
              "1101 PLC Disconnected"
            ],
            [
              "2000~2099",
              "Vision Common",
              "2001 Camera 1 Unavailable"
            ],
            [
              "2100~2199",
              "Camera 1",
              "2101 Camera 1 Disconnected"
            ],
            [
              "2200~2299",
              "Reserved",
              "미사용"
            ],
            [
              "2300~2399",
              "Reserved",
              "미사용"
            ],
            [
              "2400~2499",
              "Reserved",
              "미사용"
            ],
            [
              "3000~3099",
              "Tracker",
              "3001 Tracker Lost"
            ],
            [
              "4000~4099",
              "Pan",
              "4001 Pan Communication Fault"
            ],
            [
              "4100~4199",
              "Tilt",
              "4101 Tilt Communication Fault"
            ],
            [
              "5000~5099",
              "Laser",
              "5001 Laser Communication Fault"
            ],
            [
              "6000~6099",
              "Recipe",
              "6001 Recipe Not Found"
            ],
            [
              "6100~6199",
              "Calibration",
              "6101 Camera Calibration Missing"
            ]
          ]
        }
      ]
    }
  ]
};
