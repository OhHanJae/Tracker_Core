from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from src.common.settings import DEFAULT_CONFIG_PATH
from src.core.application import CoreApplication
from src.core.web_application import CoreWebApplication


def _windows_connection_reset_handler(
    loop: asyncio.AbstractEventLoop,
    context: dict[str, object],
) -> None:
    exception = context.get("exception")
    message = str(context.get("message") or "")
    if (
        isinstance(exception, ConnectionResetError)
        and getattr(exception, "winerror", None) == 10054
        and "_call_connection_lost" in message
    ):
        return
    loop.default_exception_handler(context)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tracker Process Control Core")
    parser.add_argument(
        "--worker", action="store_true",
        help="Run the managed control worker without the management web server",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to global core settings JSON",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Console log level",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings_path = Path(args.config).expanduser()
    app = CoreApplication(settings_path, managed_worker=True) if args.worker else CoreWebApplication(settings_path)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_windows_connection_reset_handler)
    for signal_name in ("SIGINT", "SIGTERM"):
        stop_signal = getattr(signal, signal_name, None)
        if stop_signal is None:
            continue
        try:
            loop.add_signal_handler(stop_signal, stop_event.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

    try:
        await app.start()
        signal_wait = asyncio.create_task(stop_event.wait())
        app_wait = asyncio.create_task(app.wait_closed())
        try:
            await asyncio.wait((signal_wait, app_wait), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (signal_wait, app_wait):
                task.cancel()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await app.stop()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
