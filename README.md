# Tracker Process Control Core

LastModified: 2026-10-09

비전, PTM/PT503/PT510 모터 API, 레이저, XGT Shared Memory Gateway, 글로벌 설정, 외부 모듈 실행 관리를 한 곳에서 묶는 Python 기반 코어 서버입니다.

## 역할

- PLC -> Controller 공유 메모리 100워드(200바이트)를 읽습니다.
- Controller -> PLC 공유 메모리 100워드(200바이트)를 씁니다.
- PLC 명령 `COMMAND_CODE + COMMAND_SEQ`를 감지해 외부 PTM 서버에 명령을 보냅니다.
- PTM, Laser, Vision 상태를 장비별 서비스 모듈에서 계속 폴링하고 마지막 실제값을 캐시합니다.
- 글로벌 설정과 장비 명령은 코어 TCP/HTTP 서버에서 직접 조회/수정합니다.
- 모듈별 `.bat`/`.sh` 실행 경로를 설정하고 Process Manager에서 실행/정지/재시작합니다.
- `web/` 정적 파일을 같은 포트에서 제공해 브라우저 관리 화면을 엽니다.

## 기본 포트

| 대상 | 기본 포트 | 비고 |
|---|---:|---|
| Core TCP/HTTP/Web | 8000 | 글로벌 설정/상태/수동 명령/웹 관리 화면 |
| Core Web 선택 HTTP | 80 | `core.http_port`를 설정한 경우 |
| XGT Gateway TCP | 15150 | Gateway 설정/상태 서버 |
| XGT Gateway Web | 5051 | PLC 주소·영역·통신 설정 |
| PTM API TCP | 8765 | `system.status`, `motion.stop`, `point.goto` 등 |
| PTM Web | 8080 | 레시피·모터·레이저 설정 |
| Vision TCP | 8768 | `vision.status` 기준 |
| Vision Web | 8767 | Vision 실행 환경에 따라 설정 |
| Laser TCP | 8768 | `laser.mode=tcp`일 때 사용, 기본은 `ptm` 모드 |

연결 주소와 포트는 `datas/global.json`에서 수정합니다. PLC 주소·영역 크기·주기는 Gateway 설정이 기준입니다. Core는 시작/설정 적용/운전 중 Gateway의 `get_config`를 읽으며 자동으로 덮어쓰지 않습니다. 예전 `configure_gateway_on_start=true` 값이 남아 있어도 자동 쓰기는 수행하지 않습니다. 명시적인 `xgt.configure` 명령만 Core의 PLC 설정을 Gateway에 전송합니다.

Core PLC 맵은 방향별 100 WORD(200 BYTE)입니다. Gateway의 크기가 다르거나 방향이 비활성화돼 있으면 설정 오류를 표시하고 PLC 운전을 차단합니다. Gateway Web에서 올바른 영역을 지정하세요.

운전 점검 결과와 운영체제별 배포 조건은 [RUNTIME_AUDIT.md](docs/RUNTIME_AUDIT.md), Vision 수정 지침은 [VISION_INTEGRATION_GUIDE.md](docs/VISION_INTEGRATION_GUIDE.md)를 참고하세요.

## 실행

Python 3.10 이상이면 Windows/Linux 모두 같은 소스로 실행합니다. 외부 Python 패키지는 아직 필요하지 않습니다.

### Windows

```bat
run_core.bat --log-level INFO
```

또는:

```bat
python main.py --log-level INFO
```

현재 PC처럼 venv가 깨졌거나 Python이 PATH에 없으면 Windows에서 먼저 `PYTHON` 환경변수로 실행 파일을 지정할 수 있습니다.

```powershell
$env:PYTHON="C:\Program Files\KiCad\8.0\bin\python.exe"
.\run_core.bat --log-level INFO
```

### Linux

```bash
chmod +x run_core.sh
./run_core.sh --log-level INFO
```

테스트:

```bash
python -m unittest discover -s tests
```

## 웹 관리 화면

Core 실행 후 브라우저에서 접속합니다.

```text
http://127.0.0.1:8000/
http://장비IP:8000/
http://장비IP/
```

화면 파일은 `web/` 아래에 있고, 빌드 과정 없이 Core HTTP 서버가 바로 서빙합니다.

| 화면 | 용도 |
|---|---|
| Dashboard | Core/PLC/PTM/Laser/Vision 상태와 주요 수동 명령 |
| Services | 설정 서버 명령센터, 모듈별 통신/프로세스 상태 |
| Process Manager | 모듈별 실행/정지/재시작 및 Health Check 상태 |
| Module Config | 모듈별 start/stop script, working directory, health endpoint 설정 |
| PLC | 공유메모리 연결, 입력/출력 워드 미리보기 |
| 운전 모니터 | Core 주기·명령 진행·PLC 쓰기 ACK·장비 상태 확인 |

## 보안/운용 메모

- Core 기본 바인딩은 TCP/HTTP `0.0.0.0:8000`입니다. `core.http_port:80`을 별도로 설정하면 포트 번호 없는 접속도 가능합니다. 선택 포트를 열지 못해도 8000 서버는 유지됩니다.
- XGT Gateway, PTM API, Vision 앱이 같은 장비에서 실행되면 각 서버 주소는 기본값 `127.0.0.1`을 그대로 쓰면 됩니다.
- 다른 장비에서 실행되는 서버에 붙일 때는 `datas/global.json`의 `xgt.control.host`, `motor.host`, `vision.host`를 해당 IP로 바꿉니다.
- TCP/HTTP에는 인증이 없으니 설비 내부망, 전용망, VPN 안에서만 열어두세요.
- 프로세스 `start/stop/restart` 명령은 원격 명령 실행 위험 때문에 기본적으로 localhost 접속에서만 허용합니다.

## Core TCP/HTTP 명령

TCP JSON Lines 방식은 한 줄에 JSON 객체 하나와 LF(`\n`)를 보냅니다. HTTP는 `/api/command`로 같은 명령을 보낼 수 있습니다.

| command | 설명 |
|---|---|
| `ping` | 연결 확인 |
| `get_status` | 코어/PLC/PTM/Laser/비전/알람 상태 조회 |
| `get_devices` | 장비별 서비스 상태 조회 |
| `get_processes` | 모듈별 프로세스/Health Check 상태 조회 |
| `get_config` | 글로벌 설정 조회 |
| `update_config` | `{"patch": {...}}` 부분 설정 저장 및 즉시 반영 |
| `reload_config` | `datas/global.json` 다시 로드 |
| `xgt.configure` | XGT Gateway를 현재 공유메모리/주소 설정으로 업데이트 |
| `xgt.status` | XGT Gateway 상태 조회 |
| `motion.stop` | PTM API에 `motion.stop` 전달 |
| `laser.on` | Laser 서비스에 ON 명령 전달 |
| `laser.off` | Laser 서비스에 OFF 명령 전달 |
| `alarm.reset` | latch fault reset |
| `device.command` | `{"device":"ptm","command":"motor.status","params":{}}` 형식의 장비 직접 명령 |
| `process.start` | `{"module":"ptm"}` 모듈 start script 실행, localhost 전용 |
| `process.stop` | `{"module":"ptm"}` 모듈 stop script/추적 프로세스 정지, localhost 전용 |
| `process.restart` | 모듈 정지 후 재실행, localhost 전용 |
| `process.status` | 모듈 프로세스/Health Check 상태 조회 |

브라우저/HTTP 주소:

| 주소 | 설명 |
|---|---|
| `/` | 웹 관리 화면 |
| `/api/status` | 상태 JSON |
| `/api/config` | 설정 JSON |
| `/api/devices` | 장비별 상태 JSON |
| `/api/processes` | 모듈별 프로세스 상태 JSON |
| `/api/command` | HTTP POST 수동 명령 |
| `/api/device/{id}/command` | HTTP POST 장비 직접 명령 |
| `/api/process/{id}/{action}` | HTTP POST 프로세스 start/stop/restart/status |

## 모듈 프로세스 설정

`datas/global.json`의 `processes` 섹션에서 모듈별 실행 파일을 관리합니다.

| 항목 | 설명 |
|---|---|
| `start_scripts` | Windows는 `.bat/.cmd/.ps1/.exe`, Linux는 `.sh` 파일 경로 |
| `stop_script` | 정지용 스크립트 경로, 비워두면 Core가 추적 중인 프로세스를 종료 |
| `working_dir` | 스크립트 실행 작업 폴더, 비워두면 스크립트 폴더 사용 |
| `health_type` | `tcp`, `http`, `process`, `none` 중 하나 |
| `health_host` / `health_port` | TCP/HTTP Health Check 대상 |
| `auto_restart` | 향후 Watchdog 자동 재시작 옵션용 플래그 |

## PLC 명령 매핑

기본 `command_map`:

| COMMAND_CODE | 이름 | 동작 |
|---:|---|---|
| 1 | `TARGET_APPLY_MOVE` | `point.goto(recipe_id, point_id)` |
| 2 | `HOME` | 설정된 모터 API 명령 `home.start` |
| 3 | `ERROR_RESET` | latch fault reset |
| 4 | `CONTROLLER_RESTART` | 서비스 매니저에서 처리해야 하므로 reject |
| 5 | `RELOAD_CONFIG` | 글로벌 설정 reload |

## 데이터/설정 폴더

| 폴더 | 용도 |
|---|---|
| `datas/global.json` | 코어 글로벌 설정 |
| `datas/common` | 도면/포인트 등 모터와 비전이 같이 쓰는 데이터 |
| `datas/motor` | 모터 전용 데이터 |
| `datas/vision` | 비전 전용 데이터 |
| `settings/xgt` | XGT 서버별 설정 |
| `settings/motor` | 모터 서버별 설정 |
| `settings/vision` | 비전 서버별 설정 |
| `web` | Core HTTP 서버가 제공하는 관리 화면 |

## Discovery, 네트워크, Vision 준비

- Core는 `discovery.enabled=true`일 때 UDP `37020`에서 검색 요청을 받고 5초마다 알림을 보냅니다. 외부 PC는 같은 LAN에서 아래 JSON을 `discovery.broadcast_address:discovery.port`로 UDP 전송하고 응답을 수신하면 됩니다. Controller > Discovery에서 이름, 인터페이스, 주소, 포트, 대기 시간과 재시도를 저장할 수 있습니다. Core 웹 화면의 검색도 같은 프로토콜을 사용합니다.

```json
{"protocol":"tracker-core-discovery","version":1,"type":"discover"}
```

응답은 `type="offer"`이며 `id`, `name`, `ip`, `core_port`, `web_port`를 포함합니다. 브로드캐스트는 동일 서브넷을 대상으로 하므로 라우터를 넘어 검색하려면 별도 경로가 필요합니다.

- RDK 네트워크 탭은 NetworkManager가 관리하는 `eth0`의 IPv4 주소/게이트웨이/DNS/MTU를 조회·적용합니다. `network.apply` 후 60초 안에 새 주소에서 `network.confirm`을 호출해야 프로필에 저장됩니다. 확인하지 않으면 NetworkManager 체크포인트가 변경을 되돌립니다. Core 실행 계정에 NetworkManager 설정 변경 권한이 필요합니다.
- Vision TCP는 기존 JSON Lines의 `vision.status`를 200ms 백그라운드 조회로 사용합니다. Process Manager에서 Vision 서버 IP/TCP 포트/Web 포트를 설정합니다. Web 포트 0은 iframe 미연결입니다. Vision 앱이 준비되기 전까지 `vision.enabled=false`이며, `runtime.orchestration_enabled=false`로 PLC의 추적 시작/정지 전달도 비활성입니다. 활성화하면 PLC `TRACKING_ENABLE` 변화에 따라 `tracking.start`/`tracking.stop`을 한 번씩 요청하고 결과는 `orchestration.status`에서 확인할 수 있습니다.

## 안전 동작

PLC `FORCE_STOP=1` 또는 `RUN_ENABLE=0`이면 코어는 PTM 서비스에 `motion.stop`, Laser 서비스에 `laser.off`를 보냅니다. 안전 인증 E-Stop 대체 기능은 아닙니다.
