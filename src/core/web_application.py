from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from typing import Any

from src.common.event_history import EventHistory
from src.common.discovery import DiscoveryService
from src.common.settings import AppSettings, resolve_app_path
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
        super().__init__(*args, **kwargs)
        self.history = EventHistory(resolve_app_path(self.settings.paths.logs_dir) / "events.jsonl")
        self.process_manager.set_diagnostic_callback(self._process_diagnostic)
        self.process_manager.set_lifecycle_callbacks(self._before_process_stop, self._after_process_start)
        self._configure_managed_launches()

    def _process_diagnostic(self, event: dict[str, Any]) -> None:
        self.history.add(str(event.get("process_name", "process")), str(event.get("level", "ERROR")),
                         str(event.get("message", "")), event=str(event.get("event", "process")),
                         details=event.get("details"), timestamp_ms=event.get("timestamp_ms"))

    async def _before_process_stop(self, process_name: str) -> None:
        if process_name == self.settings.processes["core"]["process_name"]:
            self._worker_snapshot = {}
            self._worker_sample_ms = 0
            return
        dependent = {self.settings.processes[key]["process_name"] for key in ("plc_gateway", "ptm")}
        if process_name in dependent and self._worker_sample_ms:
            response = await self._worker_request("runtime.prepare_stop", {"process_name": process_name})
            if not response.ok:
                self.history.add(process_name, "ERROR", f"Could not prepare worker for stop: {response.error}", event="safe_stop")

    async def _after_process_start(self, process_name: str) -> None:
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
            self.discovery = DiscoveryService(settings.discovery, settings.core.port,
                                              settings.core.http_port or settings.core.port)
            if self._started:
                await self.discovery.start()

    async def start(self) -> None:
        self._validate_operator_settings(self.settings)
        self._configure_logging()
        logging.getLogger().addHandler(self.history)
        await self._server.start()
        self._server_task = asyncio.create_task(self._server.serve_forever())
        if self._http_server is not None:
            await self._http_server.start()
            self._http_server_task = asyncio.create_task(self._http_server.serve_forever())
        await self.discovery.start()
        self._started = True
        self._worker_poll_task = asyncio.create_task(self._poll_worker(), name="core-worker-status")
        await self.process_manager.reconcile()
        self.process_manager.start_watchdog()
        self.history.add("web", "INFO", "Core management server started", event="started")

    async def stop(self) -> None:
        self._started = False
        self._stop_event.set()
        if self._worker_poll_task is not None:
            self._worker_poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_poll_task
        await self.process_manager.shutdown()
        await self.discovery.stop()
        await self.network_manager.cancel()
        for task in (self._server_task, self._http_server_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._server.stop()
        if self._http_server is not None:
            await self._http_server.stop()
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
        while not self._stop_event.is_set():
            response = await self._worker_request("runtime.snapshot", {})
            if response.ok and isinstance(response.result, dict):
                self._worker_snapshot = response.result
                self._worker_sample_ms = now_ms()
                previous_error = ""
            else:
                error = str(response.error)
                self._worker_snapshot = {}
                self._worker_sample_ms = 0
                if error != previous_error:
                    self.history.add("core_runtime", "WARNING", error, event="communication")
                    previous_error = error
            await asyncio.sleep(0.3)

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
                                "port": self.settings.core.http_port or self._server.port}
        status["core"]["status_age_ms"] = now_ms() - self._worker_sample_ms if self._worker_sample_ms else None
        return status

    async def handle_tcp_command(self, command: str, params: dict[str, Any]) -> JsonTcpResponse:
        command = _normalize_command(command)
        try:
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
