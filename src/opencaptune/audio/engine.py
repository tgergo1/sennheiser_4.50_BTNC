"""The always-on equaliser: read one device, filter, write another.

Paired with a virtual output device such as BlackHole this gives system-wide
equalisation.  macOS plays into the virtual device, this reads from it, and the
filtered result goes to the headphones.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field

from .. import eq
from ..eq import loudness as loudness_module
from .crossfeed import Crossfeed
from . import volume as volume_control
from .devices import Device, resolve
from .dsp import Equaliser


@dataclass
class EngineConfig:
    input_device: str | int
    output_device: str | int
    preset: str = "Neutral"
    calibration: str | None = None
    loudness_phon: float | None = None
    reference_phon: float = loudness_module.DEFAULT_REFERENCE_PHON
    crossfeed: int = 0
    manage_volume: bool = True
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
        self._restore_volume: tuple[int, float] | None = None

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

        self._crossfeed = Crossfeed(
            strength=config.crossfeed, sample_rate=self.sample_rate, channels=self.channels
        )
        self._equaliser = Equaliser(
            self._preset_from(config.preset, config.bass, config.treble),
            sample_rate=self.sample_rate,
            channels=self.channels,
            q=config.q,
            calibration=eq.calibration(config.calibration) if config.calibration else None,
            loudness=self._loudness_filters(config.loudness_phon, config.reference_phon),
        )

    @staticmethod
    def _preset_from(name: str, bass: int, treble: int) -> eq.Preset:
        return eq.preset(name).with_boosts(bass=bass, treble=treble)

    @staticmethod
    def _loudness_filters(phon: float | None, reference: float):
        if phon is None:
            return ()
        return loudness_module.compensation(phon, reference)

    def set_loudness(self, phon: float | None) -> None:
        """Change the level compensation while streaming."""
        filters = self._loudness_filters(phon, self.config.reference_phon)
        with self._lock:
            self._equaliser.set_loudness(filters)
        self.config.loudness_phon = phon

    def set_crossfeed(self, strength: int) -> None:
        """Change the crossfeed strength while streaming."""
        with self._lock:
            self._crossfeed.set_strength(strength)
        self.config.crossfeed = strength

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
            filtered = self._equaliser.process(self._crossfeed.process(indata))
        outdata[:] = filtered
        self.stats.frames += frames
        peak = float(abs(filtered).max()) if frames else 0.0
        if peak > self.stats.peak:
            self.stats.peak = peak

    def _take_output_volume(self) -> None:
        """Put the output device at unity so nothing attenuates behind our back.

        Once the system output is the virtual device, the volume keys act on
        that and can no longer reach the real output device, whose own volume
        then silently scales everything we send.
        """
        if not self.config.manage_volume:
            return
        try:
            device = volume_control.find_output_device(self.output.name)
            if device is None:
                return
            current = volume_control.get_volume(device)
            if current is None or current >= 0.999:
                return
            if volume_control.set_volume(device, 1.0):
                self._restore_volume = (device, current)
        except Exception:  # noqa: BLE001 - never block playback over this
            self._restore_volume = None

    def _give_output_volume_back(self) -> None:
        if self._restore_volume is None:
            return
        device, previous = self._restore_volume
        try:
            volume_control.set_volume(device, previous)
        except Exception:  # noqa: BLE001
            pass
        self._restore_volume = None

    def start(self) -> None:
        import sounddevice  # noqa: PLC0415

        if self.running:
            return
        self._take_output_volume()
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
        self._give_output_volume_back()

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
            "loudness_phon": self.config.loudness_phon,
            "crossfeed": self._crossfeed.strength,
            "output_volume_raised_from": (
                round(self._restore_volume[1], 3) if self._restore_volume else None
            ),
            "preamp_db": round(self._equaliser.preamp_db, 2),
            "latency_ms": round(self.config.block_size / self.sample_rate * 1000, 1),
            "frames": self.stats.frames,
            "glitches": self.stats.glitches,
            "peak": round(self.stats.peak, 4),
        }
