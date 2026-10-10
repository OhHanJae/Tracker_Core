# Vision 연동 점검 및 수정 지침

점검일: 2026-10-09. 대상: Windows 11 x64 / RDK X5 3.5 이미지.

사용자 요청에 따라 Vision 저장소의 코드·설정·런처는 변경하지 않았다. 아래 항목은 실제 소스에서 확인한 제약과 수정 방향이다. 카메라, RDK, PLC 실장비 성능 검증은 이 Windows 작업 공간에서 수행하지 않았다.

## 1. 현재 동작 범위

Core와 Vision의 통신 계약은 서로 맞는다. Core는 `src/vision/vision_client.py`에서 `vision.status`를 호출하고, Vision은 `web/backend/services/core_vision_bridge.py`에서 처리한다.

- 웹: HTTP 8767. Core 운전 통신: TCP 8768.
- 프레임: UTF-8 JSON 한 줄 + LF. 길이 접두어 방식이 아니다.
- 요청: `{"id":"core-request-id","command":"vision.status","params":{}}`.
- 응답: 같은 `id`, `ok`, `result` 또는 `error`.
- 지원 운전 명령: `vision.status`, `tracking.start`, `tracking.stop`.
- Core 설정에 있는 `vision.result`는 현재 Vision이 지원하지 않는다. 현 Core 상태 폴링에서는 호출하지 않지만 별도 호출하면 `UNKNOWN_COMMAND`가 정상 응답이다. 불필요한 명령을 추가하기 전에 소비 경로부터 확정한다.
- 현재 단일 카메라 계약이다. Core도 첫 카메라와 마스크 bit 0만 처리한다. 복수 카메라를 추가하려면 양쪽 명세와 PLC 맵을 함께 검토해야 한다.

TCP 요청 성공, 카메라 연결, 프레임 유효, 검사 결과 유효는 별개다. 웹이 열리거나 `ok=true`라는 이유만으로 운전 준비 완료로 표시하면 안 된다.

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

기존 Core는 TRACKER bit 0(결과 무효)을 모든 고장과 함께 추적 시작 차단에 사용했다. 정지 중인 정상 Vision은 `tracker_valid=false`이므로 시작하지 못하고, 일시적인 결과 유실 뒤에는 분석을 정지시켜 복구하지 못하는 순환이 있었다. 이번에 `src/core/orchestration.py`에서 이 비트만 Vision 분석 시작 차단에서 제외했다. 같은 TRACKER의 다른 활성/유지 고장, 다른 모든 장치 고장, FORCE_STOP 및 RUN_ENABLE 해제는 계속 추적을 차단한다. 전체 알람은 그대로 유지하므로 모터·레이저 안전정지와 위치 OK/생산 승인 조건은 완화되지 않는다.

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
