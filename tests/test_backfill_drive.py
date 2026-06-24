import pytest

from mockup_generator.config import settings
from mockup_generator.integrations import drive_client


def test_generated_folder_id_default():
    assert settings.generated_mockups_folder_id == "1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4"


def test_drive_scope_is_read_write():
    assert "https://www.googleapis.com/auth/drive" in drive_client._SCOPES


@pytest.mark.parametrize("name,expected", [
    ("BC25123.png", ("BC25123", None)),
    ("BC25123A.png", ("BC25123", "A")),
    ("BC25123_A.png", ("BC25123", "A")),
    ("BC25123_a.png", ("BC25123", "A")),      # alpha upper-cased
    ("BC25123AB.png", ("BC25123", "AB")),
    ("BC1234.jpg", ("BC1234", None)),
    ("BC25123.v2.png", (None, None)),          # extra dots -> malformed
    ("IMG_001.png", (None, None)),             # non-BC -> malformed
    ("notes.txt", (None, None)),
])
def test_parse_generated_name(name, expected):
    assert drive_client.parse_generated_name(name) == expected
