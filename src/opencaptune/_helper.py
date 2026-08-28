"""Entry point executed inside the macOS helper bundle.

Invoked as ``python -m opencaptune._helper <request.json> <response.json>``.
Nothing here writes to stdout: LaunchServices detaches it, so the response file
is the only channel back to the caller.
"""

from __future__ import annotations

import json
import sys
import traceback


def _dispatch(request: dict) -> dict:
    from .bluetooth import macos  # noqa: PLC0415 - must not import before the bundle runs

    action = request.get("action")
    if action == "list_devices":
        return {"devices": macos.list_devices()}
    if action == "sdp":
        return {"records": macos.sdp_records(request["address"], refresh=request.get("refresh", True))}
    if action == "ble_scan":
        return {"peripherals": macos.ble_scan(request.get("seconds", 10.0))}
    if action == "rfcomm_sweep":
        channels = range(request.get("first", 1), request.get("last", 30) + 1)
        return {"channels": macos.rfcomm_sweep(request["address"], channels)}
    if action == "survey":
        address = request["address"]
        result = {
            "records": macos.sdp_records(address),
            "peripherals": macos.ble_scan(request.get("seconds", 10.0)),
        }
        if request.get("rfcomm_sweep"):
            result["channels"] = macos.rfcomm_sweep(
                address, range(request.get("first", 1), request.get("last", 30) + 1)
            )
        return result
    raise ValueError(f"unknown action {action!r}")


def main(argv: list[str]) -> int:
    request_file, response_file = argv[1], argv[2]
    try:
        response = _dispatch(json.loads(open(request_file).read()))
    except Exception as error:  # noqa: BLE001 - the traceback is the payload
        response = {"error": f"{type(error).__name__}: {error}", "traceback": traceback.format_exc()}
    with open(response_file, "w") as handle:
        json.dump(response, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
