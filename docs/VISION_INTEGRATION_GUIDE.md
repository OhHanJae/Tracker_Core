# Vision 연동 점검 및 수정 지침

> 현재 알람 통신 형식은 **v5.2 비트맵**입니다. 최근 통신 진단·자동 복구는 유지하며, 이 문서의 v6/v7 알람 BYTE/WORD 설명은 이전 규격입니다. 현재 주소·코드는 [v5.2 복원 규격](PLC_ALARM_CODES_V5_2.md)을 따릅니다.

현재 규격은 v7이다. D+21 코어, D+22 PTM, D+23 비전, D+24 PLC가 각각 1 WORD이며 상위 BYTE=오류, 하위 BYTE=경고다. 모듈 안에서 오류·경고를 각각 우선순위 1건씩 표시한다. D+25는 알람 순번, D+27/28은 전체 원인 개수, D+20/26/50~79는 Reserved(0)다. 전체 규격은 [PLC 모듈별 알람 v7](PLC_ALARM_CODES_V7.md)을 따른다.

점검일: 2026-10-09. Core 연동 추가: 2026-10-10. 알람·바이패스 계약 보완: 2026-10-14. 체결 완료 명령 6 추가: 2026-10-16. 대상: Windows 11 x64 / RDK X5 3.5 이미지.

사용자 요청에 따라 Vision 저장소의 코드·설정·런처는 변경하지 않았다. 아래 항목은 실제 소스에서 확인한 제약과 수정 방향이다. 카메라, RDK, PLC 실장비 성능 검증은 이 Windows 작업 공간에서 수행하지 않았다.

## 1. 현재 동작 범위

2026-10-19 추가: 다른 체결점 지향 신호는 [`point_match` 통신 계약 및 Vision 수정 지침](VISION_WRONG_POINT_SIGNAL_GUIDE.md)을 따른다. Core 수신·표시·위치 OK 차단은 반영했으며 Vision 코드는 이번 작업에서 변경하지 않았다. 기존 위치 오차만으로 다른 체결점을 확정하지 않는다.

Core와 Vision의 통신 계약은 서로 맞는다. Core는 `src/vision/vision_client.py`에서 `vision.status`를 호출하고, Vision은 `web/backend/services/core_vision_bridge.py`에서 처리한다.

- 웹: HTTP 8767. Core 운전 통신: TCP 8768.
- 프레임: UTF-8 JSON 한 줄 + LF. 길이 접두어 방식이 아니다.
- 요청: `{"id":"core-request-id","command":"vision.status","params":{}}`.
- 응답: 같은 `id`, `ok`, `result` 또는 `error`.
- Vision 지원 운전 명령: `vision.status`, `tracking.start`, `tracking.stop`, `vision.result`.
- 현재 Vision의 `vision.result`는 레시피/포인트 조회와 미지원 응답(`supported=false`, `data_valid=false`)까지만 구현되어 있다. 실제 체결 좌표는 만들지 않는다. Core의 PLC 명령 5는 이 결과 조회를 사용한다.
- 현재 단일 카메라 계약이다. Core도 첫 카메라와 마스크 bit 0만 처리한다. 복수 카메라를 추가하려면 양쪽 명세와 PLC 맵을 함께 검토해야 한다.
- Core와 현재 Vision 브리지는 `cameras[0].ip`(또는 `camera_ip`), `tracking_bypass`, `tracking_bypass_reason`을 지원한다. 대상 검출과 결과 생성 상태를 구분하는 추가 계약은 6절을 따른다.

TCP 요청 성공, 카메라 연결, 프레임 유효, 검사 결과 유효는 별개다. 웹이 열리거나 `ok=true`라는 이유만으로 운전 준비 완료로 표시하면 안 된다.

### 1.1 카메라 IP 및 추적 바이패스 계약

Vision 코드는 이번에도 수정하지 않았다. 현재 Vision 웹과 TCP 브리지의 카메라 IP·추적 바이패스 응답을 사용한다. Core 공정 바이패스 계약은 [Vision 너트러너 데이터 연동 구현 지침서](../../1.%20계획,설계/Vision_너트러너_데이터_연동_구현_지침서.md)의 6절이며, 이번 알람 보완은 아래 6절이다.

카메라 IP는 Vision 서버의 IP와 다르다. Vision 웹에서 실제 카메라 연결 설정을 적용하고, 실제 적용 주소를 `vision.status.cameras[0].ip`에 반환한다. Core는 이를 `camera_ip`로 노출한다. IP가 없는 카메라는 `null`을 반환한다. 재연결·설정 변경 시 이전 검사 결과는 무효화한다.

추적 바이패스는 `tracking_bypass: true/false`, 사유는 `tracking_bypass_reason: string`으로 반환한다. Core는 이 값을 PLC 요청·Core 웹 설정과 함께 Vision 실효 바이패스에 반영한다. 바이패스 중 추적 시작을 요청하지 않고 실행 중인 추적에 stop을 요청하며, Vision·Camera·Tracker 알람을 PLC 공정 출력에서 제외한다. Core는 공정의 Vision 준비·위치 검사를 기존 바이패스 규칙으로 통과시키지만, 실제 `tracker_valid`, 카메라 상태와 측정값은 유효하게 위장하지 않는다. 바이패스 해제 감지를 위해 TCP 상태 폴링은 계속한다. 해제 시 PLC 추적 요청·고장/정지 조건에 따라 다시 시작한다. 생산 승인, PLC 통신, Controller 고장 및 `FORCE_STOP` 조건은 별도로 유지한다. `RUN_ENABLE`은 Controller→PLC 쓰기만 허용하며 추적을 제어하지 않는다. 오래된 Vision은 필드를 생략해도 `false`/빈 사유/`null` 기본값으로 호환된다.

Process Manager의 서버 IP/TCP 8768/실제 웹 포트를 저장하고 Vision 통신 및 프로세스를 활성화한다. `get_devices`의 Vision `status`에서 값이 보이는지 확인한 뒤 웹에서 바이패스 적용·해제와 실제 추적 정지·복귀를 검증한다.

## 2. 우선 수정하거나 확정할 항목

| 우선순위 | 확인된 사실 / 위험 | 수정 대상 및 방향 |
| --- | --- | --- |
| P1 | 현 검사 결과는 의도적으로 생산 승인되지 않는다. 통신·카메라 정상이어도 `tracker_valid=false`가 된다. | `nutrunner_axis_target_test.py::NutrunnerAxisTargetTestService._result`, `core_vision_bridge.py::CoreVisionBridge.snapshot`. 아래 생산 승인 조건을 먼저 정의한다. |
| P1 | 레시피 포인트와 Vision 검사 대상이 현재 TCP 계약에 결합되지 않는다. 향후 승인 기능을 추가하면 이전 검사 대상의 결과가 새 포인트에 사용될 수 있다. | `core_vision_bridge.py::handle`, `nutrunner_axis_target_test.py::configure`, `nutrunner_live_tracking_service.py::core_snapshot`. 대상 변경 명령/응답과 결과 식별자를 추가한다. |
| P1 | 결과 유효 시간은 분석 완료 시점 기준이다. 오래 분석한 입력 프레임을 최신 결과로 판단할 수 있다. 현재 생산 승인 차단은 유지된다. | `camera_service.py::wait_for_borrowed_frame`, `nutrunner_live_tracking_service.py::_run_worker/core_snapshot`, `core_vision_bridge.py::snapshot`. 입력 프레임 시각과 결과 시각을 구분한다. |
| P1 | `requirements.txt`에는 Flask만 선언되지만 앱 구성 단계에서 NumPy와 OpenCV를 강제 import한다. 두 모듈 없는 새 환경은 시작 실패한다. | `requirements.txt`, `app_factory.py`, `nutrunner_axis_target_test.py`, `nutrunner_tracker/marker_detector.py`. 기본 영상 의존성과 플랫폼별 카메라 의존성을 분리해 선언하고 설치 검사를 추가한다. |
| P2 | 정지 직후 이전 워커가 0.5초 안에 끝나지 않으면 `start()`가 워커를 새로 시작하지 않고 반환한다. TCP 응답은 그래도 `accepted=true`다. | `nutrunner_live_tracking_service.py::start/stop`, `core_vision_bridge.py::handle`. 정지 완료와 재시작 예약/실행 상태를 명확히 처리한다. |
| P2 | `pan_error_deg`, `tilt_error_deg`는 브리지에서 항상 `None`이다. 현재 Core의 `laser_target_ready` 조건은 충족할 수 없다. | `core_vision_bridge.py::snapshot`, `nutrunner_axis_target_test.py`. 실제 기준 좌표·각도 계산과 품질 검증을 추가하기 전에는 `None`을 유지한다. |
| P2 | 직접 `app.py` 실행은 TCP 기본 비활성, 카메라는 test 기본값이다. Windows에서 Linux 실행 명령과 장치명을 그대로 사용하면 운전 경로가 열린다고 보장할 수 없다. | `config.py`, `app.py`, 각 플랫폼 실행 스크립트. 아래 실행 조건을 명시적으로 적용한다. |

### 2.1 생산 승인 제약

`NutrunnerAxisTargetTestService._result()`는 `diagnostic_only=true`, `production_approved=false`, `permit=false`를 반환한다. `CoreVisionBridge.snapshot()`는 `production_approved`를 `tracker_valid`의 필수 조건으로 사용한다. 이는 현재 구현의 의도된 진단 제한이다. `_result()`의 승인 플래그만 `True`로 바꾸면 안 된다.

생산 연동 구현 전 다음을 확정한다.

1. 제품/도면/대상 포인트/캘리브레이션의 식별자와 버전, 현 프레임 카메라 fingerprint가 일치한다.
2. MASTER 및 소켓 자세 품질, 재투영 오차, 가림/모션 블러/연속 안정 프레임 조건을 만족한다.
3. 현재 계산이 영상상의 투영 위치 진단인지 실제 3D 접촉 위치 판정인지 구분한다. 미확인 접촉 Z나 각도를 0 또는 유효 값으로 대체하지 않는다.
4. 생산 승인 조건을 독립적으로 평가하고, 검증 실패/정보 없음/구버전 결과는 승인하지 않는다.
5. Core/PLC 정지·운전 허가·고장 조건과 별개로 Vision 결과가 자동 레이저 발사 또는 체결 허가를 만들지 않게 한다.

실카메라로 제품 위치 변화, 틀린 포인트, 캘리브레이션 변경, 가림, 통신 중단을 시험한 뒤 승인 경로를 적용한다.

### 2.2 레시피와 검사 대상 연결

현 TCP 브리지는 추적 시작/정지만 받고 Core의 `recipe_id`/`point_id`를 검사 대상으로 설정하지 않는다. Vision 검사 대상은 별도 HTTP 경로의 `configure_target_test()`를 통해 정한다. Core와 Vision에서 같은 포인트를 보고 있다는 증거가 없다.

수정할 때 기존 명령은 유지하고 별도 대상 설정 명령을 추가한다. Core 레시피 포인트를 Vision CAD target/context로 변환하는 명시적 매핑을 사용한다. 응답과 모든 유효 결과에는 대상 ID, 요청 sequence, 제품/캘리브레이션 revision을 포함한다. 대상 변경 즉시 이전 결과·안정 프레임 누적을 무효화하고 새 프레임 결과만 허용한다. Core도 현재 적용 포인트와 응답 대상이 일치할 때만 위치 OK를 소비해야 한다. 프로토콜 확장이므로 양쪽을 함께 변경·검증한다.

### 2.3 입력 프레임 기준의 시간 검증

`_run_worker()`는 `_analyze()`가 끝난 뒤 `_last_result_at=completed_at`을 저장한다. `core_snapshot()`의 `result_age_seconds`는 이 완료 시각부터 경과한 시간이다. 브리지는 현재 카메라의 최신 프레임 나이와 이 결과 나이가 각각 0.5초 이하인지 검사한다. 분석에 1초가 걸리는 동안 카메라가 계속 새 프레임을 공급하면 오래된 입력 결과도 완료 직후에는 두 검사를 모두 통과할 수 있다.

기존 `(frame, frame_id)` 호출 계약을 깨지 않는 별도 metadata 조회/메서드를 추가해 프레임 ID와 수집 시각을 함께 확보한다. 분석 결과에 입력 프레임 ID, 수집 시각, 완료 시각, 분석 소요 시간을 보관한다. 브리지는 동일 결과가 사용한 프레임의 현재 나이를 검사한다. 원시 프로세스 간 monotonic 시각을 직접 비교하지 말고 Vision에서 계산한 경과 시간을 전송한다. ROS 경로는 원본 `message.header.stamp`를 현재 버리는 점도 검토해 큐 지연을 구분한다.

0.5초 기준을 단순히 늘려 유효하게 만들지 않는다. RDK 전체 운전 부하에서 입력 프레임 나이, 분석 지연의 P95/P99, 누락률을 측정하고 실제 운전에서 허용하는 최대 지연으로 기준을 정한다. 기준 초과 시 값은 `None`, `tracker_valid=false`로 반환한다.

### 2.4 추적 재시작

`stop()`은 이벤트를 설정하고 바로 반환한다. `start()`는 이전 워커가 종료 중일 때 0.5초 join 후 아직 살아 있으면 그냥 반환한다. 이 경우 정지 이벤트가 남고 `running=false`인데 브리지는 `accepted=true`로 응답한다. 분석이 느린 RDK에서 정지→시작을 빠르게 반복하면 재현 가능하다.

수정 방향은 기존 워커 종료 후 재시작을 확실히 예약하거나, 아직 종료 중이면 진행 중/재시도 가능한 상태를 응답하는 것이다. `stop()` 완료 여부와 `start()` 수락을 실제 `running` 상태와 구분한다. 분석 I/O를 HTTP/TCP 스레드에서 무제한 기다리지 않는다. 종료되지 않은 워커와 새 워커가 동시에 카메라나 결과 상태를 갱신하지 않도록 한다. 검증은 분석에 1초 이상 걸리는 조건의 정지→즉시 시작, 반복 정지/시작, 카메라 미연결로 수행한다.

### 2.5 이번에 수정한 Core 추적 시작의 자기 차단

기존 Core는 TRACKER bit 0(결과 무효)을 모든 고장과 함께 추적 시작 차단에 사용했다. 정지 중인 정상 Vision은 `tracker_valid=false`이므로 시작하지 못하고, 일시적인 결과 유실 뒤에는 분석을 정지시켜 복구하지 못하는 순환이 있었다. `src/core/orchestration.py`는 이 비트를 Vision 분석 시작 차단에서 제외한다. 2026-10-14 보완부터 대상 부재·기존 결과 무효는 D+23 상위=81(미사용) 고장 대신 D+23 하위=85 경고로 처리한다. Tracker의 다른 고장, 바이패스하지 않은 다른 장치 고장, FORCE_STOP은 계속 추적을 차단한다. RUN_ENABLE 해제는 PLC 쓰기만 중지한다. 위치 OK와 생산 승인에 실제 유효 결과가 필요한 조건은 유지된다.

시작/정지 응답 수락 후 실제 `tracking_active`가 요청 상태와 다르면 최소 1초 간격으로 재시도한다. 이 조치는 2.4의 Vision 재시작 결함을 Core에서 완화하지만 Vision 자체 워커 종료/재시작 처리의 수정 필요성을 없애지는 않는다.

## 3. 플랫폼별 배포 지침

### 공통

현재 선언 누락을 수정할 때 NumPy와 ArUco를 제공하는 OpenCV 구성을 명시한다. 환경에 있는 `cv2`가 기본 opencv인지 contrib 빌드인지 확인하고, 겹치는 여러 opencv 패키지를 같은 환경에 설치하지 않는다. 설치 검사는 Python/OS/아키텍처, `numpy`, `cv2`, `cv2.aruco`, 사용하는 ArUco API, `pypylon`(Basler 경로)을 확인한다. Windows venv를 ARM64 장치에 복사하지 않고 각 플랫폼에서 생성한다.

Core `vision.enabled`와 `processes.vision.enabled`가 모두 필요한 설정인지 확인하고 활성화한다. 현재 기본 설정은 둘 다 false다. `vision.host/port`는 TCP 서버를, `web_host/web_port`는 브라우저용 HTTP 서버를 가리킨다. 다른 PC/RDK에서 접속할 때 `127.0.0.1`은 해당 접속 장치 자신이다. 리슨 주소 `0.0.0.0`을 목적지 주소로 저장하지 않는다.

### Windows 11 x64

현재 `fvp` 및 공식 런처는 Bash/Linux 명령을 사용하므로 Windows 네이티브 실행 스크립트를 별도로 제공해야 한다. 우선 코드 변경 없이 검증할 경우, Vision 저장소의 준비된 Python 환경에서 다음과 같이 직접 실행한다.

```powershell
# Vision 저장소 경로에서 실행. 사용하는 실제 카메라 방식에 맞춰 설정한다.
$env:FVP_CORE_VISION_TCP_ENABLED = '1'
$env:FVP_CORE_VISION_TCP_HOST = '0.0.0.0'
$env:FVP_CORE_VISION_TCP_PORT = '8768'
$env:FVP_PORT = '8767'
$env:CAMERA_SOURCE = 'basler'
$env:BASLER_SERIAL = '<실제 카메라 시리얼>'
# 아래 경로는 실제로 구성한 Vision 전용 venv의 interpreter로 바꾼다.
& .\.venv\Scripts\python.exe app.py
```

UVC 카메라를 사용하는 경우 `CAMERA_SOURCE=device`, `CAMERA_DEVICE=0` 등 실제 Windows 입력 장치로 바꾼다. 기본 `/dev/video0`은 Linux 장치명이다. `CAMERA_SOURCE=test`는 실제 카메라 연결 및 생산 유효성 검증을 대신하지 않는다. Windows 방화벽과 Basler 드라이버/카메라 IP 설정도 실제 연결 환경에서 확인한다.

### RDK X5 3.5

RDK 이미지 3.0 이상은 Ubuntu 22.04 기반이라는 공식 문서와, 실제 설치 장치의 `/etc/os-release`, `uname -m`, Python 버전을 함께 확인한다. 버전 문자열만으로 설치된 SDK와 Python 모듈 구성을 가정하지 않는다. [D-Robotics 다운로드 문서](https://developer.d-robotics.cc/rdk_x_doc/Quick_start/download?p=RDK+X5&v=3.5.0)

Basler 경로는 `fvp` → `tools/start_fvp_basler_camera.sh`에서 TCP 8768을 활성화하며 웹은 기본 8767이다. 직접 Basler 경로는 ROS를 통한 프레임 복사를 사용하지 않는다. `integrations/basler_ros/requirements-basler.txt`의 현재 고정 버전은 `pypylon==26.8`이다. Basler 공식 문서는 Windows 11 x64 및 Linux ARM64 지원을 명시하지만 실제 선택 버전의 wheel, glibc, Python ABI, pylon 카메라 동작까지 RDK에서 확인해야 한다. [pylon 플랫폼 지원](https://docs.baslerweb.com/pylon-software-suite), [pypylon 공식 설치 조건](https://github.com/basler/pypylon#installation)

MIPI 경로의 `tools/start_fvp_single_camera.sh`는 `/opt/tros/humble/setup.bash` 및 ROS2 카메라 토픽을 사용한다. 이 런처는 웹 기본값이 8000이고 TCP 활성화를 직접 export하지 않는다. Core와 연결할 때 실행 환경에 `FVP_CORE_VISION_TCP_ENABLED=1`, `FVP_CORE_VISION_TCP_HOST=0.0.0.0`, `FVP_CORE_VISION_TCP_PORT=8768`을 지정하고 Core 웹 포트를 실제 값에 맞춘다. ROS가 보이는 interpreter/환경으로 실행한다. Basler와 MIPI를 동시에 같은 카메라 소유자로 시작하지 않는다.

점검한 Windows 작업 사본의 `fvp`, `start_fvp_basler_camera.sh`, `start_fvp_single_camera.sh`는 모두 CRLF였다. Windows 폴더를 그대로 복사하면 Linux shebang/구문 오류를 낼 수 있다. **배포 사본의** Bash 파일은 LF로 정규화하고 실행 권한을 확인한다. Git에는 이 세 파일이 `100755`로 기록되어 있지만 압축·공유폴더 복사 뒤에도 권한이 유지되는지 확인한다. 향후 Vision 저장소를 수정할 때 `.gitattributes`의 Bash 파일 `eol=lf` 규칙을 추가한다. 이번 점검에서는 Vision 파일을 변환하지 않았다.

## 4. 최소 현장 검증

1. 웹 포트가 열린 뒤 별도로 TCP 8768에 `vision.status`를 보내 같은 ID의 응답을 확인한다.
2. `/api/camera/status`에서 실제 장치, 해상도, 측정 FPS, frame_id 증가, frame_age, 오류 및 calibration fingerprint를 확인한다.
3. 현재는 진단 모드이므로 좋은 영상에서도 `production_approved=false`, `tracker_valid=false`가 정상이다. 그 이유가 운전 화면에 설명되는지 확인한다.
4. 카메라 케이블 분리·재연결, 분석 오류, 0.5초 이상 프레임 지연, TCP 서비스 중단·복구 시 오래된 OK가 유지되지 않는지 확인한다.
5. 정지→즉시 추적 시작을 반복하고 `accepted`뿐 아니라 `tracking_active` 복귀를 확인한다.
6. 향후 생산 승인 구현 후에는 포인트 변경·제품 변경·카메라/캘리브레이션 변경 직후 이전 대상 결과가 무효화되는지 확인한다.
7. Core·Gateway·PTM·Vision·브라우저를 함께 운전하며 RDK CPU/메모리, 입력 프레임 나이와 분석 지연, Core 주기, PLC heartbeat/쓰기 ACK를 기록한다. 개별 카메라 테스트 성공만으로 전체 부하 운전을 승인하지 않는다.

## 5. 이번 확인 결과와 한계

- 실제 Vision `CoreVisionTCPServer`와 Core `VisionTcpClient`를 로컬 임시 TCP 포트로 연결한 9개 검사 통과: 진단 승인 차단, 정상 구조, 카메라/결과 지연, NaN 무효화, 추적 시작·정지, 미지원 명령 및 잘못된 params.
- 카메라/추적 값은 테스트 대역이었다. Vision 앱이나 카메라 장치를 시작하지 않았다.
- 종료 중 워커가 남은 상태의 `start()`가 `running=false`로 반환하는 경로를 실제 메서드의 메모리 실행으로 재현했다.
- Core 추적 제어 테스트 6개가 Python 3.10/3.12에서 각각 통과했다. 정지 상태 시작, 마커 유실 복구, 실제 상태 불일치 재시도, 다른 고장 및 정지 조건의 차단을 확인했다.
- 직접 연관된 Vision Python 파일 11개의 메모리 문법 검사 통과. Vision 저장소에 pycache, 로그, 테스트 파일을 만들지 않았다.
- Windows 11 실제 카메라, RDK X5 ARM64 3.5의 라이브러리 설치/영상 지연/복구 및 PLC 실장비 통합은 현장에서 추가 확인해야 한다.

## 6. 2026-10-14 Vision 앱 후속 수정 지침

이번 작업은 Core 코드와 이 문서만 수정한다. 아래 Vision 파일은 읽기 전용으로 확인했으며, Vision 앱 개발자가 후속 작업에서 적용한다.

### 6.1 대상 검출과 결과 생성 상태 분리

현재 `web/backend/services/core_vision_bridge.py::CoreVisionBridge.snapshot()`은 `tracker_valid`에 검출 품질, 프레임·결과 신선도, 생산 승인까지 묶는다. `nutrunner_live_tracking_service.py::core_snapshot()`에는 결과 나이가 있지만 TCP 응답에는 반환하지 않는다. 따라서 현재 응답만으로는 대상이 안 보이는 상태와 대상은 보이지만 결과가 생성되지 않는 상태를 구분할 수 없다.

기존 JSON Lines 봉투와 필드를 유지하고 `vision.status.result` 최상위에 다음 필드를 추가한다. Core는 `tracker` 객체 내부의 같은 필드도 호환 처리한다.

| 필드 | 형식 | 의미 |
|---|---|---|
| `tracker_present` | boolean 또는 `null` | 최신 영상에서 이번 추적 대상이 실제로 보이는지. 정보 없음·오래된 검출은 `null` |
| `result_ready` | boolean 또는 `null` | 현재 대상의 필요한 계산 결과가 생성됐는지. 생산 승인 여부와 별개 |
| `result_age_seconds` | 유한한 0 이상 숫자 또는 `null` | 마지막 해당 계산 결과 생성 후 경과 시간. 결과 없음은 `null` |

예를 들어 대상 부재는 `tracker_present=false`, `result_ready=false`, `tracker_valid=false`, 위치값 `null`이다. 대상이 보이지만 계산 중이면 `tracker_present=true`, `result_ready=false`이다. 진단 계산이 완료됐지만 생산 승인 전이면 `result_ready=true`, `production_approved=false`, `tracker_valid=false`가 함께 가능하다. `result_ready`를 `tracker_valid`나 `production_approved`와 동일한 값으로 만들지 않는다.

검출 사실을 `master_good`/`socket_good`에 그대로 연결하지 않는다. 이 값에는 보정·품질 조건도 포함된다. 등록된 대상의 실제 검출 상태를 사용하고, 다른 마커가 보인다는 이유로 대상 존재를 참으로 만들지 않는다. 검출 단계의 상태는 후속 위치 계산이 완료되기 전에도 갱신되어야 한다. `_last_result`의 과거 검출만 재사용하면 계산이 멈춘 상태를 현재 대상 존재로 오판하므로 입력 프레임의 신선도를 확인한다. 카메라 재연결·대상 변경·추적 재시작 시 이전 검출·결과 상태를 무효화한다.

구버전 Vision의 추가 필드 누락은 `null`로 처리한다. Core는 `tracker_valid=true`에서 대상 존재를 추론할 수 있으나, `tracker_valid=false`만으로 대상 부재나 결과 생성 실패를 확정하지 않는다. **현재 Vision 앱을 그대로 사용하면 대상이 보이지만 결과가 생성되지 않는 경우를 정확히 판별하는 기능은 제한된다. 위 필드의 후속 구현이 필요하다.**

### 6.2 PLC 알람 구분

| 상태 | Core의 PLC 처리 |
|---|---|
| 추적 요청 또는 실행 중 대상이 안 보임 | D+23 상위=81(미사용) Tracker 고장은 사용하지 않고 D+23 하위=85 경고로 처리. 위치 OK는 해제 |
| 대상 부재 또는 존재 여부 불명 | 결과 생성 지연으로 D+23 상위=6를 생성하지 않으며 Vision이 보낸 해당 비트도 제외 |
| 추적 요청 또는 실제 추적 중, 필수 카메라 online·valid, 대상 존재 확인 | `result_ready=false`가 `runtime.vision_status_max_age_ms`를 넘겨 지속되거나 `result_age_seconds`가 이 기준을 넘으면 D+23 상위=6 결과 생성 Timeout |
| 정상 TCP 응답이지만 필수 카메라 미연결 | 내부 원인은 Camera 1 미연결과 Vision 카메라 누락이며 API/이력에 모두 기록. D+23 상위 BYTE에는 우선순위가 높은 Vision 카메라 누락 코드 1만 표시. TCP 상태 Timeout과 구분 |
| 실제 TCP 연결/응답 시간 초과 | D+23 상위=7 VISION_COMM_TIMEOUT |
| 마지막 정상 상태 수신 이후 허용 시간 초과 | D+23 상위=8 VISION_STATUS_STALE |
| 연결 거절·종료 등 시간 초과 이외의 TCP 실패 | D+23 상위=9 VISION_COMM_LOST |
| Vision 실효 바이패스 | Vision·Camera·Tracker의 활성·래치 Warning/Fault를 PLC 출력·대표 코드에서 제외. 실제 장치 상태는 보존 |

D+23 상위 BYTE=6은 결과 생성/결과 나이 Timeout만 표현한다. 통신 Timeout은 상위 BYTE=7, 상태 수신 만료는 8, 일반 통신 실패는 9로 구분한다. 경고가 없을 때의 WORD는 각각 0x0700, 0x0800, 0x0900이다. 통신·상태 수신 오류는 정상 조회 복구 시 자동 해제하며, 보호·설정 고장의 latch/reset 조건은 유지한다. 정상 응답의 `online=false`만으로 TCP 조회 실패로 취급하지 않는다. 대상이 사라지면 진행 중인 결과 대기 시간을 취소하고, 다시 검출됐을 때 새 대기를 시작한다.

Vision은 대상 부재만으로 `fault=true`, D+23 상위=81(미사용) 또는 D+23 상위=6를 보내지 않는다. 카메라 미연결은 `cameras[0].online=false`, `valid=false`, 각 마스크 bit 0 해제로 사실을 반환한다. 마스크와 배열은 서로 일치시킨다. 실제 분석 워커 오류·설정 오류는 대상 부재와 구분해 기존 고장 계약으로 보고한다. 카메라 연결 여부를 웹 접속 성공이나 TCP `ok=true`로 대체하지 않는다.

### 6.3 웹 iframe 점검

현재 Vision 앱 소스에서는 `X-Frame-Options` 또는 CSP `frame-ancestors` 제한을 확인하지 못했다. Core는 설정된 웹 URL이 있으면 TCP 상태와 관계없이 iframe을 로드한다. 현장에서도 새 창만 열리고 iframe이 차단된다면 브라우저 콘솔과 Vision HTTP 응답 헤더를 확인한다. 배포 프록시가 `DENY`/`SAMEORIGIN` 또는 제한적인 `frame-ancestors`를 넣는 경우 실제 Core 웹 origin을 허용하도록 Vision 배포 설정을 수정한다. Core가 HTTPS이고 Vision이 HTTP인 경우에는 브라우저의 혼합 콘텐츠 차단을 확인하고 접속 구성을 맞춘다.

### 6.4 후속 검증

1. 추적 요청 또는 실행 중 카메라 정상·대상 부재 상태를 유지한다. 다른 원인이 없으면 D+23 하위 BYTE=85, 상위 BYTE=0이어야 한다. 대상 부재로 Tracker 고장이나 결과 생성 타임아웃을 만들지 않아야 한다.
2. 대상 존재 상태에서 계산 결과만 중단한다. 정해진 대기 시간 이후 결과 생성 타임아웃이 기록되고, 더 높은 우선순위 고장이 없으면 D+23 상위 BYTE=6이어야 한다. 계산이 복구되거나 대상이 사라지면 자동 해제되어야 한다.
3. 정상 TCP 응답을 유지한 채 카메라를 분리한다. 내부 Camera 1 미연결과 Vision 카메라 누락 원인은 모두 기록되고 D+23 상위 BYTE에는 우선순위가 높은 코드 1만 표시되어야 한다. 결과 생성 Timeout으로 잘못 분류하지 않으며 재연결 후 현재 상태에 따라 자동 복구해야 한다.
4. PLC 요청·Core 웹·Vision 웹 각각에서 바이패스를 적용한다. Vision 계열 알람과 대표 오류/경고가 PLC 출력에서 제외되고 실제 측정값을 만들지 않아야 한다. Vision 웹 바이패스 해제를 Core가 계속되는 상태 폴링으로 감지해야 한다.
5. Vision TCP 서비스를 끊는다. 바이패스가 없으면 기존 통신 Timeout이 발생해야 한다. 별도로 웹 URL이 살아 있으면 Core 사이드바 iframe은 로드되어야 한다.
6. 실제 카메라·RDK·PLC에서 같은 조건을 검증한다. Core의 테스트 대역 검증은 실제 검출 상태와 영상 처리 지연의 증거를 대신하지 않는다.

## 7. 현재 레시피/포인트와 체결 완료 연동 (2026-10-16)

Core의 대상 전송과 Vision의 `CoreVisionBridge.handle()` 내 `vision.target.set`, `vision.fastening_complete` 수신부가 구현되어 있다. 이동 완료 후 Vision 대상 적용 여부는 Core의 `vision.work.target_synced`와 Vision의 `last_core_request`를 함께 확인한다. TCP 상태 조회 성공만으로 대상 적용 성공을 판단하지 않는다.

### 7.1 PLC 명령과 처리 순서

- D+0.A는 Reserved를 유지한다. 체결 완료에는 비트를 할당하지 않는다.
- D+12 `COMMAND_CODE=6` = `FASTENING_COMPLETE`. D+10/11 `REQ_RECIPE_ID`/`REQ_POINT_ID`에 체결 대상 ID(1~65535)를 넣고, D+13 `COMMAND_SEQ`(0~65535)를 이전 요청과 다르게 갱신한다. 명령 5 `NUTRUNNER_DATA_REQUEST`는 유지한다.
- 명령 1의 PTM 이동 완료를 확인하면 Core는 적용된 레시피/포인트와 이동 명령 SEQ를 `vision.target.set`으로 백그라운드 전송한다. ACK 실패는 상태 `vision.work.last_error`에 표시하고 약 1초 간격으로 재시도한다. 통신 단절을 관측한 뒤 재연결하면 다시 전송한다.
- 명령 6은 요청 ID를 먼저 `vision.target.set`으로 전달한 후 `vision.fastening_complete`를 전송한다. 이때 `command_seq`는 PLC 명령 6의 SEQ이다. 현재 적용 포인트를 체결하려면 PLC는 명령 1에 사용한 동일한 레시피/포인트 ID를 넣는다.
- Core는 레시피/포인트/SEQ/체결 이벤트 ID가 일치하는 유효 좌표를 받자마자 `laser.off`를 전송한다. 각도·기울기가 없어도 좌표를 수신한다. 통신 응답 처리에 따른 소프트웨어 OFF 요청이며 실제 GPIO 지연은 실장비에서 측정한다.
- 레이저 OFF 응답까지 확인하면 명령 `COMPLETE/OK`. OFF 실패는 `ERROR/INTERNAL_ERROR`와 레이저 오류로 기록하며 측정값을 보존한다. 다음 유효한 명령 1 수락 전까지 Core의 자동·수동 레이저 ON/PULSE를 차단한다.
- 명령 5는 새 SEQ의 요청을 접수한 즉시 `laser.off`를 백그라운드 전송하고 Core의 Position OK 판정을 보류한다. 데이터 응답을 기다리지 않고 PLC `Tracking State=OFF`, `TRACKING_ACTIVE=0`, `POSITION_OK=0`, `POSITION_STABLE=0`, `LASER_TARGET_READY=0`으로 출력하며 자동·수동 레이저 ON/PULSE를 차단한다. Vision의 카메라·측정 수집과 `vision.result` 데이터 요청은 유지한다. 이 상태는 응답 실패나 같은 SEQ 유지, 명령 0, 절전 해제로 풀리지 않으며, 다음 지원 명령(1~6, 새 SEQ)을 접수할 때 해제한다. 다음 명령도 5이면 계속 보류한다. 상태는 `vision.work.position_check_suspended`에서 확인한다.
- 명령 5에서 지원하지 않는 결과는 `REJECTED/TRACKING_UNAVAILABLE`, 잘못된 식별자/통신 실패/대기 시간 초과는 `ERROR/TRACKING_UNAVAILABLE`이다. Vision이 보낸 `RECIPE_NOT_FOUND`/`POINT_NOT_FOUND`는 해당 PLC 결과로 반환한다. 레이저 OFF 실패는 레이저 오류로 기록하고, 유효 데이터 수신 후 OFF 실패를 확인한 명령은 `ERROR/INTERNAL_ERROR`로 처리한다. 다음 명령을 접수해도 기존 안전 인터록과 명령 6의 유효 결과 수신 후 재점등 차단 규칙은 적용한다.
- 같은 SEQ를 유지한 명령 6은 재실행하지 않는다. FORCE_STOP은 진행 중 요청을 취소한다. RUN_ENABLE 해제는 요청을 취소하지 않고 PLC 응답 쓰기만 중지한다. Vision 바이패스에서는 통신·가짜 측정 없이 `COMPLETE/OK`로 끝난다. 바이패스에서도 기존 안전 인터록은 적용한다.

### 7.2 Vision TCP JSON Lines 계약

대상 설정 요청과 응답:

```json
{"id":"core-target-1","command":"vision.target.set","params":{"recipe_id":2,"point_id":7,"command_seq":100}}
{"id":"core-target-1","ok":true,"result":{"accepted":true,"recipe_id":2,"point_id":7,"command_seq":100}}
```

Vision은 ID를 검증하고 실제 검사 대상에 반영한 후 ACK한다. 다른 대상에서 계산한 캐시 결과를 재사용하지 않는다. `accepted=true`와 동일한 정수 식별자를 모두 반환해야 한다. 대상 변경 시 진행 중 분석 결과도 대상 버전으로 구분한다.

체결 완료 요청과 좌표 응답:

```json
{"id":"core-complete-1","command":"vision.fastening_complete","params":{"recipe_id":2,"point_id":7,"command_seq":101,"event_id":"unique-fastening-event"}}
{"id":"core-complete-1","ok":true,"result":{"recipe_id":2,"point_id":7,"command_seq":101,"event_id":"unique-fastening-event","supported":true,"data_valid":true,"fastening_coordinate_mm":{"x":10.0,"y":-20.5,"z":30.0},"tightening_angle_deg":null,"inclination_deg":null,"timestamp_ms":1792110000123,"coordinate_frame":"workpiece"}}
```

Core가 요청한 같은 TCP 연결에서 한 줄 JSON 응답으로 반환한다. 식별자와 `event_id`를 그대로 반환하고, 좌표 x/y/z는 유한한 실측 수치, `timestamp_ms`는 양의 정수인 측정 시각으로 보낸다. 각도·기울기는 추후 구현 전 `null` 또는 생략 가능하다. 나중에 제공하면 유한한 각도(도)로 보낸다. 좌표계 이름은 확인된 경우에만 문자열로 보낸다.

계산 중에는 식별자/이벤트 ID와 함께 `supported=true`, `data_valid=false`, 미확정 측정값 `null`로 응답한다. Core는 명령 제한 시간(`runtime.command_timeout_s`) 안에서 약 200ms 간격으로 같은 이벤트를 재조회한다. Vision은 동일한 `event_id`를 동일한 체결 사건으로 처리해 다시 측정 트리거하지 않고, 준비되면 그 사건의 결과를 반환한다. TCP 오류는 성공으로 간주하지 않는다. 기능 미구현 상태는 `supported=false`, `data_valid=false`로 명확히 반환한다.

### 7.3 확인 항목

Core의 TCP 테스트 서버로 대상 전달, 명령 6(SEQ 0 포함), 중복 SEQ 방지, 지연/잘못된 결과 거부, 좌표만 수신, OFF 실패 및 취소 중 OFF 유지, 재점등 차단을 검증한다. 배포 시 Core와 Vision의 수신 계약 및 저장 설정을 맞추고 실제 카메라/PLC/PTM에서 좌표 대상 일치와 레이저 OFF 지연을 확인한다.

명령 5는 데이터 응답을 지연시켜도 먼저 레이저 OFF 요청과 PLC 추적 OFF가 나오는지 확인한다. 결과 오류·잘못된 대상 ID·바이패스에서도 Position OK를 내보내지 않아야 하며, 카메라 장애 알람은 계속 유지한다. 같은 SEQ·명령 0·절전 해제·지원하지 않는 명령은 보류를 해제하지 않아야 한다. 다음 지원 명령의 새 SEQ에서 판정이 재개되고, Position Stable의 안정화 시간은 처음부터 다시 측정해야 한다.

### 7.4 대상 수신 설정과 재동기화 확인

- Core는 PTM의 `completed_id`가 이번 이동의 `motion_id`와 일치한 뒤 해당 레시피/포인트/PLC SEQ를 전송한다. PTM 이동 `COMPLETE`와 Vision ACK는 별개다. FORCE_STOP·고장에 따른 안전 정지·절전은 Core의 적용 대상을 지울 수 있으므로, 과거 `COMPLETE`만 보지 말고 `runtime.target_context.applied`와 `vision.work.target`을 확인한다.
- Vision `/api/vision/run-signal`의 `enabled=true`와 실제 레시피/포인트에 대응하는 `mappings`가 필요하다. Vision 레시피 등록, 포인트 등록, CAD 대상·설치 위치·카메라 매핑, 유효한 보정은 각각 확인한다. PTM의 레시피에 포인트가 있어도 Vision에 자동 등록되지는 않는다. 설정을 자동 생성하거나 임의의 CAD 포인트에 연결하지 않는다.
- 기본 명령은 `vision.target.set`, 필드 위치는 `params.recipe_id`, `params.point_id`, `params.command_seq`다. 저장된 사용자 명령/필드 설정을 사용한다면 Core 계약과 일치시킨다. 비활성 수신도 정상 형식의 요청이면 `last_core_request`에 ID/SEQ와 `run_signal_disabled` 거부 사유를 남긴다. 기존 일반 명령 분기의 `INVALID_PARAMS: params must be an empty object` 대신 `INVALID_TARGET: run_signal_disabled`를 반환해 원인을 구분한다.
- `/api/vision/run/status`의 `last_core_request.accepted`와 `reason`으로 미수신/수신 거부/적용 성공을 구분한다. Core의 `target_synced=false`와 `vision.work.last_error`도 함께 확인한다.
- 카메라/추적 중단 또는 대상 만료 후 Core가 마지막으로 수락된 동일 `(recipe_id, point_id, command_seq)`를 재전송하면 Vision은 매핑·레시피·보정을 다시 검증하고 `WAIT_TRACKING`에서 새 표본을 수집한다. 과거 Position OK나 측정값은 재사용하지 않는다. 활성 대상에 대한 정상 갱신은 표본을 초기화하지 않는다. 같은 SEQ의 다른 대상 및 오래된 SEQ는 계속 거부하고, 제품 변경 뒤에는 이전 대상의 동일 SEQ로 복구하지 않는다.

### 7.5 Vision 포인트 등록 순서

1. **VISION → 레시피·포인트**에서 PLC와 동일한 레시피 ID를 등록한다. 해당 레시피를 선택하고 포인트 ID·이름을 입력해 **포인트 추가**를 누른다. 예를 들어 PLC의 레시피 3 / 포인트 15는 Vision에도 숫자 ID `3 / 15`로 등록한다. CAD ID `G3-015`는 다음 단계에서 연결하며, 숫자 포인트 ID 대신 입력하지 않는다.
2. **VISION → Core 수신 신호**에서 **지정점 수신 사용**을 켠다. **연결 추가**로 같은 숫자 레시피·포인트 ID에 실제 CAD 보정 포인트, 설치 위치의 `capture_set_id`, 카메라 위치, 확인된 평면 높이를 지정하고 **설정 저장**을 누른다. 화면의 설치 위치 프로필 이름(`SETTING-001` 등)과 실제 `capture_set_id`는 다를 수 있으므로 저장된 보정 정보를 확인한다.
3. 해당 CAD 그룹의 카메라 보정과 제품 위치 4점 보정이 유효한지 확인한다. 포인트 ID 등록·연결 설정과 실제 위치 측정 준비 상태는 별개다. 보정되지 않은 다른 그룹까지 현재 그룹의 보정으로 대체하지 않는다.
4. PLC에서 새 SEQ의 `TARGET_APPLY_MOVE`를 실행한다. 이동 후 Core `vision.work.target_synced=true`, Vision `last_core_request.accepted=true`와 ID/SEQ 일치를 확인하고, 새 추적 표본으로 위치 판정이 진행되는지 확인한다.

저장 파일은 Vision 데이터 폴더 아래 `vision_recipes/recipes.json`(ID·이름), `core_integration/run_signal.json`(수신 설정·CAD 연결), `cad_targets/group_settings.json`(CAD 그룹 사용 설정)이다. 기본 데이터 폴더는 `vision/data`이며 `FVP_DATA_DIR` 설정 시 그 경로를 사용한다. 데이터는 Git 관리 대상이 아니므로 소스 코드만 배포하면 로컬 등록 내용은 장비로 옮겨지지 않는다. 장비의 기존 카메라·제품 보정 파일은 보존한다.

## v7 오류 정의의 소유 범위

Vision 원본 `vision_fault_word`에서 통신 타임아웃·상태 만료·통신 끊김에 대응하는 내부 bit 6/7/8은 Core가 판정한다. 원본 상태가 정상 도착했을 때 이 세 원인을 Vision이 재전송하여 유지시키지 않는다. 결과 타임아웃 bit 5는 대상 존재·필수 카메라 정상·추적 요청 조건을 함께 확인한다. 장치 내부 진단 비트와 PLC 코드 워드를 서로 직접 대입하지 않는다.

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
