from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _set_parent_death_signal(expected_parent: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM) != 0:  # PR_SET_PDEATHSIG
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != expected_parent:
        os.kill(os.getpid(), signal.SIGTERM)


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        return 2

    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    expected_parent = int(sys.argv[2]) if len(sys.argv) == 3 else os.getppid()
    _set_parent_death_signal(expected_parent)
    if stopping:
        return 1

    script = Path(sys.argv[1]).resolve()
    child = subprocess.Popen(
        ["/bin/bash", str(script)],
        start_new_session=True,
    )

    while child.poll() is None and not stopping:
        time.sleep(0.1)

    if not stopping:
        return int(child.returncode or 0)

    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return int(child.poll() or 0)

    try:
        return int(child.wait(timeout=5.0))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return int(child.wait())


if __name__ == "__main__":
    raise SystemExit(main())
