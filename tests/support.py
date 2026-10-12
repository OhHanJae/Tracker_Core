"""Loopback TCP peers and isolated Core settings for contract tests."""
from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from pathlib import Path

from src.common.settings import AppSettings, save_settings
from src.common.status import CommandStatus
from src.communication.plc_memory_mapping import PlcInput
from src.core.application import CoreApplication


def measurement(params, *, supported=True, data_valid=True):
    # Synthetic contract data; these values never represent a Vision measurement.
    return {
        **params, "supported": supported, "data_valid": data_valid,
        "tightening_angle_deg": 90.5 if data_valid else None,
        "inclination_deg": 2.5 if data_valid else None,
        "fastening_coordinate_mm": (
            {"x": 10.0, "y": -20.0, "z": 30.0} if data_valid
            else {"x": None, "y": None, "z": None}
        ),
        "coordinate_frame": "test-fixture" if data_valid else None,
        "timestamp_ms": 1791775800000 if data_valid else None,
    }


class LoopbackPeer:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.handlers = set()
        self.errors = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            request = json.loads((await reader.readline()).decode("utf-8"))
            self.requests.append(request)
            response = await self.reply(request, writer)
            if response is not None:
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                await writer.drain()
        except (OSError, asyncio.CancelledError):
            pass
        except Exception as exc:
            self.errors.append(exc)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.handlers.discard(task)

    async def stop(self):
        self.server.close()
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.server.wait_closed()
        if self.errors:
            raise AssertionError(f"loopback peer failed: {self.errors!r}")


class CoreTestMixin:
    def make_core(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        settings = AppSettings()
        for name in settings.paths.__dataclass_fields__:
            setattr(settings.paths, name, str(root / name))
        settings.discovery.enabled = False
        settings.logging.file_enabled = False
        settings.runtime.orchestration_enabled = False
        settings.processes["plc_gateway"]["enabled"] = False
        settings.processes["vision"]["enabled"] = True
        settings.vision.enabled = True
        settings.vision.timeout_s = 0.12
        settings.laser.enabled = False
        path = root / "global.json"
        save_settings(settings, path)
        core = CoreApplication(path, managed_worker=True)
        core.vision_service.state.online = True
        core.ptm_service.state.online = True
        core.ptm_service.state.status = {"connected": True, "ready": True, "homed": True}
        self.addAsyncCleanup(self.close_core, core)
        return core

    @staticmethod
    async def close_core(core):
        tasks = [core._command_task, core._safe_stop_task, *core._bypass_stop_tasks]
        tasks = [task for task in tasks if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        core.plc_memory.close()
        core.process_manager._close_windows_job()

    async def connect_peer(self, reply):
        peer = await LoopbackPeer(reply).start()
        self.addAsyncCleanup(peer.stop)
        self.core.vision_api.settings.port = peer.port
        return peer

    async def submit(self, *, code=5, seq=105, recipe=3, point=25, run=True, stop=False):
        self.core.plc_input = PlcInput(
            run_enable=run, force_stop=stop, req_recipe_id=recipe,
            req_point_id=point, command_code=code, command_seq=seq,
        )
        await self.core._handle_plc_command()
        if self.core.command.status == CommandStatus.RECEIVED:
            await self.core._handle_plc_command()
        if self.core._command_task is not None:
            # Independent test watchdog detects a command that remains BUSY.
            await asyncio.wait_for(self.core._command_task, timeout=0.6)
        self.core._build_output(10000)
        return self.core.output.to_words()
