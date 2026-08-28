"""Client side of the equaliser daemon."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from pathlib import Path

# Unix socket paths are capped near 104 bytes on macOS, and the application
# support directory is already most of that before a long username. Keep the
# socket somewhere short and put only the state alongside the logs.
MAX_SOCKET_PATH = 100

from .audio.engine import EngineConfig
from .hostapp import HostAppError, launch_detached, support_dir


def run_dir() -> Path:
    path = support_dir() / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def socket_path() -> Path:
    path = Path(tempfile.gettempdir()) / f"opencaptune-{os.getuid()}.sock"
    if len(str(path)) > MAX_SOCKET_PATH:
        path = Path("/tmp") / f"opencaptune-{os.getuid()}.sock"
    return path


def _request(payload: dict, timeout: float = 5.0) -> dict:
    path = socket_path()
    if not path.exists():
        raise HostAppError("the equaliser is not running")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
    except OSError as error:
        # Left behind by a daemon that died: a dead socket refuses the
        # connection, and a plain file is not a socket at all. Either way
        # nothing is listening, so clear it rather than failing here again.
        path.unlink(missing_ok=True)
        raise HostAppError("the equaliser is not running") from error
    with client:
        stream = client.makefile("rwb")
        stream.write((json.dumps(payload) + "\n").encode())
        stream.flush()
        line = stream.readline()
    if not line:
        raise HostAppError("the equaliser stopped responding")
    response = json.loads(line)
    if not response.get("ok"):
        raise HostAppError(response.get("error", "the equaliser reported a failure"))
    return response


def is_running() -> bool:
    try:
        _request({"action": "status"}, timeout=2.0)
        return True
    except (HostAppError, OSError, json.JSONDecodeError):
        return False


def status() -> dict:
    return _request({"action": "status"})


def set_preset(name: str, bass: int = 0, treble: int = 0) -> dict:
    return _request({"action": "set_preset", "preset": name, "bass": bass, "treble": treble})


def reset_stats() -> dict:
    return _request({"action": "reset_stats"})


def stop() -> None:
    _request({"action": "stop"})
    for _ in range(50):
        if not socket_path().exists():
            return
        time.sleep(0.1)


def start(config: EngineConfig, timeout: float = 20.0) -> dict:
    """Launch the daemon and wait for it to report that it is streaming."""
    if is_running():
        raise HostAppError("the equaliser is already running; stop it first")

    directory = run_dir()
    ready = directory / "ready.json"
    ready.unlink(missing_ok=True)
    socket_path().unlink(missing_ok=True)

    settings = config.as_dict()
    settings["run_dir"] = str(directory)
    settings["socket_path"] = str(socket_path())
    config_file = directory / "config.json"
    config_file.write_text(json.dumps(settings))

    launch_detached("opencaptune._audio_helper", [str(config_file)])

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready.exists():
            report = json.loads(ready.read_text())
            if not report.get("ok"):
                raise HostAppError(report.get("error", "the equaliser failed to start"))
            return report
        time.sleep(0.2)

    log = directory / "daemon.log"
    detail = log.read_text().strip().splitlines()[-1:] if log.exists() else []
    raise HostAppError(
        "the equaliser did not start within "
        f"{timeout:.0f}s" + (f": {detail[0]}" if detail else "")
    )
