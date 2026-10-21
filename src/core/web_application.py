from __future__ import annotations

import asyncio
import contextlib
import copy
import http.client
import logging
from typing import Any
from urllib.parse import urlencode

from src.common.event_history import EventHistory
from src.common.alarm_history import AlarmHistory
from src.common.discovery import DiscoveryService
from src.common.settings import AppSettings, load_settings, resolve_app_path
from src.common.status import now_ms
from src.communication.json_tcp import JsonTcpResponse, send_json_request
from src.core.application import CoreApplication, _normalize_command


class CoreWebApplication(CoreApplication):
    """The management server stays available while the control worker is stopped."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._web_host = True
        self._worker_snapshot: dict[str, Any] = {}
        self._worker_sample_ms = 0
        self._worker_poll_task: asyncio.Task[None] | None = None
        self._worker_expected_stop = False
        self._worker_restart_handled: tuple[Any, ...] | None = None
        super().__init__(*args, **kwargs)
        self._vision_frame_slots = asyncio.Semaphore(4)
        for server in (self._server, self._http_server):
            if server is not None:
                server.vision_frame_handler = self._monitor_vision_frame
        self.history = EventHistory(resolve_app_path(self.settings.paths.logs_dir) / "events.jsonl")
        self.process_manager.set_diagnostic_callback(self._process_diagnostic)
        self.process_manager.set_lifecycle_callbacks(self._before_process_stop, self._after_process_start)
        self._configure_managed_launches()

    async def _monitor_vision_frame(self, query: dict[str, str]) -> tuple[str, bytes, str, dict[str, str]]:
        """Read the configured Vision preview off the control loop; never redirect."""
        web = self.settings.vision
        if not web.web_enabled or not web.web_port:
            return "503 Service Unavailable", b"Vision Web is disabled", "text/plain", {}
        after = query.get("after", "")
        if after and (not after.isascii() or not after.isdigit() or len(after) > 20):
            return "400 Bad Request", b"Invalid frame id", "text/plain", {}
        try:
            await asyncio.wait_for(self._vision_frame_slots.acquire(), 0.1)
        except asyncio.TimeoutError:
            return "503 Service Unavailable", b"Preview busy", "text/plain", {}
        try:
            # A cancelled browser request must not release its slot while the
            # bounded HTTP read still occupies a worker thread.
            task = asyncio.create_task(asyncio.to_thread(self._read_vision_frame, web, after))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
        finally:
            self._vision_frame_slots.release()

    @staticmethod
    def _read_vision_frame(web: Any, after: str) -> tuple[str, bytes, str, dict[str, str]]:
        host = str(web.web_host).strip("[]")
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        path = "/api/camera/live.jpg" + ("?" + urlencode({"after": after}) if after else "")
        connection = http.client.HTTPConnection(host, int(web.web_port), timeout=1.5)
        try:
            connection.request("GET", path, headers={"Accept": "image/jpeg"})
            response = connection.getresponse()
            headers = {}
            for name in ("X-Frame-Id", "X-Source-Width", "X-Source-Height"):
                value = response.getheader(name, "")
                if value.isascii() and value.isdigit() and len(value) <= 20:
                    headers[name] = value
            if response.status == 204:
                return "204 No Content", b"", "image/jpeg", headers
            if response.status != 200:
                status = response.status if response.status in {409, 503} else 502
                return f"{status} {'Service Unavailable' if status == 503 else 'Bad Gateway' if status == 502 else 'Conflict'}", b"Vision preview unavailable", "text/plain", {}
            content = response.read(4 * 1024 * 1024 + 1)
            if len(content) > 4 * 1024 * 1024 or response.getheader("Content-Type", "").split(";")[0] != "image/jpeg":
                return "502 Bad Gateway", b"Invalid Vision JPEG response", "text/plain", {}
            return "200 OK", content, "image/jpeg", headers
        except (OSError, http.client.HTTPException):
            return "502 Bad Gateway", b"Vision preview connection failed", "text/plain", {}
        finally:
            connection.close()

    def _process_diagnostic(self, event: dict[str, Any]) -> None:
        self.history.add(str(event.get("process_name", "process")), str(event.get("level", "ERROR")),
                         str(event.get("message", "")), event=str(event.get("event", "process")),
                         details=event.get("details"), timestamp_ms=event.get("timestamp_ms"))

    async def _before_process_stop(self, process_name: str) -> None:
        if process_name == self.settings.processes["core"]["process_name"]:
            self._worker_expected_stop = True
            self._worker_snapshot = {}
            self._worker_sample_ms = 0
            return
        dependent = {self.settings.processes[key]["process_name"] for key in ("plc_gateway", "ptm")}
        if process_name in dependent and self._worker_sample_ms:
            response = await self._worker_request("runtime.prepare_stop", {"process_name": process_name})
            if not response.ok:
                self.history.add(process_name, "ERROR", f"Could not prepare worker for stop: {response.error}", event="safe_stop")
        module = self.settings.processes["ptm"]
        if process_name == module["process_name"]:
            # PTM owns the laser output even when the control worker is stopped or bypassed.
            runtime = self.process_manager.snapshot().get(process_name, {}).get("runtime_endpoint") or {}
            response = await send_json_request(
                str(runtime.get("host", module["health_host"])),
                int(runtime.get("port", module["health_port"])),
                "laser.off", timeout_s=2.0,
            )
            if not response.ok:
                self.history.add(process_name, "ERROR", f"Could not switch laser off before PTM stop: {response.error}", event="safe_stop")

    async def _after_process_start(self, process_name: str) -> None:
        if process_name == self.settings.processes["core"]["process_name"]:
            self._worker_expected_stop = False
        if process_name == self.settings.processes["plc_gateway"]["process_name"]:
            await self._worker_request("runtime.resume_process", {})

    def _configure_managed_launches(self) -> None:
        endpoints = {}
        for key in ("core", "plc_gateway", "ptm"):
            module = self.settings.processes.get(key, {})
            name = str(module.get("process_name", ""))
            if name not in self.process_manager.modules:
                continue
            endpoints[name] = {
                "tcp_host": module["health_host"], "tcp_port": int(module["health_port"]),
                "http_host": module.get("http_host", module["health_host"]),
                "http_port": int(module.get("http_port", 0)),
                "http_enabled": key != "core" and (self.settings.xgt.web.enabled if key == "plc_gateway" else self.settings.motor.web_enabled),
            }
            if key == "core":
                self.process_manager.modules[name]["launch_args"] = [
                    "--worker", "--config", str(self.settings_path.resolve()),
                ]
        self.process_manager.update_launch_endpoints(endpoints)

    async def _replace_settings_locked(self, settings: AppSettings) -> None:
        restart_discovery = settings.discovery != self.settings.discovery
        if restart_discovery and self._started:
            await self.discovery.stop()
        self.settings = settings
        self._server.static_root = resolve_app_path(settings.paths.web_dir)
        self._server.allow_remote_process_control = settings.core.allow_remote_process_control
        if self._http_server is not None:
            self._http_server.static_root = self._server.static_root
            self._http_server.allow_remote_process_control = settings.core.allow_remote_process_control
        self.process_manager.update_modules(settings.processes)
        self.process_manager.update_communication_endpoints(self._process_communication_endpoints())
        self._configure_managed_launches()
        if self._started:
            self._configure_logging()
            await self.process_manager.reconcile(start_missing=False)
        if restart_discovery:
            self.discovery = DiscoveryService(settings.discovery, self._server.port,
                                              self.discovery.web_port)
            if self._started:
                await self.discovery.start()

    async def start(self) -> None:
        self._validate_operator_settings(self.settings)
        self._configure_logging()
        logging.getLogger().addHandler(self.history)
        await self._start_servers()
        await self.discovery.start()
        self._started = True
        await self.process_manager.reconcile(start_order=[
            self.settings.processes[key]["process_name"]
            for key in ("plc_gateway", "ptm", "vision", "core")
        ])
        self._worker_poll_task = asyncio.create_task(self._poll_worker(), name="core-worker-status")
        self.process_manager.start_watchdog()
        self.history.add("web", "INFO", "Core management server started", event="started")

    async def stop(self) -> None:
        self._started = False
        self._stop_event.set()
        if self._worker_poll_task is not None:
            self._worker_poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_poll_task
        await self._stop_servers()
        await self.process_manager.shutdown()
        await self.discovery.stop()
        await self.network_manager.cancel()
        self.plc_memory.close()
        self.history.add("web", "INFO", "Core management server stopped", event="stopped")
        logging.getLogger().removeHandler(self.history)
        self.history.close()
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler.close()
            self._log_handler = None

    async def _worker_request(self, command: str, params: dict[str, Any]) -> JsonTcpResponse:
        module = self.settings.processes["core"]
        if not module.get("enabled", True):
            return JsonTcpResponse(False, error={"code": "CORE_STOPPED", "message": "Main Core is disabled"})
        try:
            runtime = self.process_manager.snapshot().get(str(module["process_name"]), {}).get("runtime_endpoint") or {}
            host = runtime.get("host", module["health_host"])
            port = runtime.get("port", module["health_port"])
            return await send_json_request(str(host), int(port),
                                           command, params, timeout_s=3.0)
        except Exception as exc:
            return JsonTcpResponse(False, error={"code": "CORE_UNAVAILABLE", "message": str(exc)})

    async def _poll_worker(self) -> None:
        previous_error = ""
        previous_level = ""
        while not self._stop_event.is_set():
            response = await self._worker_request("runtime.snapshot", {})
            if response.ok and isinstance(response.result, dict):
                self._worker_snapshot = response.result
                self._worker_sample_ms = now_ms()
                previous_error = ""
                previous_level = ""
                request = response.result.get("runtime", {}).get("restart_request")
                if isinstance(request, dict) and request.get("ready") is True:
                    await self._restart_requested_worker(request)
            else:
                error = str(response.error)
                self._worker_snapshot = {}
                self._worker_sample_ms = 0
                expected_stop = self._worker_expected_stop or not self.settings.processes["core"].get("enabled", True)
                level = "INFO" if expected_stop else "WARNING"
                if error != previous_error or level != previous_level:
                    self.history.add("core_runtime", level, error, event="communication")
                    previous_error = error
                    previous_level = level
            await asyncio.sleep(0.3)

    async def _restart_requested_worker(self, request: dict[str, Any]) -> None:
        requested_ms = request.get("requested_ms")
        seq = request.get("plc_command_seq")
        if type(requested_ms) is not int or (seq is not None and (type(seq) is not int or not 0 <= seq <= 65535)):
            return
        async with self._settings_lock:
            name = self.settings.processes["core"]["process_name"]
            token = (name, requested_ms, seq)
            if token == self._worker_restart_handled:
                return
            self._worker_restart_handled = token
            launch = self.process_manager.modules.get(name)
            previous_args = list(launch.get("launch_args", [])) if launch else []
            try:
                if launch is None:
                    raise RuntimeError("Main Core is not configured in Process Manager")
                if seq is not None:
                    launch["launch_args"] = [*previous_args, "--plc-restart-seq", str(seq)]
                await self.process_manager.command("restart", name)
                self.history.add(name, "INFO", "Main Core restarted on request", event="controller_restart")
            except Exception as exc:
                self.history.add(name, "ERROR", str(exc), event="controller_restart")
                await self._worker_request("controller.restart_failed", {"requested_ms": requested_ms, "message": str(exc)})
            finally:
                if launch is not None and self.process_manager.modules.get(name) is launch:
                    launch["launch_args"] = previous_args

    def status_snapshot(self, processes: dict[str, Any] | None = None) -> dict[str, Any]:
        status = super().status_snapshot(processes, include_shared_memory=False)
        fresh = bool(self._worker_sample_ms and now_ms() - self._worker_sample_ms < 2000)
        if fresh:
            status.update(copy.deepcopy(self._worker_snapshot))
        else:
            status["core"]["running"] = False
            status["runtime"]["loop_running"] = False
            status["runtime"]["system_ready"] = False
            module = self.settings.processes["core"]
            status["core"]["tcp"] = {"host": module["health_host"], "port": module["health_port"]}
        status["processes"] = processes if processes is not None else self.process_manager.snapshot()
        status["web_server"] = {"running": self._started, "host": self._server.host,
                                "port": self.discovery.web_port, "primary_port": self._server.port,
                                "configured_http_port": self.settings.core.http_port,
                                "error": self._http_server_error}
        status["core"]["status_age_ms"] = now_ms() - self._worker_sample_ms if self._worker_sample_ms else None
        return status

    async def handle_tcp_command(self, command: str, params: dict[str, Any]) -> JsonTcpResponse:
        command = _normalize_command(command)
        try:
            if command == "monitor.capabilities":
                return JsonTcpResponse(True, self.monitor_capabilities())
            if command == "monitor.config.apply":
                response = await self._worker_request(command, params)
                if response.ok:
                    # The worker guarded and persisted the shared settings file.
                    # Refresh management-owned settings without a second mutation.
                    await self._replace_settings(load_settings(self.settings_path))
                return response
            if command == "monitor.snapshot":
                status = self.status_snapshot()
                fresh = bool(self._worker_sample_ms and now_ms() - self._worker_sample_ms < 2000)
                if not fresh:
                    status["monitor"] = {"schema_version": 1, "state_age_ms": None,
                                         "endpoints": self.monitor_capabilities()["endpoints"]}
                    status["plc_memory"] = {"connected": False, "error": "Main Core state unavailable"}
                    status["alarm_history"] = {"schema_version": 1, "entries": [], "total": 0,
                                               "recording": {"running": False, "last_error": "Main Core state unavailable"}}
                return JsonTcpResponse(True, status)
            if command in {"alarms.history", "alarms.export"}:
                response = await self._worker_request(command, params)
                if response.ok:
                    return response
                result = await asyncio.to_thread(AlarmHistory.read_database, self.alarm_history.path, params)
                return JsonTcpResponse(True, {"schema_version": 1, **result,
                                              "recording": {"running": False, "last_error": response.error,
                                                            "retention_days": 30}})
            if command == "alarms.active":
                status = self.status_snapshot()
                if self._worker_sample_ms and now_ms() - self._worker_sample_ms < 2000:
                    return JsonTcpResponse(True, status.get("alarm_history", {"entries": [], "total": 0}))
                return JsonTcpResponse(False, error={"code": "CORE_UNAVAILABLE", "message": "Current alarms require a fresh Core snapshot"})
            if command in {"logs.history", "logs.export"}:
                result = self.history.query(limit=5000 if command == "logs.export" else int(params.get("limit", 500)),
                                            level=str(params.get("level", "")), source=str(params.get("source", "")))
                return JsonTcpResponse(True, {"schema_version": 1, **result})
            if command == "logs.import":
                return JsonTcpResponse(True, {"imported": self.history.import_entries(params.get("entries"))})
            if command == "get_devices":
                return JsonTcpResponse(True, self.status_snapshot().get("devices", {}))
            if command in {"update_config", "reload_config"}:
                response = await super().handle_tcp_command(command, params)
                if response.ok:
                    reload_response = await self._worker_request("reload_config", {})
                    if not reload_response.ok and self._worker_sample_ms:
                        self.history.add("core_runtime", "WARNING", "Configuration saved; restart Main Core to apply", event="config")
                return response
            if command.startswith("process.") and command not in {"process.broadcast"}:
                module_id = params.get("module")
                if module_id and not params.get("process_name"):
                    key = "plc_gateway" if module_id == "plc" else str(module_id)
                    params = {**params, "process_name": self.settings.processes.get(key, {}).get("process_name", "")}
                return await super().handle_tcp_command(command, params)
            local = {"ping", "get_status", "get_processes", "get_config", "broadcast", "broadcast.ping",
                     "process.broadcast", "xgt.web_status", "ptm.web_status", "vision.web_status"}
            if command in local or command.startswith(("network.", "discovery.")):
                return await super().handle_tcp_command(command, params)
            if command == "runtime.snapshot":
                return JsonTcpResponse(True, self.status_snapshot())
            return await self._worker_request(command, params)
        except Exception as exc:
            self.history.add("web", "ERROR", str(exc), event=command)
            return JsonTcpResponse(False, error={"code": "BAD_REQUEST", "message": str(exc)})
