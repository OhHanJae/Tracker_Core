# Core–Vision 통신 계약 교차 검증

검증일: 2026-10-13. 실제 PLC·카메라 없이 소스와 루프백 TCP로 검증했다.

## 1. 검증 기준과 결론

| 대상 | 고정한 버전 |
| --- | --- |
| Core 도입 | [`2dab84c958b427f80cb7577269271f40abe13ab4`](https://github.com/OhHanJae/Tracker_Core/commit/2dab84c958b427f80cb7577269271f40abe13ab4), 검증 시작 시 `master` HEAD |
| Core 최종 기준 | [`3cf21e512b70965260fa75f71bc8a439f0063feb`](https://github.com/OhHanJae/Tracker_Core/commit/3cf21e512b70965260fa75f71bc8a439f0063feb), 검증 중 갱신된 `master`; 최종 수정·테스트는 이 커밋 위에서 실행 |
| Core 이전 동작 | 부모 커밋 `b49f1590f8b26998f530650ca637563f3f28e6b9`의 명령 수신·분기와 기본 설정 |
| Vision 도입 | [PR #20](https://github.com/meaningless107418/Fastening_Vision_Platform/pull/20), 2026-10-12 17:49:57 KST 병합, merge `0d849bb4c71be97c13d029f75e69f60e1757fee5` |
| Vision 현재 | `6c8562605b4f15776a4d045840a5c475012dc0c6`, 검증 시작 시 `main` HEAD |

Vision main에는 PR #20 이후 변경이 있으나 `core_vision_bridge.py`,
`vision_recipe_store.py`, `vision_result_contract.py`는 변경되지 않았다.
GitHub compare와 파일 blob SHA를 확인하고 해당 버전의 실제 세 모듈을
루프백 통합 테스트에 사용했다. 원본 Vision 코드는 이 저장소에 복사하지 않았다.

결론은 다음과 같다.

1. Core의 실제 요청은 `recipe_id`, `point_id`, `command_seq`를 포함해 Vision과 일치한다.
2. 코드 5의 별칭은 현재 기본 실행 경로에서 재로드와 데이터 요청을 동시에 실행하지 않는다. 재로드라는 이름과 문서가 잘못 남아 있다.
3. 카메라 오프라인 상태가 TCP 조회를 사전에 막는 문제와 응답 잡음이 타임아웃을 연장하는 문제를 재현하고 수정했다.
4. Vision은 ID 검증과 미지원 준비 응답을 제공한다. 실제 체결 각도·기울기·XYZ·측정 시각 생성은 구현하지 않았다.

## 2. 명령 코드 5와 기존 호환성

수정 전 `CommandCode.NUTRUNNER_DATA_REQUEST is CommandCode.RELOAD_CONFIG`이며
`CommandCode(5).name`은 먼저 정의한 `NUTRUNNER_DATA_REQUEST`다. 이름으로 구분한
조건문이나 딕셔너리 키를 추가하면 같은 값으로 비교되어 분기 도달 불가 또는
키 덮어쓰기가 생길 수 있다. 이는 확장 시 위험이며, 현재 두 작업이 실행된다는
증거는 아니다.

현재 `_handle_plc_command()`는 1, 2, 3, 5만 수락하고 5를
`_prepare_nutrunner_request()`로 보낸다. `_dispatch_plc_command()`에도
PLC 재로드 분기는 없다. 부모 커밋은 1, 2, 3만 수락해 당시의 PLC 5도
`REJECTED / INVALID_COMMAND`였다. 문자열 JSON 명령 `reload_config`는
`handle_tcp_command()`의 별도 경로이며 이번 변경에서도 유지된다.

| 코드/인터페이스 | 검증한 동작 | 이번 수정 |
| --- | --- | --- |
| PLC 0 | 새 명령 없음 | 유지 |
| PLC 1 | `point.goto`, `motion_id` 수락 후 최신 PTM 완료 상태 확인 | 유지 |
| PLC 2 | `home.start`, 최신 완료 상태와 `homed` 확인 | 유지 |
| PLC 3 | 유지 고장 리셋, RUN 해제/강제정지에서도 수락 | 유지 |
| PLC 4 | `REJECTED / INVALID_COMMAND`; 프로세스 재시작은 Process Manager 사용 | 유지 |
| PLC 5 | `vision.result` 데이터 조회 | 이름을 이 기능 하나로 확정 |
| PLC 6~65535 | 예약, `REJECTED / INVALID_COMMAND` | 재로드용 새 번호를 만들지 않음 |
| JSON `reload_config` | 파일을 다시 읽어 설정 교체 | 유지 |
| Vision `vision.status`, `tracking.start`, `tracking.stop` | 기존 요청·응답 유지 | 실제 브리지로 회귀 검증 |

수정은 사용되지 않는 `RELOAD_CONFIG` enum 멤버를 제거하고 `CommandCode`에
`@unique`를 적용한다. 다른 enum의 의도된 장치 별칭에는 적용하지 않는다.
PLC 숫자 0~5와 결과 코드 값은 바꾸지 않는다. 외부 Python 코드가
`CommandCode.RELOAD_CONFIG`를 직접 import했다면 그 소스는 변경해야 한다.
설정 재로드 요청은 기존 JSON `reload_config`를 사용한다.

구버전 설정에 명령 맵 5가 없으면 기본값 병합으로 데이터 요청 항목이 추가된다.
사용자 지정 맵 5가 `core/reload_config` 등으로 남아 있으면 자동으로
재해석하거나 재로드하지 않고 `INVALID_COMMAND`로 거부한다.

## 3. 전송 계약과 수신 데이터 검증

HTTP는 기본 8767, Core TCP는 기본 8768이다. Vision은 연결마다
UTF-8 JSON 객체 한 줄 + LF를 받고 응답 한 줄 후 연결을 닫는다.
수신 요청 한도는 16 KiB, 요청 읽기 제한은 0.8초다.

```json
{"id":"core-request-id","command":"vision.result","params":{"recipe_id":3,"point_id":25,"command_seq":105}}
```

위 JSON 뒤에 LF가 필요하다. `recipe_id`, `point_id`는 정수 1~65535,
`command_seq`는 정수 0~65535이며 `bool`은 정수로 인정하지 않는다.
Vision은 세 필드가 정확히 존재해야 하며 추가 필드도 `INVALID_PARAMS`로 거부한다.
Core 설정 맵의 두 ID placeholder와 달리 실제 TCP 클라이언트는 seq도 전송한다.

응답 최상위 `id`는 요청과 같아야 한다. Core는 `ok=true` 응답의 결과에서
세 식별자 일치, `supported`/`data_valid`의 엄격한 bool 타입을 확인한다.
데이터가 유효하다면 두 각도와 XYZ가 유한한 숫자이고 bool이 아니어야 하며,
`timestamp_ms`는 양의 정수여야 한다. `coordinate_frame`은 비어 있지 않은
문자열 또는 null을 허용한다. 현재 스키마는 시각의 freshness나 좌표계의 의미를
추가 검증하지 않으므로 미래 측정 구현 전에 시계 기준·허용 지연·좌표계를 확정해야 한다.

`data_valid=false` 결과는 숫자와 시각이 섞여 들어와도 Core가 모두 null로
정규화한다. `supported=false`와 `data_valid=true`의 모순은 오류다.
측정값이 없는 상태를 0도/0 mm 측정으로 만들지 않는다.

## 4. Core 처리 흐름과 PLC 결과

새 코드 5/seq를 수신하면 이전 `nutrunner_result`를 지우고
`RECEIVED`로 전환한다. 같은 seq는 다시 실행하지 않으며 65535→0 순환은 허용한다.
RUN 해제·강제정지는 사전에 거부한다. 유효한 ID, 명령 맵, Vision 활성화를
확인한 뒤 다음 주기에 `BUSY`로 바꾸고 비동기 요청을 실행한다.
PTM의 unrelated fault는 읽기 전용 데이터 조회 자체를 막지 않는다.

| 입력/응답 | 최종 COMMAND_STATUS | COMMAND_RESULT | 결과 보관 |
| --- | --- | --- | --- |
| `ok=true`, `supported=true`, `data_valid=true`, 식별자·측정 타입 정상 | COMPLETE (3) | OK (0) | 정규화한 측정 결과 |
| 현재 Vision: `ok=true`, `supported=false`, `data_valid=false` | REJECTED (4) | TRACKING_UNAVAILABLE (8) | 미지원 플래그와 null 측정값 |
| `supported=true`, `data_valid=false` | REJECTED (4) | TRACKING_UNAVAILABLE (8) | 무효 플래그와 null 측정값 |
| Vision `RECIPE_NOT_FOUND` | REJECTED (4) | RECIPE_NOT_FOUND (2) | null |
| Vision `POINT_NOT_FOUND` | REJECTED (4) | POINT_NOT_FOUND (3) | null |
| ID 0/범위 초과 | REJECTED (4) | 해당 ID 오류 (2 또는 3) | null, TCP 전송 없음 |
| 응답 ID 쌍/seq 불일치, 잘못된 측정값 | ERROR (5) | TRACKING_UNAVAILABLE (8) | null |
| `INVALID_PARAMS`, `UNKNOWN_COMMAND`, `TRACKING_UNAVAILABLE` 등 응답 오류 | ERROR (5) | TRACKING_UNAVAILABLE (8) | null |
| 연결 실패·응답 타임아웃·조기 연결 종료 | ERROR (5) | TRACKING_UNAVAILABLE (8) | null |
| Vision 비활성 | REJECTED (4) | TRACKING_UNAVAILABLE (8) | null, TCP 전송 없음 |
| Vision 바이패스 | COMPLETE (3) | OK (0) | null, 데이터 요청 없음; 바이패스 출력 bit 확인 필요 |

최종 상태에서는 COMMAND_BUSY가 내려간다. PLC 출력 상대 워드 12/13은
마지막 코드/seq, 14/15는 상태/결과다. 예를 들어 출력 시작 D1100이면
D1112/D1113/D1114/D1115에 해당한다. 실제 주소는 배포 설정을 확인해야 한다.

각도·기울기·XYZ·시각은 현재 PLC 출력 워드에 매핑되지 않는다.
Core 상태 JSON의 `vision.nutrunner_result`에만 보관한다. 데이터 조회 COMPLETE가
현재 레시피/포인트를 적용하거나 `tracker_valid`, 위치 OK, 안정화, 레이저 준비,
체결 허가를 만드는 것은 아니다. 바이패스의 COMPLETE/OK도 측정 성공이 아니다.

최종 기준 Core는 Vision 바이패스에서 CAMERA_READY를 켜고, RUN/추적 허가 및
무고장 조건이면 위치 OK·안정화·추적 활성 등 공정 플래그도 켤 수 있다.
이는 `3cf21e5`에 들어온 별도 바이패스 정책이며 이번 수정에서 유지했다.
따라서 공정 플래그만으로 측정 유효를 판단하지 말고 출력 워드 40 bit 1의
Vision 바이패스와 실제 결과의 supported/data_valid를 구분해야 한다.

진행 중 RUN 해제·강제정지·설정 변경·바이패스 진입 시 기존 취소/초기화 경로가
적용된다. 늦은 응답은 마지막 seq와 BUSY 상태를 재확인해 이미 끝난 명령을
덮어쓰지 않는다. 결과 요청 실패 자체가 PTM 통신 고장으로 기록되지는 않는다.

## 5. 재현한 문제와 수정

### 카메라 오프라인과 TCP 가용성

Vision `vision.status.result.online=false`는 카메라/상태 의미다.
Process Manager는 정상 TCP 응답이면 Communication online=true로 평가한다.
수정 전 Core 데이터 요청은 DeviceService의 카메라 online=false만으로
거부해 카메라와 무관한 레시피 검증 및 미지원 응답도 확인할 수 없었다.

수정 후 조회는 서비스 활성 여부를 검사하고 실제 TCP 결과로 판정한다.
오프라인 카메라를 online으로 바꾸지 않으며 모터·레이저·생산 허가 조건도
바꾸지 않는다. 실제 Vision 브리지에 오프라인 카메라 test double을 연결해
status 성공, camera offline, 준비 응답 수신을 함께 검증했다.

### 전체 요청 타임아웃

수정 전 connect/drain/readline마다 `timeout_s`가 새로 적용됐다.
특히 다른 최상위 ID나 잘못된 JSON이 시간 제한보다 빠르게 계속 도착하면
정상 응답 없이 BUSY가 지속됐다. 0.12초 설정에서 0.01초 간격의 다른 ID
스트림으로 재현했고 0.6초 테스트 watchdog이 요청을 중단해야 했다.

수정 후 단조시계 deadline 하나를 연결·송신·모든 응답 읽기·정리에 공유한다.
무관한 ID, 배너, JSON 비객체, 잘못된 JSON은 남은 시간 안에서만 무시한다.
타임아웃은 `JsonTcpError(code="TIMEOUT")`, 연결 실패는 `CONNECT_FAILED`로
보고하며 Core의 PLC 결과는 기존 TRACKING_UNAVAILABLE (8)을 유지한다.
동일 ID 응답의 `ok` bool과 LF 프레이밍도 확인한다.

이 전송 함수는 PTM/XGT 조회에서도 사용하므로 timeout_s는 이제 단계별 제한이
아닌 전체 요청 제한이다. 배포 시 이 의미를 기준으로 타임아웃을 설정한다.

## 6. 현재 지원과 미구현 경계

| 기능 | 현재 상태 |
| --- | --- |
| JSON Lines status, start/stop, 요청 ID 상관관계 | 지원 |
| Vision 소유 레시피·포인트 등록/조회/변경/삭제 및 영구 저장 | 지원 |
| `vision.result` ID/seq 검증과 준비 응답 | 지원 |
| 없는 레시피·포인트, 손상 저장소의 오류 응답 | 지원 |
| Core 명령 5 수신, 결과 정규화, 상태/결과 코드 전송 | 지원 |
| 체결 각도·기울기·실제 XYZ·측정 timestamp 생성 | 미구현; null 유지 |
| Vision의 supported/data_valid를 true로 만드는 실제 측정 경로 | 미구현 |
| 결과 측정값을 PLC 워드로 전달 | 미구현 |
| 레시피/포인트 ID를 실제 측정 대상·제품·보정 버전에 바인딩하고 생산 승인 | 미구현 |
| 타임스탬프 freshness, 기준 좌표계, 제품/캘리브레이션 일치 검증 | 향후 측정 계약 필요 |

자동화 테스트의 정상 측정 응답은 합성 데이터이며 실제 Vision 측정 구현의
증거가 아니다. 현재 Vision의 정상 동작은 미지원 준비 응답이다.

## 7. 자동화 검증과 실행법

Core는 표준 라이브러리만 사용한다. 일반 회귀 테스트는 다음과 같다.

```bash
# Tracker_Core 저장소 루트에서 실행한다.
python -m unittest discover -s tests -v
```

별도 Vision 소스가 없으면 실제 브리지 통합 테스트 5개는 명시적으로 skip한다.
루프백 responder를 쓰는 일반 테스트는 독립 실행한다. GitHub Actions는 Python
3.10/3.12로 이 일반 테스트와 compileall을 실행하도록 추가했다.

```bash
# 실제 Fastening_Vision_Platform checkout의 절대경로를 지정한다.
FVP_REPOSITORY=/path/to/Fastening_Vision_Platform python -m unittest discover -s tests -v

# Vision 저장소 루트에서 기존 관련 계약 테스트도 실행한다.
python -m unittest discover -s tests -p 'test_*vision*.py' -v
```

Windows PowerShell에서는 `$env:FVP_REPOSITORY='C:\\path\\Fastening_Vision_Platform'`
설정 후 동일한 Core unittest 명령을 실행한다.

실행 결과: Python 3.12에서 Core 테스트 37개(실제 브리지 5개 포함)와
기존 Vision 계약 테스트 14개 통과. 별칭·오프라인 사전 거부·무한 잡음 읽기의
회귀 테스트는 수정 전 실패, 수정 후 통과했다. Python 3.10 실행은 GitHub
Actions 결과로 확인해야 하며 로컬에서 3.10을 실행했다고 주장하지 않는다.

PLC 출력 단어 검증은 메모리 직렬화까지이며 XGT 전송 ACK, 실장비 시운전,
카메라 정확도·지연·측정 성능 및 운영 배포는 이번 검증에 포함되지 않는다.
