# Vision 다른 체결점 지향 신호 구현 지침

작성일: 2026-10-19. Core의 수신·상태·레이저 표시와 통신 문서만 변경했다. **Vision 코드는 변경하지 않았으며 아래 내용은 후속 구현 지침이다.**

## 1. 신호 의미

`position_error_mm`가 크다는 사실은 지정점에서 벗어났다는 뜻이다. 다른 체결점을 향한다는 확정 신호가 아니다. Core는 이 오차만으로 `wrong_point`를 만들지 않는다.

| 판정 | `wrong_point` | 실제 매칭 ID |
|---|---|---|
| 현재 지정점에 유효하게 매칭 | `false` | 지정 레시피/포인트와 동일 |
| 다른 등록 체결점에 유효하게 매칭 | `true` | 지정 레시피/포인트와 다름 |
| 미매칭, 후보 중복, 검출/보정/프레임 무효, 계산 중 | `null` | 확인되지 않은 값은 `null` |

단순히 가장 가까운 점이라는 이유로 매칭을 확정하지 않는다. 후보까지의 거리·공구 지향/형상·보정 품질·신선도와 후보 간 모호성을 검증한다. 어떤 점에도 충분히 가깝지 않으면 `null`이다.

## 2. TCP 추가 계약

기존 UTF-8 JSON Lines 봉투와 `vision.status` 요청 `params:{}`를 유지한다. 응답 `result`에 선택 필드 `point_match`를 추가한다.

```json
{"id":"status-1","ok":true,"result":{"online":true,"tracking_active":true,"tracker_present":true,"tracker_valid":false,"result_ready":true,"position_error_mm":null,"camera_required_mask":1,"camera_online_mask":1,"camera_valid_mask":1,"point_match":{"recipe_id":3,"point_id":15,"command_seq":100,"matched_recipe_id":3,"matched_point_id":20,"wrong_point":true,"age_seconds":0.04}}}
```

현재 지정점 `3/15/100`에서 다른 점 `3/20`을 확인한 예이다. `tracker_valid=false`를 유지하므로 이 진단 신호가 생산 위치 OK를 허용하지 않는다.

| `point_match` 필드 | 형식 | 의미 |
|---|---|---|
| `recipe_id` | 정수 1~65535 또는 `null` | 판정에 사용한 Core 지정 레시피 |
| `point_id` | 정수 1~65535 또는 `null` | 판정에 사용한 Core 지정 포인트 |
| `command_seq` | 정수 0~65535 또는 `null` | 해당 `vision.target.set`의 SEQ |
| `matched_recipe_id` | 정수 1~65535 또는 `null` | 실제 매칭점의 Core 레시피 ID |
| `matched_point_id` | 정수 1~65535 또는 `null` | 실제 매칭점의 Core 포인트 ID |
| `wrong_point` | boolean 또는 `null` | 다른 점 / 현재 점 / 판단 불가 |
| `age_seconds` | 유한한 0 이상 숫자 또는 `null` | 입력 프레임과 매칭 결과의 현재 경과 시간 중 큰 값 |

`true/false`에는 모든 ID·SEQ와 유효 시간이 필요하다. 숫자 문자열, boolean ID, NaN, Infinity, 음수 시간은 금지한다. `true`인데 실제 ID가 지정점과 같거나 `false`인데 다르면 Core는 판단 불가로 처리한다. 미확정 예:

```json
{"recipe_id":3,"point_id":15,"command_seq":100,"matched_recipe_id":null,"matched_point_id":null,"wrong_point":null,"age_seconds":null}
```

필드가 없는 구버전은 기존 위치 판정으로 호환한다. 새 구현은 판정 불가일 때 필드를 생략하지 말고 위 객체로 보고한다. `point_match:null`이나 잘못된 객체도 판단 불가이며 위치 OK에 사용할 수 없다.

## 3. Vision 수정 위치

아래 경로는 Vision 저장소 기준이다.

1. `web/backend/services/nutrunner_axis_target_test.py`: `nearest_target_id`, `target_results`, `inside_target_ids`, `passed_target_ids`를 만드는 기존 진단 계산을 확인한다. DEV에서 선택한 제한된 후보 집합의 가장 가까운 점을 전체 작업점 판정으로 복사하지 않는다. 실제 운전 제품·그룹의 등록 후보 범위와 유효/유일 매칭 기준을 확보한다.
2. `web/backend/services/run_point_service.py::RunPointService.observe()`: 현재는 지정점까지의 거리로 `MOVING/VERIFYING/ARRIVED`를 정한다. 동일 입력 프레임·제품·공구·보정 revision으로 실제 매칭점을 판정하고, 현재 지정 ID/SEQ와 함께 저장한다. 지정점에서 멀다는 이유만으로 다른 점을 확정하지 않는다.
3. 같은 서비스의 `snapshot()`: 마지막 매칭 표본의 현재 나이와 함께 `point_match`를 반환한다. 상태 조회나 동일 튜플의 `vision.target.set` 재전송으로 관측 시각을 갱신하지 않는다. 상태 조회 스레드에서 새 프레임이나 분석 완료를 기다리지 않는다.
4. `web/backend/services/nutrunner_live_tracking_service.py::core_snapshot()`: 진단 결과를 운전 서비스에 전달할 때 입력 `frame_id`와 제품/보정/공구 컨텍스트가 같은지 검증한다. 지정점 위치 측정과 다른 프레임의 후보 진단을 섞지 않는다.
5. `web/backend/services/core_vision_bridge.py::CoreVisionBridge.handle()`: `vision.status`의 기존 `run_position`을 유지하면서 별도로 `result.point_match`를 추가한다. `tracker_valid`, `production_approved`와 기존 카메라/결과 신선도 조건은 완화하지 않는다.

Core 숫자 ID와 CAD ID는 별개다. `G3-020`을 잘라 숫자 20으로 만들지 말고 `core_integration/run_signal.json`의 명시적 레시피/포인트 ↔ CAD 연결로 역매핑한다. 동일 CAD 점이 여러 등록점에 대응해 현재 작업에서 유일하지 않거나 다른 그룹의 보정이 검증되지 않았다면 `null`이다.

추적 정지·바이패스·카메라 유실·프레임 만료·새 지정점·제품/보정/공구 변경 시 이전 매칭을 무효화한다. 재연결 후 현재 지정점에 대한 새 프레임을 확보한다. 새 PLC 주소나 새 TCP 명령은 필요하지 않다.

## 4. Core 소비 동작

- `VisionSnapshot.point_match`로 정규화한다. Core `get_status`와 HTTP `/api/status`의 `vision.point_match`는 수신 내용이고, `vision.wrong_point`는 현재 적용 가능한 판정(`true/false/null`)이다.
- 현재 적용 레시피/포인트/이동 SEQ와 정확히 일치해야 한다. 추적 요청·실행, 대상 존재, 필수 카메라 online/valid, Vision 정상 통신과 이동/체결 결과 처리 중이 아닌 상태를 확인한다.
- `age_seconds`에 Core의 마지막 정상 수신 후 경과 시간을 더해 `runtime.vision_status_max_age_ms` 이하인지 검사한다. 다른 측정의 `result_age_seconds`로 대신하지 않는다.
- 다른 점 확정은 기존 PTM `laser.state(state="wrong_point")` 표시로 전달한다. 레이저 프로필·수동 우선권·FORCE_STOP·RUN_ENABLE·안전 인터록은 유지한다. 새 PLC 알람 비트나 고장 래치를 만들지 않는다.
- 새 신호가 제공되면 다른 점 또는 판단 불가 상태에서 기존 `POSITION_OK` 및 이에 의존하는 `LASER_TARGET_READY`를 허용하지 않는다. 현재 점 `false`도 기존 `tracker_valid`·위치 오차·안정 시간 조건을 만족해야 위치 OK다. Vision 바이패스 정책은 유지한다.
- 현재 점으로 복귀하면 표시를 자동 복구한다. 이 신호로 `tracker_valid`나 생산 승인을 참으로 만들지 않는다.

## 5. 후속 검증

1. 지정점 A에서 A를 향하면 같은 실제 ID와 `false`, 다른 등록점 B를 향하면 B의 숫자 ID와 `true`인지 확인한다.
2. 단순 오차 초과·어떤 점에도 맞지 않음·후보 모호함은 `null`인지 확인한다. 진단 매칭만으로 생산 승인이 생기면 안 된다.
3. `true`인데 위치 오차가 작게 들어와도 Core 위치 OK가 해제되는지 확인한다.
4. 카메라 유실·추적 정지·바이패스·만료·보정 변경 시 과거 `true/false`를 쓰지 않는지, 다른 레시피나 오래된 SEQ도 버리는지 확인한다.
5. A→B→A 변경, SEQ 0, 동일 튜플 재전송, TCP 재접속을 검사한다. 재전송으로 표본 나이가 초기화되면 안 된다.
6. 목표 복귀 시 자동 복구와 PTM `wrong_point` 프로필의 켜짐·점멸·꺼짐 동작을 실장비에서 확인한다.

Core 로컬 TCP/단위 테스트는 Vision 판정 구현, 실제 카메라 정확도, RDK 배포와 PLC·레이저 현장 검증을 대신하지 않는다. **이번 Core 변경만 배포하면 기존 Vision은 신호를 보내지 않으므로 `vision.wrong_point`는 `null`이다.**
