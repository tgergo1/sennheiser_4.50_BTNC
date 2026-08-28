"""The daemon's control protocol, exercised over a real Unix socket.

The engine is stubbed out so this needs no audio hardware; what is under test
is the request/response contract between `captune` and the daemon.
"""

import os
import threading
from pathlib import Path

import pytest

from opencaptune import _audio_helper, daemon
from opencaptune.hostapp import HostAppError


class StubEngine:
    def __init__(self):
        self.preset_calls = []

    def status(self):
        return {"running": True, "preset": "Neutral", "frames": 4096}

    def reset_stats(self):
        self.reset_calls = getattr(self, "reset_calls", 0) + 1

    def set_preset(self, name, bass=0, treble=0):
        if name == "Nonsense":
            raise KeyError("unknown preset 'Nonsense'")
        self.preset_calls.append((name, bass, treble))


@pytest.fixture
def control_socket(tmp_path, monkeypatch):
    # Short path: AF_UNIX names are capped near 104 bytes and tmp_path is long.
    path = Path("/tmp") / f"opencaptune-test-{os.getpid()}.sock"
    monkeypatch.setattr(daemon, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(daemon, "socket_path", lambda: path)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def running_daemon(control_socket, tmp_path):
    engine = StubEngine()
    thread = threading.Thread(
        target=_audio_helper._serve,
        args=(engine, control_socket, tmp_path / "daemon.log"),
        daemon=True,
    )
    thread.start()
    for _ in range(100):
        if control_socket.exists():
            break
        threading.Event().wait(0.02)
    yield engine
    if control_socket.exists():
        daemon.stop()
    thread.join(timeout=5)


def test_status_round_trips(running_daemon):
    assert daemon.is_running()
    assert daemon.status()["preset"] == "Neutral"


def test_set_preset_reaches_the_engine(running_daemon):
    daemon.set_preset("Rock", bass=40, treble=10)
    assert running_daemon.preset_calls == [("Rock", 40, 10)]


def test_an_engine_error_comes_back_as_a_failure_not_a_crash(running_daemon):
    with pytest.raises(HostAppError, match="Nonsense"):
        daemon.set_preset("Nonsense")
    # The daemon must survive a bad request.
    assert daemon.is_running()


def test_reset_stats_reaches_the_engine(running_daemon):
    daemon.reset_stats()
    assert running_daemon.reset_calls == 1


def test_stop_shuts_down_and_removes_the_socket(running_daemon, control_socket):
    daemon.stop()
    assert not control_socket.exists()
    assert not daemon.is_running()


def test_talking_to_a_daemon_that_is_not_running_is_a_clear_error(control_socket):
    assert not daemon.is_running()
    with pytest.raises(HostAppError, match="not running"):
        daemon.status()


def test_a_stale_socket_left_by_a_dead_daemon_is_cleaned_up(control_socket):
    control_socket.touch()
    with pytest.raises(HostAppError, match="not running"):
        daemon.status()
    assert not control_socket.exists()


def test_a_process_inside_the_bundle_never_rebuilds_it(tmp_path, monkeypatch):
    """Regression: rebuilding from inside deletes the caller's own executable.

    The menu bar app runs from the bundle, so clicking Start called
    ensure_bundle, which saw a stamp mismatch, removed the bundle, and then
    could not copy the interpreter back — because the interpreter it was told
    to copy was the one it had just deleted.
    """
    from opencaptune import hostapp

    bundle = tmp_path / "OpenCapTune.app"
    executable = bundle / "Contents" / "MacOS" / "OpenCapTune"
    executable.parent.mkdir(parents=True)
    executable.write_text("not really a binary")

    monkeypatch.setattr(hostapp, "bundle_path", lambda: bundle)
    monkeypatch.setattr(hostapp.sys, "executable", str(executable))
    assert hostapp.running_from_bundle()

    assert hostapp.ensure_bundle() == bundle
    assert executable.exists(), "ensure_bundle deleted the running executable"

    # Even asked to force, it must not saw off the branch it is sitting on.
    assert hostapp.ensure_bundle(force=True) == bundle
    assert executable.exists()


def test_a_process_outside_the_bundle_is_free_to_rebuild(tmp_path, monkeypatch):
    from opencaptune import hostapp

    bundle = tmp_path / "OpenCapTune.app"
    monkeypatch.setattr(hostapp, "bundle_path", lambda: bundle)
    monkeypatch.setattr(hostapp.sys, "executable", "/usr/bin/python3")
    assert not hostapp.running_from_bundle()
