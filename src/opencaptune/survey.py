"""Decide whether a headphone has an app-facing control channel at all.

The question this answers is the one that decides whether *any* companion app
can configure a given headphone: does it advertise a vendor control service?
If it does not, no amount of protocol work will help, because there is nothing
listening.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from .bluetooth import uuids as uuid_table
from .hostapp import run_helper


@dataclass
class Survey:
    address: str
    records: list[dict] = field(default_factory=list)
    peripherals: list[dict] = field(default_factory=list)
    channels: list[dict] = field(default_factory=list)

    @property
    def advertised_services(self) -> list[dict]:
        services = {}
        for record in self.records:
            for service in record["service_classes"]:
                services[service["uuid"]] = service
        return sorted(services.values(), key=lambda service: service["uuid"])

    @property
    def vendor_control_services(self) -> list[dict]:
        return [
            service
            for service in self.advertised_services
            if uuid_table.is_vendor_control(service["uuid"])
        ]

    @property
    def has_serial_port(self) -> bool:
        return any(
            uuid_table.short_uuid(service["uuid"]) == 0x1101
            for service in self.advertised_services
        )

    @property
    def ble_control_peripherals(self) -> list[dict]:
        matches = []
        for peripheral in self.peripherals:
            for service in peripheral["services"]:
                if uuid_table.is_vendor_control(service.replace("-", "")):
                    matches.append(peripheral)
                    break
        return matches

    @property
    def sweep_was_blocked(self) -> bool:
        """True when every open failed identically, which means nothing was learnt."""
        if not self.channels:
            return False
        if any(entry["opened"] for entry in self.channels):
            return False
        return len({entry["io_return"] for entry in self.channels}) == 1

    @property
    def verdict(self) -> str:
        if self.vendor_control_services:
            names = ", ".join(s["name"] for s in self.vendor_control_services)
            return f"Vendor control service present: {names}"
        if self.ble_control_peripherals:
            return "Vendor control service present over BLE"
        if self.has_serial_port:
            return (
                "No known vendor service, but a Serial Port service is advertised — "
                "worth probing, the protocol may simply be unrecognised"
            )
        return (
            "No control channel. This device advertises only standard audio "
            "profiles, so it cannot be configured over Bluetooth by any app"
        )


def survey(address: str, ble_seconds: float = 10.0, rfcomm_sweep: bool = False) -> Survey:
    if sys.platform != "darwin":
        raise NotImplementedError(
            "only the macOS backend is implemented; on Linux use `sdptool browse`"
        )
    response = run_helper(
        {
            "action": "survey",
            "address": address,
            "seconds": ble_seconds,
            "rfcomm_sweep": rfcomm_sweep,
        }
    )
    return Survey(
        address=address,
        records=response.get("records", []),
        peripherals=response.get("peripherals", []),
        channels=response.get("channels", []),
    )
