from __future__ import annotations

from src.pt_motor.motor_control import MotorApiClient


class LaserCommandClient:
    def __init__(self, motor_api: MotorApiClient) -> None:
        self.motor_api = motor_api

    async def off(self) -> None:
        await self.motor_api.laser_off()
