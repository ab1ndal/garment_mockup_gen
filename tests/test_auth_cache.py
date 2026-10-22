import base64
import json
import time

import pytest

from backend import auth
from mockup_generator.db.profiles_repo import Profile


def _token(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.sig"


@pytest.fixture
def verify_calls(monkeypatch):
    """Count trips to Supabase Auth; the clock is controlled via ``clock``."""
    calls = []
    user = type("U", (), {"id": "u1"})()

    class _Auth:
        def get_user(self, token):
            calls.append(token)
            return type("R", (), {"user": user})()

    monkeypatch.setattr(auth, "anon_client", lambda: type("C", (), {"auth": _Auth()})())
    monkeypatch.setattr(auth, "service_client", lambda: object())
    monkeypatch.setattr(auth, "get_profile_by_id", lambda db, uid: Profile(
        id=uid, email="a@b.c", role="user", is_active=True))
    monkeypatch.setattr(auth, "_AUTH_CACHE", {})
    return calls


def _at(monkeypatch, t):
    monkeypatch.setattr(auth.time, "monotonic", lambda: t)


def test_cached_for_five_minutes(monkeypatch, verify_calls):
    tok = _token(time.time() + 3600)
    _at(monkeypatch, 1000.0)
    auth.get_current_user(f"Bearer {tok}")
    _at(monkeypatch, 1000.0 + 299)
    auth.get_current_user(f"Bearer {tok}")
    assert len(verify_calls) == 1          # a 39s pause no longer re-verifies
    _at(monkeypatch, 1000.0 + 301)
    auth.get_current_user(f"Bearer {tok}")
    assert len(verify_calls) == 2


def test_cache_never_outlives_token_expiry(monkeypatch, verify_calls):
    tok = _token(time.time() + 60)         # expires in a minute
    _at(monkeypatch, 1000.0)
    auth.get_current_user(f"Bearer {tok}")
    _at(monkeypatch, 1000.0 + 61)
    auth.get_current_user(f"Bearer {tok}")
    assert len(verify_calls) == 2          # re-verified (and Supabase would reject it)


def test_unreadable_exp_falls_back_to_ttl():
    assert auth._seconds_until_expiry("not-a-jwt") is None
    assert auth._seconds_until_expiry("a.%%%.c") is None
