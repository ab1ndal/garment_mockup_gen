from mockup_generator.config import settings
from mockup_generator.integrations import drive_client


def test_generated_folder_id_default():
    assert settings.generated_mockups_folder_id == "1FBDw_F40zDt4zvp6el3Ei50Nw8cOahm4"


def test_drive_scope_is_read_write():
    assert "https://www.googleapis.com/auth/drive" in drive_client._SCOPES
