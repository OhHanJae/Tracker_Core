"""Optional tests against an existing FVP checkout, without copying its code.

Set FVP_REPOSITORY to the Fastening_Vision_Platform repository root. These
tests exercise its actual bridge, JSON Lines server and persistent recipe
store; camera/tracking inputs are test doubles and never claim measurements.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

from src.common.status import CommandResult, CommandStatus
from src.communication.json_tcp import send_json_request
from tests.support import CoreTestMixin


FVP_REPOSITORY = os.environ.get("FVP_REPOSITORY")


class Camera:
    def status(self):
        return {"connected": False, "frame_age_seconds": None, "ip": None, "error": None}


class Tracking:
    def __init__(self):
        self.active = False

    def core_snapshot(self):
        return {"tracking_active": self.active, "tracking_bypass": False}

    def start(self):
        self.active = True
        return True

    def stop(self):
        self.active = False


@unittest.skipUnless(FVP_REPOSITORY, "set FVP_REPOSITORY for the actual Vision bridge tests")
class ActualVisionIntegrationTests(CoreTestMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = Path(FVP_REPOSITORY).resolve()
        if not (root / "web/backend/services/core_vision_bridge.py").is_file():
            self.fail(f"FVP_REPOSITORY is not a Vision checkout: {root}")
        sys.path.insert(0, str(root))
        self.addCleanup(sys.path.remove, str(root))
        from web.backend.services.core_vision_bridge import CoreVisionBridge, CoreVisionTCPServer
        from web.backend.services.vision_recipe_store import VisionRecipeStore

        self.core = self.make_core()
        self.store = VisionRecipeStore(self.core.settings_path.parent / "vision-config")
        self.store.create_recipe({"recipe_id": 3, "name": "integration-fixture"})
        self.store.create_point(3, {"point_id": 25})
        self.tracking = Tracking()
        self.bridge = CoreVisionBridge(Camera(), self.tracking, recipes=self.store)
        self.server = CoreVisionTCPServer(("127.0.0.1", 0), self.bridge)
        self.thread = self.server.start_background()
        self.addAsyncCleanup(self.close_server)
        self.core.vision_api.settings.port = self.server.server_address[1]
        self.core.vision_api.settings.timeout_s = 0.5
        self.core.settings.vision.port = self.server.server_address[1]
        # Managed workers do not own process modules; register the probe only,
        # without starting any process, so the manager uses this loopback port.
        self.core.process_manager.update_modules({"vision": self.core.settings.processes["vision"]})
        self.core.process_manager.update_communication_endpoints(
            self.core._process_communication_endpoints())

    async def close_server(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        await asyncio.to_thread(self.thread.join, 1)

    async def test_camera_offline_actual_preparation_response_and_seq_bounds(self):
        await self.core.vision_service.poll_once()
        self.assertFalse(self.core.vision_service.state.online)
        self.assertEqual(self.core.vision_service.state.ok_count, 1)
        for seq in (0, 65535):
            words = await self.submit(seq=seq)
            self.assertEqual(words[12:16], [5, seq, int(CommandStatus.REJECTED), int(CommandResult.TRACKING_UNAVAILABLE)])
            result = self.core.nutrunner_result
            self.assertEqual((result["recipe_id"], result["point_id"], result["command_seq"]), (3, 25, seq))
            self.assertIs(result["supported"], False)
            self.assertIs(result["data_valid"], False)
            self.assertIsNone(result["tightening_angle_deg"])
            self.assertIsNone(result["inclination_deg"])
            self.assertIsNone(result["timestamp_ms"])
            self.assertIsNone(result["coordinate_frame"])
            self.assertEqual(result["fastening_coordinate_mm"], {"x": None, "y": None, "z": None})

    async def test_actual_missing_recipe_and_point_map_to_plc_rejection(self):
        for seq, recipe, point, result in [
            (1, 4, 25, CommandResult.RECIPE_NOT_FOUND),
            (2, 3, 26, CommandResult.POINT_NOT_FOUND),
        ]:
            words = await self.submit(seq=seq, recipe=recipe, point=point)
            self.assertEqual(words[14:16], [int(CommandStatus.REJECTED), int(result)])
            self.assertIsNone(self.core.nutrunner_result)

    async def test_actual_corrupt_recipe_store_maps_to_unavailable(self):
        self.store.path.write_text("{invalid", encoding="utf-8")
        words = await self.submit()
        self.assertEqual(words[14:16], [int(CommandStatus.ERROR), int(CommandResult.TRACKING_UNAVAILABLE)])
        self.assertIsNone(self.core.nutrunner_result)

    async def test_actual_status_and_tracking_commands_remain_compatible(self):
        status = await self.core.vision_api.snapshot()
        self.assertFalse(status.online)
        self.assertFalse(status.tracker_valid)
        self.assertIsNone(status.position_error_mm)
        self.assertEqual(await self.core.vision_service.command("tracking.start"), {"accepted": True})
        self.assertTrue(self.tracking.active)
        self.assertEqual(await self.core.vision_service.command("tracking.stop"), {"accepted": True})
        self.assertFalse(self.tracking.active)
        endpoint = self.core._process_communication_endpoints()["vision_server"]
        self.assertEqual(endpoint["command"], "vision.status")
        self.assertEqual(endpoint["params"], {})
        response = await send_json_request("127.0.0.1", self.server.server_address[1], endpoint["command"], endpoint["params"], 0.5)
        self.assertTrue(response.ok)
        self.assertFalse(response.result["online"])
        communication = await self.core.process_manager._probe_communication(
            "vision_server", self.core.settings.processes["vision"])
        self.assertTrue(communication["online"])
        self.assertFalse(communication["response"]["online"])

    async def test_actual_vision_rejects_missing_extra_or_noninteger_params(self):
        valid = {"recipe_id": 3, "point_id": 25, "command_seq": 105}
        for invalid in (
            {"recipe_id": 3, "point_id": 25},
            {**valid, "extra": 1},
            {**valid, "recipe_id": True},
            {**valid, "command_seq": -1},
        ):
            response = await send_json_request("127.0.0.1", self.server.server_address[1], "vision.result", invalid, 0.5)
            self.assertFalse(response.ok)
            self.assertEqual(response.error["code"], "INVALID_PARAMS")
