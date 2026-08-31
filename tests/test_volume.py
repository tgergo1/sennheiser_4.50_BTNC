import pytest

from opencaptune.audio import volume


@pytest.fixture
def writes(monkeypatch):
    """Record every element written, with no master control present."""
    recorded = []

    def fake_write(device, element, value):
        if element == volume.MASTER_ELEMENT:
            return False
        recorded.append((element, value))
        return True

    monkeypatch.setattr(volume, "_write_float", fake_write)
    return recorded


def test_setting_volume_writes_every_channel(writes):
    # Regression: any() over a generator stopped after the first channel,
    # leaving the other one where it was — one ear quieter than the other.
    assert volume.set_volume(7, 1.0)
    assert writes == [(1, 1.0), (2, 1.0)]


def test_volume_is_clamped_to_the_valid_range(writes):
    volume.set_volume(7, 5.0)
    volume.set_volume(7, -3.0)
    assert [value for _, value in writes] == [1.0, 1.0, 0.0, 0.0]


def test_a_master_control_is_used_alone_when_present(monkeypatch):
    recorded = []

    def fake_write(device, element, value):
        recorded.append(element)
        return True

    monkeypatch.setattr(volume, "_write_float", fake_write)
    assert volume.set_volume(7, 0.5)
    assert recorded == [volume.MASTER_ELEMENT]


def test_a_device_with_no_writable_control_reports_failure(monkeypatch):
    monkeypatch.setattr(volume, "_write_float", lambda *a: False)
    assert not volume.set_volume(7, 1.0)


def test_reading_prefers_the_master_control(monkeypatch):
    monkeypatch.setattr(volume, "_read_float",
                        lambda d, e: 0.4 if e == volume.MASTER_ELEMENT else 0.9)
    assert volume.get_volume(7) == pytest.approx(0.4)


def test_reading_averages_the_channels_when_there_is_no_master(monkeypatch):
    values = {1: 0.5, 2: 0.7}
    monkeypatch.setattr(volume, "_read_float", lambda d, e: values.get(e))
    assert volume.get_volume(7) == pytest.approx(0.6)


def test_a_device_with_no_volume_at_all_reads_as_none(monkeypatch):
    monkeypatch.setattr(volume, "_read_float", lambda d, e: None)
    assert volume.get_volume(7) is None