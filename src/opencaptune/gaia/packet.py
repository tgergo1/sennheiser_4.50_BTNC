"""Encoding and decoding of GAIA frames as carried over BR/EDR RFCOMM.

Wire format (all multi-byte fields big-endian)::

    0        1         2        3        4      5      6      7      8         len+8
    +--------+---------+--------+--------+------+------+------+------+ ...... +--------+
    |  SOF   | VERSION | FLAGS  | LENGTH |  VENDOR ID  | COMMAND ID  | PAYLOAD | CHECK |
    +--------+---------+--------+--------+------+------+------+------+ ...... +--------+

SOF is always 0xFF.  Bit 0 of FLAGS marks the presence of the trailing checksum
byte, which is a plain XOR over every preceding byte of the frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as c

SOF = 0xFF
PROTOCOL_VERSION = 0x01
FLAG_CHECKSUM = 0x01
HEADER_LENGTH = 8
MAX_PAYLOAD = 254
MAX_PACKET = 270


class GaiaFrameError(ValueError):
    """Raised when a byte string is not a well-formed GAIA frame."""


@dataclass
class GaiaPacket:
    command_id: int
    payload: bytes = b""
    vendor_id: int = c.VENDOR_QUALCOMM
    checksum: bool = False

    @property
    def is_acknowledgement(self) -> bool:
        return bool(self.command_id & c.ACK_MASK)

    @property
    def command(self) -> int:
        """The command ID with the acknowledgement bit stripped."""
        return self.command_id & c.COMMAND_MASK

    @property
    def status(self) -> int | None:
        """Status byte of an acknowledgement, or None for a request."""
        if not self.is_acknowledgement or not self.payload:
            return None
        return self.payload[0]

    @property
    def status_name(self) -> str | None:
        status = self.status
        if status is None:
            return None
        return c.STATUS_NAMES.get(status, f"UNKNOWN_0x{status:02X}")

    def encode(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD:
            raise GaiaFrameError(
                f"payload of {len(self.payload)} bytes exceeds the {MAX_PAYLOAD} byte maximum"
            )
        frame = bytes(
            [
                SOF,
                PROTOCOL_VERSION,
                FLAG_CHECKSUM if self.checksum else 0x00,
                len(self.payload),
                (self.vendor_id >> 8) & 0xFF,
                self.vendor_id & 0xFF,
                (self.command_id >> 8) & 0xFF,
                self.command_id & 0xFF,
            ]
        ) + self.payload
        if self.checksum:
            frame += bytes([xor_checksum(frame)])
        return frame

    def __str__(self) -> str:
        text = f"{c.command_name(self.command_id)} vendor=0x{self.vendor_id:04X}"
        if self.is_acknowledgement:
            text += f" status={self.status_name}"
        if self.payload:
            text += f" payload={self.payload.hex()}"
        return text


def xor_checksum(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def decode(frame: bytes) -> GaiaPacket:
    """Decode exactly one complete frame."""
    packet, consumed = _decode_one(frame)
    if consumed != len(frame):
        raise GaiaFrameError(
            f"{len(frame) - consumed} trailing bytes after the frame; use extract() for streams"
        )
    return packet


def extract(buffer: bytes) -> tuple[list[GaiaPacket], bytes]:
    """Pull every complete frame out of a stream buffer.

    Returns the packets decoded so far and whatever incomplete tail is left,
    which the caller should prepend to the next chunk of received bytes.
    """
    packets: list[GaiaPacket] = []
    offset = 0
    while offset < len(buffer):
        # Resynchronise: anything before a start-of-frame byte is noise.
        if buffer[offset] != SOF:
            next_sof = buffer.find(bytes([SOF]), offset + 1)
            if next_sof < 0:
                return packets, b""
            offset = next_sof
            continue
        try:
            packet, consumed = _decode_one(buffer[offset:])
        except _Incomplete:
            break
        except GaiaFrameError:
            offset += 1
            continue
        packets.append(packet)
        offset += consumed
    return packets, buffer[offset:]


class _Incomplete(Exception):
    """Internal: the buffer holds only part of a frame."""


def _decode_one(data: bytes) -> tuple[GaiaPacket, int]:
    if len(data) < HEADER_LENGTH:
        raise _Incomplete
    if data[0] != SOF:
        raise GaiaFrameError(f"expected SOF 0xFF, found 0x{data[0]:02X}")
    flags = data[2]
    payload_length = data[3]
    has_checksum = bool(flags & FLAG_CHECKSUM)
    total = HEADER_LENGTH + payload_length + (1 if has_checksum else 0)
    if len(data) < total:
        raise _Incomplete
    if has_checksum:
        expected = xor_checksum(data[: total - 1])
        if data[total - 1] != expected:
            raise GaiaFrameError(
                f"checksum mismatch: frame carries 0x{data[total - 1]:02X}, computed 0x{expected:02X}"
            )
    packet = GaiaPacket(
        command_id=(data[6] << 8) | data[7],
        payload=bytes(data[HEADER_LENGTH : HEADER_LENGTH + payload_length]),
        vendor_id=(data[4] << 8) | data[5],
        checksum=has_checksum,
    )
    return packet, total
