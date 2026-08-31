import numpy as np
import pytest

from opencaptune.audio.engine import _Ring


def block(value, frames=4, channels=2):
    return np.full((frames, channels), value, dtype=np.float32)


def test_what_goes_in_comes_out_in_order():
    ring = _Ring(32, 2)
    ring.write(block(1.0))
    ring.write(block(2.0))
    assert np.all(ring.read(4) == 1.0)
    assert np.all(ring.read(4) == 2.0)


def test_reading_more_than_is_there_gives_silence_not_stale_audio():
    ring = _Ring(32, 2)
    ring.write(block(1.0, frames=4))
    out = ring.read(8)
    assert np.all(out[:4] == 1.0)
    assert np.all(out[4:] == 0.0)
    assert ring.starved == 4


def test_it_wraps_around_the_end_of_the_buffer():
    ring = _Ring(8, 2)
    ring.write(block(1.0, frames=6))
    assert np.all(ring.read(6) == 1.0)
    ring.write(block(2.0, frames=6))     # straddles the wrap point
    assert np.all(ring.read(6) == 2.0)


def test_overrunning_drops_the_oldest_audio_and_says_so():
    ring = _Ring(8, 2)
    ring.write(block(1.0, frames=8))
    ring.write(block(2.0, frames=4))
    assert ring.dropped == 4
    # The newest audio survives; the oldest is what went.
    out = ring.read(8)
    assert np.all(out[-4:] == 2.0)


def test_filled_tracks_what_is_waiting():
    ring = _Ring(32, 2)
    assert ring.filled == 0
    ring.write(block(1.0, frames=10))
    assert ring.filled == 10
    ring.read(4)
    assert ring.filled == 6


def test_output_is_float32_and_the_right_shape():
    ring = _Ring(32, 2)
    out = ring.read(7)
    assert out.shape == (7, 2)
    assert out.dtype == np.float32


def test_it_survives_being_hammered_from_two_threads():
    import threading

    ring = _Ring(512, 2)
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            ring.write(block(1.0, frames=64))

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(200):
            assert ring.read(64).shape == (64, 2)
    finally:
        stop.set()
        thread.join()
