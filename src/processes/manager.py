from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from src.common.network import client_connect_host
from src.common.settings import ROOT_DIR, resolve_app_path
from src.common.status import now_ms
from src.communication.json_tcp import send_json_request


class ProcessManager:
    """Start/stop/restart configured processes using process_name only.

    ``process_name`` is the logical control key used by Tracker Core.
    The operating-system process name (for example ``python.exe``) is not used
    as a control key.

    Runtime ownership is persisted in a small PID-state file containing the
    actual service PID and a process fingerprint.  This allows Tracker Core to
    recover process ownership after a Core restart without killing unrelated
    Python processes.
    """

    _STATE_VERSION = 1
    _DISCOVERY_TIMEOUT_S = 60.0
    _DISCOVERY_INTERVAL_S = 0.10
    _VALIDATION_CACHE_MS = 2_000
    _WATCHDOG_INTERVAL_S = 1.0
    _WATCHDOG_FAILURE_LIMIT = 3

    def __init__(self, modules: dict[str, dict[str, Any]]) -> None:
        # Configurations are always indexed by the logical process_name.
        self.modules: dict[str, dict[str, Any]] = {}

        # Only subprocesses created by this ProcessManager instance live here.
        # The actual service PID is persisted separately in runtime state files.
        self._processes: dict[str, asyncio.subprocess.Process] = {}

        self._last_actions: dict[str, dict[str, Any]] = {}

        # Cached validated runtime records.  The cache avoids spawning
        # PowerShell on every status poll on Windows.
        self._runtime_cache: dict[str, dict[str, Any]] = {}
        self._runtime_validation_ms: dict[str, int] = {}
        self._session_owned: set[str] = set()
        self._operation_lock = asyncio.Lock()
        self._watchdog_failures: dict[str, int] = {}
        self._watchdog_state: dict[str, dict[str, Any]] = {}
        self._watchdog_task: asyncio.Task[None] | None = None
        self._communication_configs: dict[str, dict[str, Any]] = {}
        self._communication_state: dict[str, dict[str, Any]] = {}
        self._ping_sequences: dict[str, int] = {}
        self._shutting_down = False
        self._windows_job: int | None = None
        self._retired_process_names: set[str] = set()

        self._runtime_dir = Path(ROOT_DIR) / "runtime" / "processes"
        self._runtime_dir.mkdir(parents=True, exist_ok=True)

        self.update_modules(modules)
        if os.name == "nt":
            self._windows_job = self._create_windows_job()

    def update_modules(self, modules: dict[str, dict[str, Any]]) -> None:
        # Re-index settings.processes by process_name.  Existing runtime PID
        # files are intentionally left untouched so a Core/config reload does
        # not lose ownership of a process that is still running.
        updated = self._index_modules_by_process_name(modules)
        self._retired_process_names.update(set(self.modules) - set(updated))
        self._retired_process_names.difference_update(updated)
        self.modules = updated
        for process_name, config in updated.items():
            if not config.get("enabled", True):
                self._communication_state.pop(process_name, None)
                self._watchdog_failures.pop(process_name, None)
                self._watchdog_state.pop(process_name, None)

    def update_communication_endpoints(
        self,
        endpoints: dict[str, dict[str, Any]],
    ) -> None:
        self._communication_configs = {
            str(process_name): dict(config)
            for process_name, config in endpoints.items()
            if process_name in self.modules and isinstance(config, dict)
        }

    async def status_all(self) -> dict[str, Any]:
        process_names = list(self.modules)
        values = await asyncio.gather(*(self.status(name) for name in process_names))
        return dict(zip(process_names, values))

    def snapshot(self) -> dict[str, Any]:
        # snapshot() is synchronous because Core uses it in status_snapshot().
        # Use the same persisted ownership information, but avoid endpoint
        # health I/O here.  Full health checks are provided by status().
        return {
            process_name: self._compose_status(
                self._snapshot_one(
                    process_name,
                    config,
                    self._valid_runtime_record_sync(process_name),
                ),
                self._communication_state.get(process_name),
            )
            for process_name, config in self.modules.items()
        }

    async def status(self, process_name: str) -> dict[str, Any]:
        config = self._module(process_name)

        if not config.get("enabled", True):
            runtime = await asyncio.to_thread(
                self._valid_runtime_record_sync,
                process_name,
                True,
            )
            item = self._snapshot_one(process_name, config, runtime)
            item["health"] = {
                "type": "process",
                "online": False,
                "message": "service is disabled",
            }
            communication = self._communication_result(
                process_name,
                success=False,
                error="service is disabled",
                update_failure=False,
            )
            return self._compose_status(item, communication, state="disabled")

        runtime = await asyncio.to_thread(
            self._valid_runtime_record_sync,
            process_name,
            True,
        )

        item = self._snapshot_one(process_name, config, runtime)
        running = runtime is not None
        item["health"] = {
            "type": "process",
            "online": running,
            "message": "process is alive" if running else "process is dead",
        }
        if not running:
            communication = await self._probe_communication(process_name, config)
            if communication.get("stale"):
                communication = self._communication_state.get(process_name, communication)
            if communication.get("online"):
                item["running"] = True
                item["managed"] = False
                item["health"] = {
                    "type": "process",
                    "online": True,
                    "message": "service endpoint is online (unmanaged)",
                }
                return self._compose_status(item, communication, state="online")
            return self._compose_status(item, communication, state="offline")

        communication = await self._probe_communication(process_name, config)
        if communication.get("stale"):
            communication = self._communication_state.get(process_name, communication)
        item["recovered"] = bool(
            runtime is not None and runtime.get("_source") == "pid_file"
        )

        if runtime is not None:
            item["pid"] = int(runtime["pid"])
            item["wrapper_pid"] = _optional_int(runtime.get("wrapper_pid"))

        return self._compose_status(item, communication)

    async def start(self, process_name: str) -> dict[str, Any]:
        async with self._operation_lock:
            return await self._start(process_name)

    async def _start(self, process_name: str) -> dict[str, Any]:
        config = self._module(process_name)

        if not config.get("enabled", True):
            raise ValueError(f"process is disabled: {process_name}")

        # A valid PID-state file means this process is already owned by this
        # process_name, even if Core itself has been restarted.
        runtime = await asyncio.to_thread(
            self._valid_runtime_record_sync,
            process_name,
            True,
        )
        if runtime is not None:
            if os.name == "nt":
                self._assign_windows_job(int(runtime["pid"]))
            return await self.status(process_name)

        # If the configured endpoint is already alive but we have no validated
        # ownership record, do not start another copy and do not adopt/kill it.
        # This protects unrelated processes that happen to use the same port.
        health = await self._check_health(config)
        if health.get("online", False):
            raise RuntimeError(
                f"endpoint is already online but ownership is not verified: "
                f"{process_name} ({health.get('endpoint', 'unknown endpoint')})"
            )

        script = self._script_path(self._script_setting(config, "start"))
        if script is None:
            raise ValueError(f"start_scripts is empty: {process_name}")

        self._validate_script(script)
        working_dir = self._working_dir(config, script)
        args = self._start_command(script)

        kwargs: dict[str, Any] = {
            "cwd": str(working_dir),
            "stdout": asyncio.subprocess.DEVNULL,
            "stderr": asyncio.subprocess.DEVNULL,
            "env": {**os.environ, "PYTHON": sys.executable},
        }

        if os.name == "nt":
            creationflags = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            kwargs["creationflags"] = creationflags
        else:
            kwargs["start_new_session"] = True

        wrapper = await asyncio.create_subprocess_exec(*args, **kwargs)
        if os.name == "nt":
            try:
                self._assign_windows_job(wrapper.pid)
            except Exception:
                with contextlib.suppress(Exception):
                    await self._terminate_process(wrapper)
                raise
        self._processes[process_name] = wrapper

        try:
            runtime = await self._discover_runtime_process(
                process_name=process_name,
                config=config,
                wrapper_pid=wrapper.pid,
                script=script,
            )
        except Exception:
            # Start failed before ownership could be established.  Terminate
            # only the wrapper we created; never guess by python.exe name.
            with contextlib.suppress(Exception):
                await self._terminate_process(wrapper)
            self._processes.pop(process_name, None)
            raise

        if runtime is None:
            # If a BAT/CMD uses `start` or otherwise detaches a child without a
            # health_port, the real service may no longer be discoverable
            # safely.  Refuse to claim ownership rather than store a wrong PID.
            with contextlib.suppress(Exception):
                if wrapper.returncode is None:
                    await self._terminate_process(wrapper)
            self._processes.pop(process_name, None)
            raise RuntimeError(
                f"could not identify the actual service PID for {process_name}; "
                "configure health_port or avoid detached start commands"
            )

        self._save_runtime_record(process_name, runtime, source="session")

        self._last_actions[process_name] = {
            "action": "start",
            "ok": True,
            "timestamp_ms": now_ms(),
            "message": (
                f"started {process_name} "
                f"(pid {runtime['pid']}, wrapper {wrapper.pid})"
            ),
        }

        return await self.status(process_name)

    async def stop(self, process_name: str) -> dict[str, Any]:
        async with self._operation_lock:
            return await self._stop(process_name)

    async def _stop(self, process_name: str) -> dict[str, Any]:
        config = self._module(process_name)

        # Preserve existing behavior: if a configured stop_script exists, give
        # it the first chance to stop the service gracefully.
        stop_script = self._script_path(self._script_setting(config, "stop"))
        if stop_script is not None:
            self._validate_script(stop_script)
            await self._run_script_once(
                stop_script,
                self._working_dir(config, stop_script),
            )

        runtime = await asyncio.to_thread(
            self._valid_runtime_record_sync,
            process_name,
            True,  # force re-validation before destructive control
        )

        if runtime is not None:
            # The PID fingerprint was validated immediately before this call.
            # Terminate only that exact process tree.
            await self._terminate_runtime_pid(runtime)

        await self._cleanup_session_wrapper(process_name)

        # Re-check before deleting the state file.  A normal exit should make
        # this return None and automatically remove stale state.
        remaining = await asyncio.to_thread(
            self._valid_runtime_record_sync,
            process_name,
            True,
        )
        if remaining is None:
            self._remove_runtime_record(process_name)

        self._last_actions[process_name] = {
            "action": "stop",
            "ok": remaining is None,
            "timestamp_ms": now_ms(),
            "message": (
                f"stopped {process_name}"
                if remaining is None
                else f"stop requested but process is still running: {process_name}"
            ),
        }

        if remaining is not None:
            raise RuntimeError(
                f"process did not stop: {process_name} (pid {remaining['pid']})"
            )

        return await self.status(process_name)

    async def restart(self, process_name: str) -> dict[str, Any]:
        async with self._operation_lock:
            await self._stop(process_name)
            return await self._start(process_name)

    async def start_enabled(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for process_name, config in self.modules.items():
            if not config.get("enabled", True):
                continue
            if not str(self._script_setting(config, "start") or "").strip():
                continue
            try:
                results[process_name] = await self.start(process_name)
            except Exception as exc:
                self._last_actions[process_name] = {
                    "action": "auto_start",
                    "ok": False,
                    "timestamp_ms": now_ms(),
                    "message": str(exc),
                }
                results[process_name] = {"error": str(exc)}
        return results

    async def reconcile(self, *, start_missing: bool = True) -> dict[str, Any]:
        """Apply enabled state after a config reload."""
        results: dict[str, Any] = {}
        for process_name in sorted(self._retired_process_names):
            runtime = await asyncio.to_thread(
                self._valid_runtime_record_sync,
                process_name,
                True,
            )
            try:
                if runtime is not None:
                    await self._terminate_runtime_pid(runtime)
                await self._cleanup_session_wrapper(process_name)
                self._remove_runtime_record(process_name)
                results[process_name] = {"retired": True, "running": False}
            except Exception as exc:
                results[process_name] = {"error": str(exc)}
        self._retired_process_names.clear()

        for process_name, config in self.modules.items():
            runtime = await asyncio.to_thread(
                self._valid_runtime_record_sync,
                process_name,
                True,
            )
            try:
                if not config.get("enabled", True):
                    self._watchdog_failures.pop(process_name, None)
                    if runtime is not None:
                        results[process_name] = await self.stop(process_name)
                    continue
                if (
                    start_missing
                    and runtime is None
                    and str(self._script_setting(config, "start") or "").strip()
                ):
                    results[process_name] = await self.start(process_name)
            except Exception as exc:
                results[process_name] = {"error": str(exc)}
        return results

    def start_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def shutdown(self) -> dict[str, Any]:
        self._shutting_down = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
            self._watchdog_task = None

        results = await self.stop_all()
        self._close_windows_job()
        return results

    async def _watchdog_loop(self) -> None:
        while not self._shutting_down:
            await asyncio.sleep(self._WATCHDOG_INTERVAL_S)
            for process_name, config in list(self.modules.items()):
                if self._shutting_down:
                    return
                if not config.get("enabled", True) or not config.get("auto_restart", False):
                    self._watchdog_failures.pop(process_name, None)
                    self._watchdog_state.pop(process_name, None)
                    continue
                if not str(self._script_setting(config, "start") or "").strip():
                    continue

                runtime = await asyncio.to_thread(
                    self._valid_runtime_record_sync,
                    process_name,
                    True,
                )
                failure_type = "communication_fail" if runtime is not None else "process_dead"
                communication = await self._probe_communication(process_name, config)
                if communication.get("stale"):
                    communication = self._communication_state.get(
                        process_name,
                        communication,
                    )
                healthy = bool(communication.get("online", False))

                if healthy:
                    self._watchdog_failures.pop(process_name, None)
                    self._watchdog_state[process_name] = {
                        "state": "healthy",
                        "consecutive_failures": 0,
                    }
                    continue

                failures = self._watchdog_failures.get(process_name, 0) + 1
                self._watchdog_failures[process_name] = failures
                self._watchdog_state[process_name] = {
                    "state": failure_type,
                    "consecutive_failures": failures,
                }
                if failures < self._WATCHDOG_FAILURE_LIMIT:
                    continue

                self._watchdog_failures[process_name] = 0
                try:
                    if runtime is None:
                        await self.start(process_name)
                    else:
                        await self.restart(process_name)
                except Exception as exc:
                    self._last_actions[process_name] = {
                        "action": "watchdog_restart",
                        "ok": False,
                        "timestamp_ms": now_ms(),
                        "message": str(exc),
                    }

    async def stop_all(self) -> dict[str, Any]:
        results: dict[str, Any] = {}

        # Include configured processes and persisted runtime records.  The
        # latter matters when config.reload removed a module while its service
        # was still alive.
        process_names = set(self.modules.keys())
        process_names.update(self._processes.keys())
        process_names.update(self._persisted_process_names())

        for process_name in sorted(process_names):
            try:
                if process_name in self.modules:
                    runtime = await asyncio.to_thread(
                        self._valid_runtime_record_sync,
                        process_name,
                    )
                    wrapper = self._processes.get(process_name)

                    if (
                        runtime is None
                        and (wrapper is None or wrapper.returncode is not None)
                    ):
                        continue

                    results[process_name] = await self.stop(process_name)
                    continue

                # Process was removed from config, but a persisted fingerprint
                # still allows safe shutdown without guessing its identity.
                runtime = await asyncio.to_thread(
                    self._valid_runtime_record_sync,
                    process_name,
                    True,
                )
                if runtime is not None:
                    await self._terminate_runtime_pid(runtime)

                await self._cleanup_session_wrapper(process_name)
                self._remove_runtime_record(process_name)

                self._last_actions[process_name] = {
                    "action": "stop",
                    "ok": True,
                    "timestamp_ms": now_ms(),
                    "message": f"stopped unconfigured process: {process_name}",
                }
                results[process_name] = {
                    "process_name": process_name,
                    "online": False,
                    "running": False,
                    "pid": _optional_int(runtime.get("pid")) if runtime else None,
                    "returncode": None,
                    "last_action": self._last_actions[process_name],
                }

            except Exception as exc:
                self._last_actions[process_name] = {
                    "action": "stop",
                    "ok": False,
                    "timestamp_ms": now_ms(),
                    "message": str(exc),
                }
                results[process_name] = {
                    "process_name": process_name,
                    "online": False,
                    "running": False,
                    "error": str(exc),
                    "last_action": self._last_actions[process_name],
                }

        return results

    async def command(self, action: str, process_name: str) -> dict[str, Any]:
        process_name = str(process_name).strip()
        if not process_name:
            raise ValueError("process_name is required")

        if action == "start":
            return await self.start(process_name)
        if action == "stop":
            return await self.stop(process_name)
        if action == "restart":
            return await self.restart(process_name)
        if action == "status":
            return await self.status(process_name)

        raise ValueError(f"unsupported process action: {action}")

    def _module(self, process_name: str) -> dict[str, Any]:
        config = self.modules.get(process_name)
        if not isinstance(config, dict):
            raise ValueError(f"unknown process_name: {process_name}")
        return config

    def _index_modules_by_process_name(
        self,
        modules: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Build the runtime configuration map using process_name as the key."""
        indexed: dict[str, dict[str, Any]] = {}

        for config_key, config in modules.items():
            # Config Server is the Core's own JsonLineServer.  Main/Core and
            # its embedded server are never child-process management targets.
            if config_key in {"config", "main"}:
                continue
            if not isinstance(config, dict):
                raise ValueError(
                    f"process configuration must be an object: {config_key}"
                )

            process_name = str(config.get("process_name") or "").strip()
            if not process_name:
                display_name = str(config.get("name") or config_key)
                raise ValueError(
                    "process_name is required for process configuration: "
                    f"{display_name}"
                )

            if process_name in indexed:
                raise ValueError(f"duplicate process_name: {process_name}")

            indexed[process_name] = config

        return indexed

    def _snapshot_one(
        self,
        process_name: str,
        config: dict[str, Any],
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        running = runtime is not None
        wrapper = self._processes.get(process_name)

        return {
            "name": config.get("name", process_name),
            "process_name": process_name,
            "enabled": bool(config.get("enabled", True)),
            "start_scripts": self._script_setting(config, "start") or "",
            "stop_script": self._script_setting(config, "stop") or "",
            "working_dir": config.get("working_dir", ""),
            "health_type": config.get("health_type", "none"),
            "health_host": config.get("health_host", ""),
            "health_port": config.get("health_port", 0),
            "auto_restart": bool(config.get("auto_restart", False)),
            "running": running,
            "online": running,
            "managed": running,
            "recovered": bool(
                runtime is not None and runtime.get("_source") == "pid_file"
            ),
            # Actual service PID.  It may differ from wrapper_pid for BAT/CMD.
            "pid": _optional_int(runtime.get("pid")) if runtime else None,
            "wrapper_pid": (
                _optional_int(runtime.get("wrapper_pid"))
                if runtime
                else (wrapper.pid if wrapper is not None else None)
            ),
            "returncode": wrapper.returncode if wrapper is not None else None,
            "last_action": self._last_actions.get(process_name),
            "watchdog": self._watchdog_state.get(
                process_name,
                {"state": "disabled" if not config.get("auto_restart", False) else "idle", "consecutive_failures": 0},
            ),
            "health": {
                "online": running,
                "type": "process",
                "message": "process is alive" if running else "process is dead",
            },
        }

    def _compose_status(
        self,
        item: dict[str, Any],
        communication: dict[str, Any] | None,
        state: str | None = None,
    ) -> dict[str, Any]:
        running = bool(item.get("running"))
        communication = communication or self._communication_result(
            str(item["process_name"]),
            success=False,
            error="communication has not been checked",
            update_failure=False,
        )
        if not item.get("enabled", True):
            communication = {
                **communication,
                "online": False,
                "error": "service is disabled",
            }
        elif not running:
            communication = {
                **communication,
                "online": False,
                "error": "process is dead",
            }
        if state is None:
            if not item.get("enabled", True):
                state = "disabled"
            elif not running:
                state = "offline"
            elif communication.get("online"):
                state = "online"
            else:
                state = "communication_error"
        item["state"] = state
        item["status"] = {
            "disabled": "Disabled",
            "offline": "Offline: process dead",
            "communication_error": "Communication Error",
            "online": "Online",
        }[state]
        item["running"] = running
        item["managed"] = bool(item.get("managed"))
        item["online"] = state == "online"
        item["communication"] = communication
        return item

    def _communication_result(
        self,
        process_name: str,
        *,
        success: bool,
        request_id: str | None = None,
        sequence: int | None = None,
        response: Any = None,
        latency_ms: int | None = None,
        error: str | None = None,
        timed_out: bool = False,
        update_failure: bool = True,
    ) -> dict[str, Any]:
        previous = self._communication_state.get(process_name, {})
        failures = int(previous.get("consecutive_failures") or 0)
        if update_failure:
            failures = 0 if success else failures + 1
        result = {
            "online": success,
            "request_id": request_id,
            "sequence": sequence,
            "last_ping_ms": previous.get("last_ping_ms"),
            "last_pong_ms": now_ms() if success else previous.get("last_pong_ms"),
            "latency_ms": latency_ms,
            "consecutive_failures": failures,
            "response": response,
            "timeout": timed_out,
            "error": error,
        }
        self._communication_state[process_name] = result
        return result

    async def _probe_communication(
        self,
        process_name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = self._communication_configs.get(process_name, {})
        host = client_connect_host(
            str(endpoint.get("host") or config.get("health_host") or "127.0.0.1")
        )
        port = int(endpoint.get("port") or config.get("health_port") or 0)
        command = str(endpoint.get("command") or "ping")
        timeout_s = float(endpoint.get("timeout_s") or 2.0)
        endpoint_text = f"{host}:{port}" if port > 0 else None
        sequence = self._ping_sequences.get(process_name, 0) + 1
        self._ping_sequences[process_name] = sequence
        request_id = f"ping-{process_name}-{sequence}-{uuid.uuid4().hex[:8]}"
        params = endpoint.get("params", {"request_id": request_id, "sequence": sequence})
        started = now_ms()
        previous = self._communication_state.get(process_name, {})
        self._communication_state[process_name] = {
            **previous,
            "online": False,
            "request_id": request_id,
            "sequence": sequence,
            "last_ping_ms": started,
        }
        if port <= 0:
            result = self._communication_result(
                process_name,
                success=False,
                request_id=request_id,
                sequence=sequence,
                error="communication endpoint is not configured",
            )
            result.update(endpoint=endpoint_text, command=command, timeout_s=timeout_s)
            return result

        try:
            response = await send_json_request(
                host,
                port,
                command,
                params,
                timeout_s=timeout_s,
                request_id=request_id,
            )
            if sequence != self._ping_sequences.get(process_name):
                return {
                    "online": False,
                    "request_id": request_id,
                    "sequence": sequence,
                    "response": response.result,
                    "timeout": False,
                    "error": "stale response ignored",
                    "stale": True,
                    "endpoint": endpoint_text,
                    "command": command,
                }
            if not response.ok:
                raise RuntimeError(str(response.error))
            result = self._communication_result(
                process_name,
                success=True,
                request_id=request_id,
                sequence=sequence,
                response=response.result,
                latency_ms=max(0, now_ms() - started),
            )
            result["last_ping_ms"] = started
            result.update(endpoint=endpoint_text, command=command, timeout_s=timeout_s)
            return result
        except Exception as exc:
            if sequence != self._ping_sequences.get(process_name):
                return {
                    "online": False,
                    "request_id": request_id,
                    "sequence": sequence,
                    "response": None,
                    "timeout": False,
                    "error": "stale response ignored",
                    "stale": True,
                    "endpoint": endpoint_text,
                    "command": command,
                }
            message = str(exc)
            result = self._communication_result(
                process_name,
                success=False,
                request_id=request_id,
                sequence=sequence,
                latency_ms=max(0, now_ms() - started),
                error=message,
                timed_out="timeout" in message.lower(),
            )
            result["last_ping_ms"] = started
            result.update(endpoint=endpoint_text, command=command, timeout_s=timeout_s)
            return result

    async def broadcast_ping(self) -> dict[str, Any]:
        broadcast_id = f"broadcast-{uuid.uuid4().hex[:12]}"

        async def probe(process_name: str) -> dict[str, Any]:
            status = await self.status(process_name)
            communication = status["communication"]
            return {
                "request_id": communication.get("request_id"),
                "module": status.get("name"),
                "process_name": process_name,
                "success": bool(status.get("online")),
                "response": communication.get("response"),
                "timeout": bool(communication.get("timeout")),
                "error": communication.get("error"),
            }

        names = [
            name for name, config in self.modules.items() if config.get("enabled", True)
        ]
        return {
            "request_id": broadcast_id,
            "results": await asyncio.gather(*(probe(name) for name in names)),
        }

    async def _check_health(self, config: dict[str, Any]) -> dict[str, Any]:
        health_type = str(config.get("health_type", "none")).lower()
        if health_type in {"", "none", "process"}:
            return {
                "type": health_type or "none",
                "online": False,
                "message": "no endpoint health check",
            }

        if health_type in {"tcp", "http"}:
            host = client_connect_host(
                str(config.get("health_host") or "127.0.0.1")
            )
            port = int(config.get("health_port") or 0)
            if port <= 0:
                return {
                    "type": health_type,
                    "online": False,
                    "message": "health_port is empty",
                }

            started = now_ms()
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=0.6,
                )
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

                return {
                    "type": health_type,
                    "online": True,
                    "endpoint": f"{host}:{port}",
                    "response_ms": max(0, now_ms() - started),
                }
            except Exception as exc:
                return {
                    "type": health_type,
                    "online": False,
                    "endpoint": f"{host}:{port}",
                    "message": str(exc),
                }

        return {
            "type": health_type,
            "online": False,
            "message": "unsupported health_type",
        }

    async def _discover_runtime_process(
        self,
        process_name: str,
        config: dict[str, Any],
        wrapper_pid: int,
        script: Path,
    ) -> dict[str, Any] | None:
        """Find the real service PID created by start_scripts.

        Discovery priority:
        1. PID that owns configured health_port.
        2. Descendant of the wrapper process.
        3. Wrapper PID itself for direct executable launches.

        For BAT/CMD/PS1 wrappers without health_port, the manager waits briefly
        for a non-shell descendant.  If a script fully detaches a child and
        ownership cannot be proven, this method returns None rather than
        adopting an arbitrary python.exe.
        """
        health_port = int(config.get("health_port") or 0)
        wrapper_suffix = script.suffix.lower()
        deadline = asyncio.get_running_loop().time() + self._DISCOVERY_TIMEOUT_S

        last_descendants: list[int] = []

        while asyncio.get_running_loop().time() < deadline:
            # Strongest signal: the configured service endpoint is owned by one
            # of the processes that appeared after our start request.
            if health_port > 0:
                listening_pids = await asyncio.to_thread(
                    self._listening_pids,
                    health_port,
                )
                if listening_pids:
                    descendants = await asyncio.to_thread(
                        self._descendant_pids,
                        wrapper_pid,
                    )
                    allowed = {wrapper_pid, *descendants}

                    # Normal case: port owner is wrapper or one of its children.
                    candidates = [pid for pid in listening_pids if pid in allowed]

                    # `cmd.exe /c start ...` may detach fast enough that process
                    # ancestry becomes hard to reconstruct.  Because the port
                    # was verified offline immediately before start(), a single
                    # new owner at this exact configured port is safe to adopt.
                    if not candidates and len(listening_pids) == 1:
                        candidates = list(listening_pids)

                    if candidates:
                        pid = candidates[0]
                        record = await asyncio.to_thread(
                            self._build_runtime_record,
                            process_name,
                            pid,
                            wrapper_pid,
                            config,
                            script,
                        )
                        if record is not None:
                            return record

                # Minimal/container Ubuntu installations may not have `ss`,
                # and `ss -p` can omit PID details even for a reachable port.
                # The endpoint was verified offline before start(), so adopt
                # only a live descendant of the wrapper after readiness is
                # confirmed.  Never adopt an unrelated system process here.
                health = await self._check_health(config)
                if health.get("online", False):
                    descendants = await asyncio.to_thread(
                        self._descendant_pids,
                        wrapper_pid,
                    )
                    pid = await asyncio.to_thread(
                        self._select_service_descendant,
                        descendants,
                    )
                    if pid is not None:
                        record = await asyncio.to_thread(
                            self._build_runtime_record,
                            process_name,
                            pid,
                            wrapper_pid,
                            config,
                            script,
                        )
                        if record is not None:
                            return record

                wrapper = self._processes.get(process_name)
                if wrapper is not None and wrapper.returncode is not None:
                    return None

                # A configured health endpoint is the lifecycle readiness
                # contract.  Do not adopt transient setup/python processes
                # before that exact port has a verified owner.
                await asyncio.sleep(self._DISCOVERY_INTERVAL_S)
                continue

            descendants = await asyncio.to_thread(
                self._descendant_pids,
                wrapper_pid,
            )
            if descendants:
                last_descendants = descendants

                # Without a health port, prefer the deepest non-shell child.
                pid = await asyncio.to_thread(
                    self._select_service_descendant,
                    descendants,
                )
                if pid is not None:
                    record = await asyncio.to_thread(
                        self._build_runtime_record,
                        process_name,
                        pid,
                        wrapper_pid,
                        config,
                        script,
                    )
                    if record is not None:
                        # Give BAT/CMD/PS1 a little time to finish spawning
                        # before accepting a child that may only be transient.
                        if wrapper_suffix not in {".bat", ".cmd", ".ps1"}:
                            return record

                        await asyncio.sleep(self._DISCOVERY_INTERVAL_S)
                        if self._pid_exists(pid):
                            return record

            # Direct EXE launch: the wrapper is the service.
            if (
                wrapper_suffix == ".exe"
                and self._pid_exists(wrapper_pid)
            ):
                record = await asyncio.to_thread(
                    self._build_runtime_record,
                    process_name,
                    wrapper_pid,
                    wrapper_pid,
                    config,
                    script,
                )
                if record is not None:
                    return record

            await asyncio.sleep(self._DISCOVERY_INTERVAL_S)

        if health_port > 0:
            return None

        # Final fallback after the discovery window.
        if last_descendants:
            pid = await asyncio.to_thread(
                self._select_service_descendant,
                last_descendants,
            )
            if pid is not None and self._pid_exists(pid):
                return await asyncio.to_thread(
                    self._build_runtime_record,
                    process_name,
                    pid,
                    wrapper_pid,
                    config,
                    script,
                )

        return None

    def _build_runtime_record(
        self,
        process_name: str,
        pid: int,
        wrapper_pid: int,
        config: dict[str, Any],
        script: Path,
    ) -> dict[str, Any] | None:
        info = self._process_info(pid)
        if info is None:
            return None

        if os.name == "nt":
            self._assign_windows_job(pid)

        return {
            "version": self._STATE_VERSION,
            "process_name": process_name,
            "pid": int(pid),
            "wrapper_pid": int(wrapper_pid),
            "captured_ms": now_ms(),
            "start_token": str(info.get("start_token") or ""),
            "name": str(info.get("name") or ""),
            "executable": str(info.get("executable") or ""),
            "command_line": str(info.get("command_line") or ""),
            "health_port": int(config.get("health_port") or 0),
            "start_scripts": str(script),
        }

    def _valid_runtime_record_sync(
        self,
        process_name: str,
        force: bool = False,
    ) -> dict[str, Any] | None:
        """Load and validate ownership using PID + process fingerprint.

        A cached validation is reused briefly for frequent status polling.
        Destructive operations pass force=True to bypass the cache.
        """
        cached = self._runtime_cache.get(process_name)
        validated_ms = self._runtime_validation_ms.get(process_name, 0)

        if (
            not force
            and cached is not None
            and now_ms() - validated_ms < self._VALIDATION_CACHE_MS
            and self._pid_exists(int(cached["pid"]))
        ):
            return dict(cached)

        record = self._load_runtime_record(process_name)
        if record is None:
            self._runtime_cache.pop(process_name, None)
            self._runtime_validation_ms.pop(process_name, None)
            return None

        if not self._record_identity_matches(process_name, record):
            # PID is gone or has been reused/changed.  Never control it.
            self._remove_runtime_record(process_name)
            return None

        record["_source"] = (
            "session" if process_name in self._session_owned else "pid_file"
        )
        self._runtime_cache[process_name] = dict(record)
        self._runtime_validation_ms[process_name] = now_ms()
        return dict(record)

    def _record_identity_matches(
        self,
        process_name: str,
        record: dict[str, Any],
    ) -> bool:
        if str(record.get("process_name") or "") != process_name:
            return False

        pid = _optional_int(record.get("pid"))
        if pid is None or pid <= 0:
            return False
        if not self._pid_exists(pid):
            return False

        info = self._process_info(pid)
        if info is None:
            return False

        saved_start = str(record.get("start_token") or "")
        current_start = str(info.get("start_token") or "")

        # Creation/start token is the primary PID-reuse guard.
        if saved_start:
            if not current_start or saved_start != current_start:
                return False

        saved_exe = _normalize_path(record.get("executable"))
        current_exe = _normalize_path(info.get("executable"))
        if saved_exe and current_exe and saved_exe != current_exe:
            return False

        saved_cmd = _normalize_command_line(record.get("command_line"))
        current_cmd = _normalize_command_line(info.get("command_line"))
        if saved_cmd and current_cmd and saved_cmd != current_cmd:
            return False

        # If no start token was available, require at least one stable identity
        # field to match.  This prevents blind PID-only ownership.
        if not saved_start and not (
            (saved_exe and current_exe and saved_exe == current_exe)
            or (saved_cmd and current_cmd and saved_cmd == current_cmd)
        ):
            return False

        return True

    def _save_runtime_record(
        self,
        process_name: str,
        record: dict[str, Any],
        source: str,
    ) -> None:
        path = self._state_file(process_name)
        path.parent.mkdir(parents=True, exist_ok=True)

        persisted = {
            key: value
            for key, value in record.items()
            if not str(key).startswith("_")
        }

        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(persisted, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(path)

        cached = dict(persisted)
        cached["_source"] = source
        if source == "session":
            self._session_owned.add(process_name)
        self._runtime_cache[process_name] = cached
        self._runtime_validation_ms[process_name] = now_ms()

    def _load_runtime_record(
        self,
        process_name: str,
    ) -> dict[str, Any] | None:
        path = self._state_file(process_name)
        if not path.exists():
            return None

        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(value, dict):
            return None
        if int(value.get("version") or 0) != self._STATE_VERSION:
            return None
        return value

    def _remove_runtime_record(self, process_name: str) -> None:
        self._runtime_cache.pop(process_name, None)
        self._runtime_validation_ms.pop(process_name, None)
        self._session_owned.discard(process_name)

        path = self._state_file(process_name)
        with contextlib.suppress(FileNotFoundError, OSError):
            path.unlink()

    def _persisted_process_names(self) -> set[str]:
        result: set[str] = set()

        if not self._runtime_dir.exists():
            return result

        for path in self._runtime_dir.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(value, dict):
                continue

            process_name = str(value.get("process_name") or "").strip()
            if process_name:
                result.add(process_name)

        return result

    def _state_file(self, process_name: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", process_name).strip("._")
        if not safe:
            safe = "process"
        safe = safe[:80]

        digest = hashlib.sha256(process_name.encode("utf-8")).hexdigest()[:10]
        return self._runtime_dir / f"{safe}-{digest}.json"

    async def _terminate_runtime_pid(
        self,
        runtime: dict[str, Any],
    ) -> None:
        pid = int(runtime["pid"])

        # Validate immediately before sending any destructive signal.
        process_name = str(runtime.get("process_name") or "")
        if not self._record_identity_matches(process_name, runtime):
            # Do not ever fall back to killing by image name.
            self._remove_runtime_record(process_name)
            return

        if os.name == "nt":
            await self._terminate_windows_tree(pid)
        else:
            await self._terminate_posix_tree(pid)

    async def _terminate_windows_tree(self, pid: int) -> None:
        targets = [*reversed(self._descendant_pids(pid)), pid]
        fingerprints = {
            target: self._windows_process_info(target)
            for target in targets
        }

        for target in targets:
            saved = fingerprints.get(target)
            current = self._windows_process_info(target)
            if saved is None or current is None:
                continue
            if str(saved.get("start_token") or "") != str(
                current.get("start_token") or ""
            ):
                continue
            self._terminate_windows_pid(target)

        if not await self._wait_pid_exit(pid, timeout_s=5.0):
            raise RuntimeError(f"failed to terminate process tree: pid {pid}")

    def _terminate_windows_pid(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if not handle:
            if self._pid_exists(pid):
                raise OSError(ctypes.get_last_error(), f"OpenProcess failed: pid {pid}")
            return
        try:
            if not kernel32.TerminateProcess(handle, 1) and self._pid_exists(pid):
                raise OSError(ctypes.get_last_error(), f"TerminateProcess failed: pid {pid}")
        finally:
            kernel32.CloseHandle(handle)

    async def _terminate_posix_tree(self, pid: int) -> None:
        descendants = await asyncio.to_thread(self._descendant_pids, pid)

        # Children first, then the service process.
        for target in [*reversed(descendants), pid]:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(target, signal.SIGTERM)

        if await self._wait_pid_exit(pid, timeout_s=5.0):
            return

        descendants = await asyncio.to_thread(self._descendant_pids, pid)
        for target in [*reversed(descendants), pid]:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(target, signal.SIGKILL)

        if not await self._wait_pid_exit(pid, timeout_s=2.0):
            raise RuntimeError(f"failed to terminate process tree: pid {pid}")

    async def _cleanup_session_wrapper(self, process_name: str) -> None:
        wrapper = self._processes.pop(process_name, None)
        if wrapper is None:
            return

        if wrapper.returncode is not None:
            return

        try:
            await asyncio.wait_for(wrapper.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            await self._terminate_process(wrapper)

    async def _terminate_process(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        """Terminate a wrapper subprocess created in the current Core session."""
        if process.returncode is not None:
            return

        if os.name == "nt":
            with contextlib.suppress(Exception):
                process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            with contextlib.suppress(Exception):
                os.killpg(process.pid, signal.SIGTERM)

        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                process.kill()
            await process.wait()

    async def _wait_pid_exit(self, pid: int, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s

        while asyncio.get_running_loop().time() < deadline:
            if not self._pid_exists(pid):
                return True
            await asyncio.sleep(0.10)

        return not self._pid_exists(pid)

    def _create_windows_job(self) -> int:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        return int(handle)

    def _assign_windows_job(self, pid: int) -> None:
        if self._windows_job is None:
            raise RuntimeError("Windows lifecycle job is not available")

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process_handle = kernel32.OpenProcess(
            0x0100 | 0x0001,  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
            False,
            int(pid),
        )
        if not process_handle:
            raise OSError(ctypes.get_last_error(), f"OpenProcess failed: pid {pid}")

        try:
            in_job = wintypes.BOOL()
            if kernel32.IsProcessInJob(
                process_handle,
                wintypes.HANDLE(self._windows_job),
                ctypes.byref(in_job),
            ) and in_job.value:
                return
            if not kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(self._windows_job),
                process_handle,
            ):
                raise OSError(
                    ctypes.get_last_error(),
                    f"AssignProcessToJobObject failed: pid {pid}",
                )
        finally:
            kernel32.CloseHandle(process_handle)

    def _close_windows_job(self) -> None:
        if self._windows_job is None:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(wintypes.HANDLE(self._windows_job))
        self._windows_job = None

    def _process_info(self, pid: int) -> dict[str, Any] | None:
        if os.name == "nt":
            return self._windows_process_info(pid)
        return self._posix_process_info(pid)

    def _windows_process_info(self, pid: int) -> dict[str, Any] | None:
        import ctypes
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", wintypes.DWORD),
                ("high", wintypes.DWORD),
            ]

        class ProcessBasicInformation(ctypes.Structure):
            _fields_ = [
                ("reserved1", ctypes.c_void_p),
                ("peb_base", ctypes.c_void_p),
                ("reserved2", ctypes.c_void_p * 2),
                ("unique_pid", ctypes.c_void_p),
                ("parent_pid", ctypes.c_void_p),
            ]

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.USHORT),
                ("maximum_length", wintypes.USHORT),
                ("buffer", ctypes.c_void_p),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        ntdll.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE,
            wintypes.ULONG,
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        ]

        handle = kernel32.OpenProcess(
            0x1000 | 0x0010,  # QUERY_LIMITED_INFORMATION | VM_READ
            False,
            int(pid),
        )
        if not handle:
            return None

        try:
            executable_buffer = ctypes.create_unicode_buffer(32768)
            executable_size = wintypes.DWORD(len(executable_buffer))
            executable = ""
            if kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                executable_buffer,
                ctypes.byref(executable_size),
            ):
                executable = executable_buffer.value

            creation = FileTime()
            exit_time = FileTime()
            kernel_time = FileTime()
            user_time = FileTime()
            start_token = ""
            if kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                start_token = str((int(creation.high) << 32) | int(creation.low))

            basic = ProcessBasicInformation()
            return_length = wintypes.ULONG()
            ppid = 0
            if ntdll.NtQueryInformationProcess(
                handle,
                0,
                ctypes.byref(basic),
                ctypes.sizeof(basic),
                ctypes.byref(return_length),
            ) == 0:
                ppid = int(basic.parent_pid or 0)

            command_line = ""
            required = wintypes.ULONG()
            ntdll.NtQueryInformationProcess(
                handle,
                60,  # ProcessCommandLineInformation
                None,
                0,
                ctypes.byref(required),
            )
            if required.value:
                raw = ctypes.create_string_buffer(required.value)
                if ntdll.NtQueryInformationProcess(
                    handle,
                    60,
                    raw,
                    required.value,
                    ctypes.byref(required),
                ) == 0:
                    value = ctypes.cast(raw, ctypes.POINTER(UnicodeString)).contents
                    if value.buffer and value.length:
                        command_line = ctypes.wstring_at(
                            value.buffer,
                            value.length // ctypes.sizeof(ctypes.c_wchar),
                        )

            return {
                "pid": int(pid),
                "ppid": ppid,
                "name": Path(executable).name,
                "executable": executable,
                "command_line": command_line,
                "start_token": start_token,
            }
        finally:
            kernel32.CloseHandle(handle)

    def _posix_process_info(self, pid: int) -> dict[str, Any] | None:
        proc_dir = Path("/proc") / str(int(pid))
        if not proc_dir.exists():
            return None

        try:
            stat_text = (proc_dir / "stat").read_text(
                encoding="utf-8",
                errors="replace",
            )
            close_paren = stat_text.rfind(")")
            if close_paren < 0:
                return None

            rest = stat_text[close_paren + 2 :].split()
            # /proc/<pid>/stat:
            # rest[0] = field 3 (state)
            # rest[1] = field 4 (ppid)
            # rest[19] = field 22 (starttime)
            ppid = int(rest[1])
            start_token = rest[19]

            name = (proc_dir / "comm").read_text(
                encoding="utf-8",
                errors="replace",
            ).strip()

            command_raw = (proc_dir / "cmdline").read_bytes()
            command_line = command_raw.replace(b"\x00", b" ").decode(
                "utf-8",
                errors="replace",
            ).strip()

            executable = ""
            with contextlib.suppress(OSError):
                executable = os.readlink(proc_dir / "exe")

            return {
                "pid": int(pid),
                "ppid": ppid,
                "state": rest[0],
                "name": name,
                "executable": executable,
                "command_line": command_line,
                "start_token": start_token,
            }
        except (OSError, ValueError, IndexError):
            return None

    def _descendant_pids(self, root_pid: int) -> list[int]:
        if os.name == "nt":
            table = self._windows_process_table()
        else:
            table = self._posix_process_table()

        if not table:
            return []

        children: dict[int, list[int]] = {}
        for pid, info in table.items():
            ppid = _optional_int(info.get("ppid"))
            if ppid is None:
                continue
            children.setdefault(ppid, []).append(pid)

        result: list[int] = []
        stack = list(children.get(int(root_pid), []))

        while stack:
            pid = stack.pop(0)
            if pid in result:
                continue
            result.append(pid)
            stack.extend(children.get(pid, []))

        return result

    def _windows_process_table(self) -> dict[int, dict[str, Any]]:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("usage", wintypes.DWORD),
                ("pid", wintypes.DWORD),
                ("default_heap", ctypes.c_void_p),
                ("module_id", wintypes.DWORD),
                ("threads", wintypes.DWORD),
                ("parent_pid", wintypes.DWORD),
                ("priority_base", ctypes.c_long),
                ("flags", wintypes.DWORD),
                ("exe_file", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return {}

        table: dict[int, dict[str, Any]] = {}
        try:
            entry = ProcessEntry32()
            entry.size = ctypes.sizeof(entry)
            has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
            while has_entry:
                table[int(entry.pid)] = {
                    "pid": int(entry.pid),
                    "ppid": int(entry.parent_pid),
                    "name": str(entry.exe_file),
                }
                has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)

        return table

    def _posix_process_table(self) -> dict[int, dict[str, Any]]:
        table: dict[int, dict[str, Any]] = {}
        proc = Path("/proc")
        if not proc.exists():
            return table

        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue

            pid = int(entry.name)
            info = self._posix_process_info(pid)
            if info is not None:
                table[pid] = info

        return table

    def _select_service_descendant(
        self,
        descendants: list[int],
    ) -> int | None:
        # Descendants are returned parent-first.  Prefer the deepest/later
        # process that is not an obvious shell wrapper.
        shell_names = {
            "cmd.exe",
            "powershell.exe",
            "pwsh.exe",
            "conhost.exe",
            "sh",
            "bash",
            "dash",
        }

        for pid in reversed(descendants):
            info = self._process_info(pid)
            if info is None:
                continue

            name = str(info.get("name") or "").lower()
            if name not in shell_names:
                return pid

        return None

    def _listening_pids(self, port: int) -> list[int]:
        if port <= 0:
            return []

        if os.name == "nt":
            result = self._run_sync_command(
                ["netstat.exe", "-ano", "-p", "tcp"]
            )
            if result.returncode != 0:
                return []

            pids: set[int] = set()
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                if parts[0].upper() != "TCP":
                    continue
                if parts[3].upper() != "LISTENING":
                    continue
                if not _endpoint_has_port(parts[1], port):
                    continue

                pid = _optional_int(parts[4])
                if pid is not None:
                    pids.add(pid)

            return sorted(pids)

        # Linux: best effort using `ss`.  If ss is not installed or process
        # details are hidden by permissions, discovery falls back to ancestry.
        result = self._run_sync_command(["ss", "-ltnp"])

        pids: set[int] = set()
        pid_pattern = re.compile(r"pid=(\d+)")

        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "LISTEN" not in line:
                    continue

                parts = line.split()
                if len(parts) < 4:
                    continue

                local_endpoint = parts[3]
                if not _endpoint_has_port(local_endpoint, port):
                    continue

                for match in pid_pattern.finditer(line):
                    pids.add(int(match.group(1)))

        if pids:
            return sorted(pids)

        return self._proc_listening_pids(port)

    def _proc_listening_pids(self, port: int) -> list[int]:
        """Resolve Linux TCP listener owners without requiring ss/lsof."""
        socket_inodes: set[str] = set()
        for table_path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
            try:
                lines = table_path.read_text(
                    encoding="ascii",
                    errors="replace",
                ).splitlines()[1:]
            except OSError:
                continue

            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "0A":  # TCP_LISTEN
                    continue
                try:
                    local_port = int(fields[1].rsplit(":", 1)[1], 16)
                except (IndexError, ValueError):
                    continue
                if local_port == port:
                    socket_inodes.add(fields[9])

        if not socket_inodes:
            return []

        owners: set[int] = set()
        proc_root = Path("/proc")
        try:
            process_dirs = list(proc_root.iterdir())
        except OSError:
            return []

        targets = {f"socket:[{inode}]" for inode in socket_inodes}
        for process_dir in process_dirs:
            if not process_dir.name.isdigit():
                continue
            try:
                descriptors = (process_dir / "fd").iterdir()
                for descriptor in descriptors:
                    try:
                        if os.readlink(descriptor) in targets:
                            owners.add(int(process_dir.name))
                            break
                    except OSError:
                        continue
            except OSError:
                continue

        return sorted(owners)

    def _pid_exists(self, pid: int) -> bool:
        if pid <= 0:
            return False

        if os.name == "nt":
            # A terminated process object can remain open briefly while handles
            # are released.  Check STILL_ACTIVE instead of OpenProcess alone.
            try:
                import ctypes
                from ctypes import wintypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.OpenProcess.argtypes = [
                    wintypes.DWORD,
                    wintypes.BOOL,
                    wintypes.DWORD,
                ]
                kernel32.OpenProcess.restype = wintypes.HANDLE
                kernel32.GetExitCodeProcess.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                kernel32.GetExitCodeProcess.restype = wintypes.BOOL
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.CloseHandle.restype = wintypes.BOOL
                handle = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION,
                    False,
                    int(pid),
                )
                if not handle:
                    return False
                try:
                    exit_code = wintypes.DWORD()
                    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return False
                    return exit_code.value == 259  # STILL_ACTIVE
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                return self._windows_process_info(pid) is not None

        info = self._posix_process_info(pid)
        if info is not None:
            return str(info.get("state") or "") != "Z"

        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def _run_sync_command(
        self,
        args: list[str],
    ) -> subprocess.CompletedProcess[str]:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "check": False,
        }

        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )

        try:
            return subprocess.run(args, **kwargs)
        except OSError as exc:
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout="",
                stderr=str(exc),
            )

    def _script_path(self, value: Any) -> Path | None:
        if value is None or str(value).strip() == "":
            return None
        return resolve_app_path(str(value).strip(), ROOT_DIR)

    @staticmethod
    def _script_setting(config: dict[str, Any], action: str) -> Any:
        if action == "start":
            return config.get("start_scripts")
        return config.get(f"{action}_script")

    def _working_dir(self, config: dict[str, Any], script: Path) -> Path:
        working_dir = str(config.get("working_dir") or "").strip()
        if working_dir:
            return resolve_app_path(working_dir, ROOT_DIR)
        return script.parent

    def _validate_script(self, script: Path) -> None:
        if not script.exists() or not script.is_file():
            raise ValueError(f"script not found: {script}")

        suffix = script.suffix.lower()

        if os.name == "nt" and suffix not in {".bat", ".cmd", ".ps1", ".exe"}:
            raise ValueError(
                "Windows module scripts must be .bat, .cmd, .ps1, or .exe"
            )

        if os.name != "nt" and suffix != ".sh":
            raise ValueError("Linux module scripts must be .sh")

    def _start_command(self, script: Path) -> list[str]:
        suffix = script.suffix.lower()

        if os.name == "nt" and suffix in {".bat", ".cmd"}:
            return ["cmd.exe", "/c", str(script)]

        if os.name == "nt" and suffix == ".ps1":
            return [
                "powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ]

        if os.name != "nt":
            supervisor = Path(__file__).with_name("supervisor.py")
            return [sys.executable, str(supervisor), str(script)]

        return [str(script)]

    async def _run_script_once(
        self,
        script: Path,
        working_dir: Path,
    ) -> None:
        args = self._start_command(script)

        kwargs: dict[str, Any] = {
            "cwd": str(working_dir),
            "stdout": asyncio.subprocess.DEVNULL,
            "stderr": asyncio.subprocess.DEVNULL,
            "env": {**os.environ, "PYTHON": sys.executable},
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )

        process = await asyncio.create_subprocess_exec(*args, **kwargs)

        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError as exc:
            with contextlib.suppress(Exception):
                process.kill()
            raise TimeoutError(f"stop script timeout: {script}") from exc

        if process.returncode not in {0, None}:
            raise RuntimeError(
                f"script exited with code {process.returncode}: {script}"
            )


def _optional_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def _normalize_command_line(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _endpoint_has_port(endpoint: str, port: int) -> bool:
    endpoint = str(endpoint or "").strip()

    if endpoint.endswith(f":{port}"):
        return True

    # IPv6 netstat/ss forms such as [::]:8080 are also covered by endswith,
    # but keep this regex for unusual bracket/zone formatting.
    return bool(re.search(rf"[:\]]{int(port)}$", endpoint))
