import importlib


def test_rembg_model_default(monkeypatch):
    monkeypatch.delenv("REMBG_MODEL", raising=False)
    import mockup_generator.config as config
    importlib.reload(config)
    assert config.get_settings().rembg_model == "birefnet-general-lite"


def test_rembg_model_env_override(monkeypatch):
    monkeypatch.setenv("REMBG_MODEL", "birefnet-general")
    import mockup_generator.config as config
    importlib.reload(config)
    assert config.get_settings().rembg_model == "birefnet-general"
