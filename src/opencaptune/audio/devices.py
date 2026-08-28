"""Finding and naming CoreAudio devices."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    input_channels: int
    output_channels: int
    default_sample_rate: float
    host_api: str

    @property
    def is_input(self) -> bool:
        return self.input_channels > 0

    @property
    def is_output(self) -> bool:
        return self.output_channels > 0


def devices() -> list[Device]:
    import sounddevice  # noqa: PLC0415 - importing PortAudio is slow and not always wanted

    host_apis = sounddevice.query_hostapis()
    return [
        Device(
            index=index,
            name=entry["name"],
            input_channels=entry["max_input_channels"],
            output_channels=entry["max_output_channels"],
            default_sample_rate=entry["default_samplerate"],
            host_api=host_apis[entry["hostapi"]]["name"],
        )
        for index, entry in enumerate(sounddevice.query_devices())
    ]


def resolve(wanted: str | int, kind: str) -> Device:
    """Find a device by index, exact name, or unambiguous partial name.

    ``kind`` is "input" or "output", and narrows the search to devices that can
    actually do that — which is what makes a bare name like "BlackHole" usable
    for both ends of a loop without ambiguity.
    """
    if kind not in ("input", "output"):
        raise ValueError("kind must be 'input' or 'output'")

    candidates = [d for d in devices() if (d.is_input if kind == "input" else d.is_output)]
    if not candidates:
        raise LookupError(f"no {kind} devices are available")

    if isinstance(wanted, int) or (isinstance(wanted, str) and wanted.isdigit()):
        index = int(wanted)
        for device in candidates:
            if device.index == index:
                return device
        raise LookupError(f"device {index} is not an {kind} device")

    exact = [d for d in candidates if d.name == wanted]
    if len(exact) == 1:
        return exact[0]

    partial = [d for d in candidates if wanted.lower() in d.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(f"{d.name!r} ({d.index})" for d in partial)
        raise LookupError(f"{wanted!r} matches several {kind} devices: {names}")

    available = ", ".join(repr(d.name) for d in candidates)
    raise LookupError(f"no {kind} device matches {wanted!r}; available: {available}")
