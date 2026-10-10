from __future__ import annotations

import copy
import json
import os
from dataclasses import MISSING, asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT_DIR / "datas" / "global.json"


@dataclass
class CoreServerSettings:
    host: str = "0.0.0.0"
    port: int = 8000
    http_port: int | None = None
    cycle_interval_ms: int = 50
    status_publish_interval_ms: int = 200
    allow_remote_process_control: bool = False


@dataclass
class DiscoverySettings:
    enabled: bool = True
    method: str = "udp_broadcast"
    interface: str = "eth0"
    broadcast_address: str = "255.255.255.255"
    port: int = 37020
    timeout_ms: int = 1000
    retries: int = 2
    name: str = "Tracker Core"
    controller_id: str = ""


@dataclass
class LoggingSettings:
    level: str = "INFO"
    file_enabled: bool = True
    max_file_mb: int = 20
    backup_count: int = 5


@dataclass
class SystemSettings:
    memory_warn_percent: int = 90
    disk_warn_percent: int = 90


@dataclass
class TcpEndpointSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    timeout_s: float = 2.0
    enabled: bool = True


@dataclass
class SharedMemorySettings:
    name: str = "xgt_gateway_v1"
    total_size: int = 512
    read_offset: int = 64
    read_words: int = 100
    write_offset: int = 264
    write_words: int = 100
    configure_gateway_on_start: bool = False


@dataclass
class PlcAreaSettings:
    read_address: str = "D0"
    write_address: str = "D100"
    interval_ms: int = 50


@dataclass
class XgtSettings:
    control: TcpEndpointSettings = field(
        default_factory=lambda: TcpEndpointSettings(port=15150)
    )
    web: TcpEndpointSettings = field(
        default_factory=lambda: TcpEndpointSettings(port=5051)
    )
    shared_memory: SharedMemorySettings = field(default_factory=SharedMemorySettings)
    plc_area: PlcAreaSettings = field(default_factory=PlcAreaSettings)


@dataclass
class MotorSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    web_enabled: bool = True
    timeout_s: float = 3.0
    enabled: bool = True
    status_command: str = "system.status"


@dataclass
class VisionSettings:
    host: str = "127.0.0.1"
    port: int = 8768
    web_host: str = "127.0.0.1"
    web_port: int = 0
    web_enabled: bool = False
    timeout_s: float = 1.0
    enabled: bool = False
    camera_count: int = 1
    status_command: str = "vision.status"
    result_command: str = "vision.result"


@dataclass
class LaserSettings:
    host: str = "127.0.0.1"
    port: int = 8768
    timeout_s: float = 2.0
    enabled: bool = True
    mode: str = "ptm"
    status_command: str = "laser.status"
    on_command: str = "laser.on"
    off_command: str = "laser.off"


@dataclass
class RuntimeSettings:
    orchestration_enabled: bool = True
    vision_status_max_age_ms: int = 1500
    plc_heartbeat_warn_ms: int = 1000
    plc_heartbeat_fault_ms: int = 3000
    position_stable_ms: int = 300
    tracking_tolerance_default_mm_x100: int = 100
    laser_tolerance_default_deg_x100: int = 50
    command_timeout_s: float = 10.0
    fault_latch_enabled: bool = True


@dataclass
class DataPathSettings:
    common_dir: str = "datas/common"
    motor_dir: str = "datas/motor"
    vision_dir: str = "datas/vision"
    xgt_settings_dir: str = "settings/xgt"
    motor_settings_dir: str = "settings/motor"
    vision_settings_dir: str = "settings/vision"
    logs_dir: str = "logs"
    web_dir: str = "web"


def _default_process_modules() -> dict[str, dict[str, Any]]:
    return {
        "core": {
            "name": "Main Core", "enabled": True,
            "process_name": "core_runtime", "start_scripts": "main.py",
            "stop_script": "", "working_dir": ".",
            "health_type": "tcp", "health_host": "127.0.0.1",
            "health_port": 8770, "auto_restart": False,
        },
        "plc_gateway": {
            "name": "PLC Communication",
            "enabled": True,
            "process_name": "xgt_gateway",
            "start_scripts": "",
            "stop_script": "",
            "working_dir": "",
            "health_type": "tcp",
            "health_host": "127.0.0.1",
            "health_port": 15150,
            "auto_restart": False,
        },
        "ptm": {
            "name": "PTM / Laser Server",
            "enabled": True,
            "process_name": "ptm_server",
            "start_scripts": "",
            "stop_script": "",
            "working_dir": "",
            "health_type": "tcp",
            "health_host": "127.0.0.1",
            "health_port": 8765,
            "auto_restart": False,
        },
        "vision": {
            "name": "Vision Server",
            "enabled": False,
            "process_name": "vision_server",
            "start_scripts": "",
            "stop_script": "",
            "working_dir": "",
            "health_type": "tcp",
            "health_host": "127.0.0.1",
            "health_port": 8768,
            "auto_restart": False,
        },
    }


def _default_command_map() -> dict[str, dict[str, Any]]:
    return {
        "1": {
            "name": "TARGET_APPLY_MOVE",
            "target": "motor",
            "command": "point.goto",
            "params": {
                "recipe_id": "$req_recipe_id",
                "point_id": "$req_point_id",
            },
        },
        "2": {
            "name": "HOME",
            "target": "motor",
            "command": "home.start",
            "params": {},
        },
        "3": {
            "name": "ERROR_RESET",
            "target": "core",
            "command": "alarm.reset",
            "params": {},
        },
        "4": {
            "name": "CONTROLLER_RESTART",
            "target": "core",
            "command": "controller.restart_request",
            "params": {},
        },
    }


@dataclass
class AppSettings:
    core: CoreServerSettings = field(default_factory=CoreServerSettings)
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    system: SystemSettings = field(default_factory=SystemSettings)
    xgt: XgtSettings = field(default_factory=XgtSettings)
    motor: MotorSettings = field(default_factory=MotorSettings)
    laser: LaserSettings = field(default_factory=LaserSettings)
    vision: VisionSettings = field(default_factory=VisionSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)
    paths: DataPathSettings = field(default_factory=DataPathSettings)
    processes: dict[str, dict[str, Any]] = field(default_factory=_default_process_modules)
    command_map: dict[str, dict[str, Any]] = field(default_factory=_default_command_map)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce_dataclass(cls: type[Any], data: dict[str, Any]) -> Any:
    field_defs = getattr(cls, "__dataclass_fields__", {})
    kwargs: dict[str, Any] = {}
    for name, field_def in field_defs.items():
        value = data.get(name)
        default_factory = getattr(field_def, "default_factory", MISSING)
        has_factory = default_factory is not MISSING
        if value is None:
            if has_factory:
                kwargs[name] = default_factory()
            elif field_def.default is not MISSING:
                kwargs[name] = field_def.default
            else:
                kwargs[name] = None
            continue
        target_type = field_def.type
        if hasattr(target_type, "__dataclass_fields__") and isinstance(value, dict):
            kwargs[name] = _coerce_dataclass(target_type, value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def settings_from_dict(data: dict[str, Any]) -> AppSettings:
    default_data = AppSettings().to_dict()
    merged = _deep_merge(default_data, data)
    merged["processes"].pop("laser", None)
    # Process Manager owns managed listeners; retain the existing client aliases.
    for key, tcp, web, host_key, port_key in (
        ("plc_gateway", merged["xgt"]["control"], merged["xgt"]["web"], "host", "port"),
        ("ptm", merged["motor"], merged["motor"], "web_host", "web_port"),
    ):
        process = merged["processes"][key]
        supplied = data.get("processes", {}).get(key, {})
        process["health_host"] = supplied.get("health_host", tcp["host"])
        process["health_port"] = supplied.get("health_port", tcp["port"])
        process["http_host"] = supplied.get("http_host", web[host_key])
        process["http_port"] = supplied.get("http_port", web[port_key])
        tcp["host"], tcp["port"] = process["health_host"], int(process["health_port"])
        web[host_key], web[port_key] = process["http_host"], int(process["http_port"])
    merged["vision"]["camera_count"] = 1
    return AppSettings(
        core=_coerce_dataclass(CoreServerSettings, merged["core"]),
        discovery=_coerce_dataclass(DiscoverySettings, merged["discovery"]),
        logging=_coerce_dataclass(LoggingSettings, merged["logging"]),
        system=_coerce_dataclass(SystemSettings, merged["system"]),
        xgt=XgtSettings(
            control=_coerce_dataclass(TcpEndpointSettings, merged["xgt"]["control"]),
            web=_coerce_dataclass(TcpEndpointSettings, merged["xgt"]["web"]),
            shared_memory=_coerce_dataclass(
                SharedMemorySettings, merged["xgt"]["shared_memory"]
            ),
            plc_area=_coerce_dataclass(PlcAreaSettings, merged["xgt"]["plc_area"]),
        ),
        motor=_coerce_dataclass(MotorSettings, merged["motor"]),
        laser=_coerce_dataclass(LaserSettings, merged["laser"]),
        vision=_coerce_dataclass(VisionSettings, merged["vision"]),
        runtime=_coerce_dataclass(RuntimeSettings, merged["runtime"]),
        paths=_coerce_dataclass(DataPathSettings, merged["paths"]),
        processes=merged["processes"],
        command_map=merged["command_map"],
    )


def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> AppSettings:
    path = resolve_app_path(path)
    if not path.exists() or path.stat().st_size == 0:
        return AppSettings()
    with path.open("r", encoding="utf-8-sig") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"Settings root must be an object: {path}")
    return settings_from_dict(data)


def save_settings(settings: AppSettings, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path = resolve_app_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as fp:
        json.dump(settings.to_dict(), fp, indent=2, ensure_ascii=False)
        fp.write("\n")
    os.replace(tmp_path, path)


def apply_settings_patch(settings: AppSettings, patch: dict[str, Any]) -> AppSettings:
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    merged = _deep_merge(settings.to_dict(), patch)
    # Older clients can still patch endpoint aliases; explicit process fields win.
    mappings = (
        ("plc_gateway", patch.get("xgt", {}).get("control", {}), {"host": "health_host", "port": "health_port"}),
        ("plc_gateway", patch.get("xgt", {}).get("web", {}), {"host": "http_host", "port": "http_port"}),
        ("ptm", patch.get("motor", {}), {"host": "health_host", "port": "health_port", "web_host": "http_host", "web_port": "http_port"}),
    )
    for key, values, fields in mappings:
        explicit = patch.get("processes", {}).get(key, {})
        for alias, field_name in fields.items():
            if alias in values and field_name not in explicit:
                merged["processes"][key][field_name] = values[alias]
    return settings_from_dict(merged)


def ensure_data_directories(settings: AppSettings, root: Path = ROOT_DIR) -> None:
    for value in asdict(settings.paths).values():
        path = resolve_app_path(value, root)
        path.mkdir(parents=True, exist_ok=True)


def resolve_app_path(value: str | Path, root: Path = ROOT_DIR) -> Path:
    text = os.path.expandvars(os.path.expanduser(str(value)))
    path = Path(text)
    if not path.is_absolute():
        path = root / path
    return path
