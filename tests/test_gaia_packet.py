import pytest

from opencaptune.gaia import constants as c
from opencaptune.gaia import packet as p


def test_encodes_a_request_with_no_payload():
    frame = p.GaiaPacket(c.COMMAND_GET_API_VERSION).encode()
    assert frame == bytes([0xFF, 0x01, 0x00, 0x00, 0x00, 0x0A, 0x03, 0x00])


def test_encodes_vendor_and_payload_big_endian():
    frame = p.GaiaPacket(c.COMMAND_SET_EQ_CONTROL, b"\x03", vendor_id=0x1234).encode()
    assert frame == bytes([0xFF, 0x01, 0x00, 0x01, 0x12, 0x34, 0x02, 0x14, 0x03])


def test_checksum_is_an_xor_over_the_whole_frame():
    frame = p.GaiaPacket(c.COMMAND_GET_API_VERSION, checksum=True).encode()
    assert frame[2] & p.FLAG_CHECKSUM
    assert frame[-1] == p.xor_checksum(frame[:-1])
    assert p.decode(frame).command == c.COMMAND_GET_API_VERSION


def test_round_trips():
    original = p.GaiaPacket(c.COMMAND_SET_EQ_PARAMETER, b"\x01\x12\x00\x64\x01")
    decoded = p.decode(original.encode())
    assert decoded == original


def test_acknowledgement_exposes_status_and_base_command():
    ack = p.decode(
        p.GaiaPacket(c.COMMAND_GET_API_VERSION | c.ACK_MASK, b"\x00\x01\x02").encode()
    )
    assert ack.is_acknowledgement
    assert ack.command == c.COMMAND_GET_API_VERSION
    assert ack.status == c.STATUS_SUCCESS
    assert ack.status_name == "SUCCESS"


def test_request_has_no_status():
    assert p.GaiaPacket(c.COMMAND_GET_API_VERSION, b"\x00").status is None


def test_rejects_a_bad_checksum():
    frame = bytearray(p.GaiaPacket(c.COMMAND_GET_API_VERSION, checksum=True).encode())
    frame[-1] ^= 0xFF
    with pytest.raises(p.GaiaFrameError, match="checksum mismatch"):
        p.decode(bytes(frame))


def test_rejects_a_missing_start_of_frame():
    with pytest.raises(p.GaiaFrameError, match="expected SOF"):
        p.decode(b"\x00\x01\x00\x00\x00\x0a\x03\x00")


def test_rejects_an_oversized_payload():
    with pytest.raises(p.GaiaFrameError, match="exceeds"):
        p.GaiaPacket(c.COMMAND_GET_API_VERSION, b"\x00" * 255).encode()


def test_extract_splits_a_stream_and_keeps_the_remainder():
    first = p.GaiaPacket(c.COMMAND_GET_API_VERSION).encode()
    second = p.GaiaPacket(c.COMMAND_GET_CURRENT_BATTERY_LEVEL, b"\x01\x02").encode()
    packets, rest = p.extract(first + second[:4])
    assert [packet.command for packet in packets] == [c.COMMAND_GET_API_VERSION]
    assert rest == second[:4]

    packets, rest = p.extract(rest + second[4:])
    assert [packet.command for packet in packets] == [c.COMMAND_GET_CURRENT_BATTERY_LEVEL]
    assert rest == b""


def test_extract_resynchronises_after_leading_noise():
    frame = p.GaiaPacket(c.COMMAND_GET_API_VERSION).encode()
    packets, rest = p.extract(b"\x11\x22\x33" + frame)
    assert [packet.command for packet in packets] == [c.COMMAND_GET_API_VERSION]
    assert rest == b""


def test_command_name_marks_acknowledgements():
    assert c.command_name(c.COMMAND_GET_API_VERSION) == "COMMAND_GET_API_VERSION"
    assert c.command_name(c.COMMAND_GET_API_VERSION | c.ACK_MASK) == "COMMAND_GET_API_VERSION (ACK)"
    assert c.command_name(0x0777) == "UNKNOWN_0x0777"
