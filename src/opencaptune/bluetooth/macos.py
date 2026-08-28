"""Device inspection through IOBluetooth and CoreBluetooth.

Everything here must run inside the helper bundle — see ``opencaptune.hostapp``.
Calling any of it from a bare interpreter aborts the process.
"""

from __future__ import annotations

import time

from . import uuids as uuid_table

# IOReturn codes seen when opening RFCOMM channels.
K_IO_RETURN_SUCCESS = 0x00000000

_SDP_TYPE_UNSIGNED_INT = 1
_SDP_TYPE_UUID = 3
_SDP_TYPE_STRING = 4
_SDP_TYPE_SEQUENCE = 6
_SDP_TYPE_ALTERNATIVE = 7


def _frameworks():
    import IOBluetooth  # noqa: PLC0415 - deliberately lazy, see module docstring
    from Foundation import NSDate, NSRunLoop  # noqa: PLC0415

    return IOBluetooth, NSRunLoop, NSDate


def _pump(seconds: float) -> None:
    """Run the current run loop so IOBluetooth delegates get a chance to fire."""
    _, NSRunLoop, NSDate = _frameworks()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.05)
        )


def _element_to_python(element):
    """Convert an IOBluetoothSDPDataElement into plain Python."""
    if element is None:
        return None
    kind = element.getTypeDescriptor()
    if kind == _SDP_TYPE_UUID:
        value = element.getUUIDValue()
        return {"uuid": bytes(value).hex()} if value is not None else None
    if kind in (_SDP_TYPE_SEQUENCE, _SDP_TYPE_ALTERNATIVE):
        return [_element_to_python(child) for child in (element.getArrayValue() or [])]
    if kind == _SDP_TYPE_STRING:
        return element.getStringValue()
    number = element.getNumberValue()
    return int(number) if number is not None else None


def _collect_uuids(value, found: list[str]) -> None:
    if isinstance(value, dict) and "uuid" in value:
        found.append(value["uuid"])
    elif isinstance(value, list):
        for item in value:
            _collect_uuids(item, found)


def find_device(address: str):
    IOBluetooth, _, _ = _frameworks()
    normalised = address.replace(":", "-").lower()
    device = IOBluetooth.IOBluetoothDevice.deviceWithAddressString_(normalised)
    if device is None:
        raise LookupError(f"no Bluetooth device with address {address}")
    return device


def list_devices() -> list[dict]:
    """Every device macOS has paired, newest pairing first."""
    IOBluetooth, _, _ = _frameworks()
    devices = IOBluetooth.IOBluetoothDevice.pairedDevices() or []
    return [
        {
            "name": device.name(),
            "address": device.addressString(),
            "connected": bool(device.isConnected()),
            "class_of_device": int(device.classOfDevice()),
            "major_class": int(device.deviceClassMajor()),
            "minor_class": int(device.deviceClassMinor()),
        }
        for device in devices
    ]


def sdp_records(address: str, refresh: bool = True, timeout: float = 8.0) -> list[dict]:
    """Browse the device's SDP database."""
    device = find_device(address)
    if refresh:
        device.performSDPQuery_(None)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not device.services():
            _pump(0.2)

    records = []
    for record in device.services() or []:
        result, channel = record.getRFCOMMChannelID_(None)
        attributes = record.attributes() or {}
        decoded = {int(key): _element_to_python(value) for key, value in attributes.items()}
        service_classes: list[str] = []
        _collect_uuids(decoded.get(0x0001), service_classes)
        records.append(
            {
                "service_name": record.getServiceName(),
                "rfcomm_channel": int(channel) if result == K_IO_RETURN_SUCCESS else None,
                "service_classes": [
                    {"uuid": value, "name": uuid_table.describe(value)}
                    for value in service_classes
                ],
                "attributes": {f"0x{key:04X}": value for key, value in sorted(decoded.items())},
            }
        )
    return records


def rfcomm_sweep(address: str, channels: range, dwell: float = 1.0) -> list[dict]:
    """Attempt to open each RFCOMM channel in turn.

    This is how you find a control channel that SDP does not advertise.  Note
    that macOS frequently refuses these opens outright — a uniform failure
    across every channel means the sweep was blocked, not that the channels are
    absent.  Linux gives a trustworthy answer here; macOS often does not.
    """
    import objc  # noqa: PLC0415

    device = find_device(address)
    protocol = objc.protocolNamed("IOBluetoothRFCOMMChannelDelegate")
    from Foundation import NSObject  # noqa: PLC0415

    received: dict[int, bytearray] = {}

    class _Delegate(NSObject, protocols=[protocol]):
        def rfcommChannelData_data_length_(self, channel, data, length):
            received.setdefault(int(channel.getChannelID()), bytearray()).extend(
                bytes(data[:length])
            )

        def rfcommChannelClosed_(self, channel):
            pass

    delegate = _Delegate.alloc().init()

    results = []
    for number in channels:
        result, channel = device.openRFCOMMChannelSync_withChannelID_delegate_(
            None, number, delegate
        )
        code = result & 0xFFFFFFFF
        opened = bool(channel is not None and channel.isOpen())
        if channel is not None and not opened and code == K_IO_RETURN_SUCCESS:
            _pump(dwell)
            opened = bool(channel.isOpen())
        entry = {"channel": number, "io_return": f"0x{code:08X}", "opened": opened}
        if opened:
            entry["mtu"] = int(channel.getMTU())
            entry["received"] = bytes(received.get(number, b"")).hex()
        if channel is not None:
            channel.closeChannel()
        results.append(entry)
    return results


def ble_scan(seconds: float = 10.0) -> list[dict]:
    """Passively scan for BLE advertisements."""
    import CoreBluetooth  # noqa: PLC0415
    from Foundation import NSObject  # noqa: PLC0415

    seen: dict[str, dict] = {}

    class _Central(NSObject):
        def centralManagerDidUpdateState_(self, manager):
            if manager.state() == CoreBluetooth.CBManagerStatePoweredOn:
                manager.scanForPeripheralsWithServices_options_(None, None)

        def centralManager_didDiscoverPeripheral_advertisementData_RSSI_(
            self, manager, peripheral, advertisement, rssi
        ):
            key = str(peripheral.identifier())
            if key in seen:
                return
            services = advertisement.get("kCBAdvDataServiceUUIDs") or []
            manufacturer = advertisement.get("kCBAdvDataManufacturerData")
            seen[key] = {
                "identifier": key,
                "name": advertisement.get("kCBAdvDataLocalName") or peripheral.name(),
                "rssi": int(rssi),
                "services": [str(service) for service in services],
                "manufacturer_data": bytes(manufacturer).hex() if manufacturer else None,
            }

    delegate = _Central.alloc().init()
    # Held in a local: dropping the manager stops the scan immediately.
    manager = CoreBluetooth.CBCentralManager.alloc().initWithDelegate_queue_(delegate, None)
    _pump(seconds)
    manager.stopScan()
    return list(seen.values())
