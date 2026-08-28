"""Bluetooth service UUIDs, including the vendor ones this project cares about."""

# Vendor control services.  A device that offers one of these has an app-facing
# protocol; a device that offers neither cannot be configured over Bluetooth.
GAIA_RFCOMM = "00001107d10211e19b2300025b00a5a5"
GAIA_GATT = "00001100d10211e19b2300025b00a5a5"

VENDOR_CONTROL_SERVICES = {
    GAIA_RFCOMM: "GAIA (CSR/Qualcomm vendor control, RFCOMM)",
    GAIA_GATT: "GAIA (CSR/Qualcomm vendor control, BLE GATT)",
    "000000000000100080000002ee000002": "Airoha/RACE vendor control",
}

# The 16-bit assigned numbers a headset is likely to advertise.
ASSIGNED_NUMBERS = {
    0x1000: "Service Discovery Server",
    0x1101: "Serial Port (SPP)",
    0x1108: "Headset",
    0x110A: "Audio Source",
    0x110B: "Audio Sink",
    0x110C: "A/V Remote Control Target",
    0x110D: "Advanced Audio Distribution",
    0x110E: "A/V Remote Control",
    0x110F: "A/V Remote Control Controller",
    0x1112: "Headset Audio Gateway",
    0x111E: "Handsfree",
    0x111F: "Handsfree Audio Gateway",
    0x1131: "Headset - HS",
    0x1200: "PnP Information",
    0x1203: "Generic Audio",
}


def describe(uuid_hex: str) -> str:
    """Best-effort human name for a normalised (32 hex character) UUID."""
    uuid_hex = uuid_hex.lower()
    if uuid_hex in VENDOR_CONTROL_SERVICES:
        return VENDOR_CONTROL_SERVICES[uuid_hex]
    short = short_uuid(uuid_hex)
    if short is not None:
        return ASSIGNED_NUMBERS.get(short, f"Unknown assigned number 0x{short:04X}")
    return "Unknown vendor UUID"


def short_uuid(uuid_hex: str) -> int | None:
    """The 16-bit form of a UUID inside the Bluetooth base range, else None."""
    uuid_hex = uuid_hex.lower()
    if len(uuid_hex) == 4:
        return int(uuid_hex, 16)
    if len(uuid_hex) == 8:
        return int(uuid_hex[4:], 16) if uuid_hex.startswith("0000") else None
    if len(uuid_hex) == 32 and uuid_hex.endswith("00001000800000805f9b34fb"):
        return int(uuid_hex[4:8], 16)
    return None


def normalise(uuid_hex: str) -> str:
    """Expand a 16- or 32-bit UUID to its full 128-bit hex form."""
    uuid_hex = uuid_hex.lower().replace("-", "")
    if len(uuid_hex) == 4:
        uuid_hex = f"0000{uuid_hex}"
    if len(uuid_hex) == 8:
        uuid_hex += "00001000800000805f9b34fb"
    return uuid_hex


def is_vendor_control(uuid_hex: str) -> bool:
    return normalise(uuid_hex) in VENDOR_CONTROL_SERVICES
