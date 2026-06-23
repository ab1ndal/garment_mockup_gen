import pytest
from mockup_generator.db.product_ids import product_key, parse_range


def test_key_three_and_four_digit_seq():
    assert product_key("BC25001") == 25_000_001
    assert product_key("BC251000") == 25_001_000
    assert product_key("BC26227") == 26_000_227


def test_key_orders_across_width_boundary():
    # lexically 'BC251000' < 'BC25999' (wrong); numerically it must be greater
    assert product_key("BC251000") > product_key("BC25999")


def test_key_malformed_returns_none():
    for bad in ["", "X1", "BC2", "BCAB123", "25001", None]:  # type: ignore[list-item]
        assert product_key(bad) is None


def test_parse_range_orders_low_high():
    assert parse_range("BC25001", "BC251000") == (25_000_001, 25_001_000)
    assert parse_range("BC251000", "BC25001") == (25_000_001, 25_001_000)


def test_parse_range_rejects_bad_endpoint():
    with pytest.raises(ValueError):
        parse_range("BC25001", "nope")
