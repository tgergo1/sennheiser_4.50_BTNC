"""The equaliser daemon, executed inside the macOS helper bundle.

Invoked as ``python -m opencaptune._audio_helper <config.json>``.  Audio input
needs microphone permission, which macOS only grants to a bundled,
LaunchServices-launched process — see ``opencaptune.hostapp``.

Because that launch detaches stdio, the daemon is controlled through a Unix
socket carrying newline-delimited JSON, and reports anything interesting to a
log file beside it.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import traceback
from pathlib import Path

from .audio.engine import Engine, EngineConfig


def _log(path: Path, message: str) -> None:
    with path.open("a") as handle:
        handle.write(message.rstrip() + "\n")


def _serve(engine: Engine, socket_path: Path, log: Path) -> None:
    if len(str(socket_path)) > 100:
        raise ValueError(f"socket path is too long for AF_UNIX: {socket_path}")
    if socket_path.exists():
        socket_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(4)
    _log(log, f"listening on {socket_path}")

    try:
        while True:
            connection, _ = server.accept()
            with connection:
                payload = connection.makefile("rwb")
                line = payload.readline()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                    action = request.get("action")
                    if action == "status":
                        response = {"ok": True, **engine.status()}
                    elif action == "set_preset":
                        engine.set_preset(
                            request["preset"],
                            bass=request.get("bass", 0),
                            treble=request.get("treble", 0),
                        )
                        response = {"ok": True, **engine.status()}
                    elif action == "set_crossfeed":
                        engine.set_crossfeed(int(request["crossfeed"]))
                        response = {"ok": True, **engine.status()}
                    elif action == "set_loudness":
                        phon = request.get("phon")
                        engine.set_loudness(float(phon) if phon is not None else None)
                        response = {"ok": True, **engine.status()}
                    elif action == "set_calibration":
                        engine.set_calibration(request.get("calibration"))
                        response = {"ok": True, **engine.status()}
                    elif action == "reset_stats":
                        engine.reset_stats()
                        response = {"ok": True, **engine.status()}
                    elif action == "stop":
                        response = {"ok": True, "stopping": True}
                    else:
                        response = {"ok": False, "error": f"unknown action {action!r}"}
                except Exception as error:  # noqa: BLE001 - report, never die
                    response = {"ok": False, "error": f"{type(error).__name__}: {error}"}
                    _log(log, traceback.format_exc())

                payload.write((json.dumps(response) + "\n").encode())
                payload.flush()

                if response.get("stopping"):
                    _log(log, "stopping on request")
                    return
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    config_file = Path(argv[1])
    settings = json.loads(config_file.read_text())
    run_dir = Path(settings.pop("run_dir"))
    socket_path = Path(settings.pop("socket_path"))
    log = run_dir / "daemon.log"
    ready = run_dir / "ready.json"

    engine = None
    try:
        engine = Engine(EngineConfig(**settings))
        engine.start()
        _log(log, f"started: {json.dumps(engine.status())}")
        ready.write_text(json.dumps({"ok": True, "pid": os.getpid(), **engine.status()}))
        _serve(engine, socket_path, log)
    except Exception as error:  # noqa: BLE001 - the caller reads this back
        _log(log, traceback.format_exc())
        ready.write_text(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
        return 1
    finally:
        if engine is not None:
            engine.stop()
        _log(log, "exited")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
