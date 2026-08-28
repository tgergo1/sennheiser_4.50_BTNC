from opencaptune.bluetooth import uuids


def test_normalises_short_uuids_to_the_base_range():
    assert uuids.normalise("1101") == "0000110100001000800000805f9b34fb"
    assert uuids.normalise("00001101") == uuids.normalise("1101")


def test_recovers_the_short_form_only_inside_the_base_range():
    assert uuids.short_uuid(uuids.normalise("110b")) == 0x110B
    assert uuids.short_uuid(uuids.GAIA_RFCOMM) is None


def test_recognises_vendor_control_services():
    assert uuids.is_vendor_control(uuids.GAIA_RFCOMM)
    assert uuids.is_vendor_control("00001100-D102-11E1-9B23-00025B00A5A5")
    assert not uuids.is_vendor_control("110b")


def test_describes_known_and_unknown_uuids():
    assert uuids.describe(uuids.normalise("111e")) == "Handsfree"
    assert "GAIA" in uuids.describe(uuids.GAIA_RFCOMM)
    assert uuids.describe(uuids.normalise("1234")).startswith("Unknown assigned number")
