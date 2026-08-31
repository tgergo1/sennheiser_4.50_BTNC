import json

import pytest

from opencaptune.bluetooth import headset

REPORT = {
    "SPBluetoothDataType": [
        {
            "device_connected": [
                {"Bogcifüles": {
                    "device_address": "00:16:94:41:89:D8",
                    "device_batteryLevelMain": "85%",
                    "device_vendorID": "0x0A12",
                    "device_firmwareVersion": "0.0.0",
                }},
                {"Some Speaker": {"device_address": "AA:BB:CC:DD:EE:FF"}},
            ]
        }
    ]
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(headset, "_bluetooth_report", lambda: REPORT)


def test_connected_headsets_are_listed_with_what_they_report():
    found = headset.connected_headsets()
    assert [e["name"] for e in found] == ["Bogcifüles", "Some Speaker"]
    assert found[0]["battery"] == 85
    assert found[0]["vendor_id"] == "0x0A12"


def test_a_device_that_reports_no_battery_reads_as_none():
    assert headset.battery_percent("Some Speaker") is None


def test_battery_by_name():
    assert headset.battery_percent("Bogcifüles") == 85


def test_an_unknown_device_is_none_rather_than_an_error():
    assert headset.find("Not Connected") is None
    assert headset.battery_percent("Not Connected") is None


def test_a_malformed_percentage_does_not_raise(monkeypatch):
    monkeypatch.setattr(headset, "_bluetooth_report", lambda: {
        "SPBluetoothDataType": [{"device_connected": [
            {"Odd": {"device_batteryLevelMain": "unknown"}}]}]})
    assert headset.battery_percent("Odd") is None


def test_no_bluetooth_report_at_all_is_survivable(monkeypatch):
    monkeypatch.setattr(headset, "_bluetooth_report", lambda: {})
    assert headset.connected_headsets() == []
