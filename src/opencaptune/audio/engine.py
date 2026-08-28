"""The always-on equaliser: read one device, filter, write another.

Paired with a virtual output device such as BlackHole this gives system-wide
equalisation.  macOS plays into the virtual device, this reads from it, and the
filtered result goes to the headphones.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field

from .. import eq
from .devices import Device, resolve
from .dsp import Equaliser


@dataclass
class EngineConfig:
    input_device: str | int
    output_device: str | int
    preset: str = "Neutral"
    calibration: str | None = None
    bass: int = 0
    treble: int = 0
    sample_rate: int | None = None
    block_size: int = 512
    channels: int = 2
    q: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Stats:
    frames: int = 0
    glitches: int = 0
    peak: float = 0.0


class Engine:
    """Streams audio from one device through the equaliser to another."""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.stats = Stats()
        self._stream = None
        self._lock = threading.Lock()

        self.input = resolve(config.input_device, "input")
        self.output = resolve(config.output_device, "output")
        if self.input.index == self.output.index:
            raise ValueError(
                f"input and output are the same device ({self.input.name!r}); "
                "the point of the loop is that they differ"
            )

        self.sample_rate = int(config.sample_rate or self.output.default_sample_rate)
        self.channels = min(config.channels, self.input.input_channels, self.output.output_channels)
        if self.channels < 1:
            raise ValueError("the chosen devices have no usable channels in common")

        self._equaliser = Equaliser(
            self._preset_from(config.preset, config.bass, config.treble),
            sample_rate=self.sample_rate,
            channels=self.channels,
            q=config.q,
            calibration=eq.calibration(config.calibration) if config.calibration else None,
        )

    @staticmethod
    def _preset_from(name: str, bass: int, treble: int) -> eq.Preset:
        return eq.preset(name).with_boosts(bass=bass, treble=treble)

    @property
    def preset(self) -> eq.Preset:
        return self._equaliser.preset

    @property
    def running(self) -> bool:
        return self._stream is not None and self._stream.active

    def reset_stats(self) -> None:
        """Clear the counters, e.g. after fixing a source of glitches."""
        self.stats = Stats()

    def set_calibration(self, name: str | None) -> None:
        """Change the headphone correction while streaming."""
        replacement = eq.calibration(name) if name else None
        with self._lock:
            self._equaliser.set_calibration(replacement)
        self.config.calibration = name

    def set_preset(self, name: str, bass: int = 0, treble: int = 0) -> None:
        """Change curve while streaming."""
        replacement = self._preset_from(name, bass, treble)
        with self._lock:
            self._equaliser.set_preset(replacement)
        self.config.preset, self.config.bass, self.config.treble = name, bass, treble

    def _callback(self, indata, outdata, frames, time_info, status) -> None:
        if status:
            self.stats.glitches += 1
        with self._lock:
            filtered = self._equaliser.process(indata)
        outdata[:] = filtered
        self.stats.frames += frames
        peak = float(abs(filtered).max()) if frames else 0.0
        if peak > self.stats.peak:
            self.stats.peak = peak

    def start(self) -> None:
        import sounddevice  # noqa: PLC0415

        if self.running:
            return
        self._stream = sounddevice.Stream(
            device=(self.input.index, self.output.index),
            samplerate=self.sample_rate,
            blocksize=self.config.block_size,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "input": self.input.name,
            "output": self.output.name,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "block_size": self.config.block_size,
            "preset": self.preset.name,
            "calibration": (
                self._equaliser.calibration.name if self._equaliser.calibration else None
            ),
            "preamp_db": round(self._equaliser.preamp_db, 2),
            "latency_ms": round(self.config.block_size / self.sample_rate * 1000, 1),
            "frames": self.stats.frames,
            "glitches": self.stats.glitches,
            "peak": round(self.stats.peak, 4),
        }
