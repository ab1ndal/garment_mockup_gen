import pytest

from mockup_generator.timing import format_timings, timed


def test_timed_records_stage_in_ms():
    t = {}
    with timed(t, "stage"):
        pass
    assert isinstance(t["stage"], int) and t["stage"] >= 0


def test_timed_records_even_when_stage_raises():
    t = {}
    with pytest.raises(ValueError), timed(t, "boom"):
        raise ValueError
    assert "boom" in t


def test_format_timings_keeps_recording_order():
    assert format_timings({"b": 2, "a": 1}) == "b=2ms a=1ms"
