import pytest
from pydantic import ValidationError
from backend.schemas import EditParamsModel, ImportPublishRequest


def test_defaults():
    p = EditParamsModel()
    assert p.rotate_quarter == 0 and p.bg == "white" and p.brightness == 1.0


def test_rejects_out_of_range_brightness():
    with pytest.raises(ValidationError):
        EditParamsModel(brightness=3.0)


def test_rejects_bad_rotate_quarter():
    with pytest.raises(ValidationError):
        EditParamsModel(rotate_quarter=7)


def test_publish_request_parses_nested_params():
    req = ImportPublishRequest(productid="P1", file_id="f1",
                               params={"bg": "cream", "shadow": True})
    assert req.params.bg == "cream" and req.params.shadow is True
