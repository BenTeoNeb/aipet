"""Tests cost nothing: no network access (so no paid model calls).

Any outbound connection makes the test fail immediately. Models, speech recognition and TTS
are faked in tests; trials with a real model are done by hand, outside pytest.
"""

import socket

import pytest

_connect = socket.socket.connect
_LOCAL = ("127.0.0.1", "::1", "localhost")


def _guard(self, address, *a, **kw):
    host = address[0] if isinstance(address, tuple) else address
    if self.family == socket.AF_UNIX or host in _LOCAL:
        return _connect(self, address, *a, **kw)
    raise RuntimeError(f"Network is forbidden in tests (possible paid call): connection to {address}")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _guard)
    monkeypatch.setattr(socket, "create_connection", lambda address, *a, **kw: _guard(socket.socket(), address))


@pytest.fixture(autouse=True)
def fake_google_credentials(monkeypatch):
    """Never use (or look for) real Google credentials: tests behave the same on a laptop with
    gcloud credentials and on CI without any (where the lookup would hit the metadata server)."""
    import google.auth
    import google.auth._default
    from google.auth.credentials import AnonymousCredentials

    def fake_default(*args, **kwargs):
        return AnonymousCredentials(), "test-project"

    monkeypatch.setattr(google.auth, "default", fake_default)
    monkeypatch.setattr(google.auth._default, "default", fake_default)


@pytest.fixture(autouse=True)
def no_real_logs(monkeypatch, tmp_path):
    """Usage lines and logs written during a test go to a temporary folder, never the family's."""
    monkeypatch.setenv("PET_LOGS", str(tmp_path / "logs"))
    monkeypatch.setenv("PET_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr("aipet.games.adventure.SHADOW_ATLAS", False)  # no background search per test


@pytest.fixture(autouse=True)
def no_dev_switches(monkeypatch):
    """Development switches from a local .env must not change test behaviour."""
    for name in ("PET_NO_SLEEP", "PET_DEAF", "PET_CHARACTER", "FLOCON_NO_SLEEP", "FLOCON_DEAF", "FLOCON_CHARACTER"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def fake_api_keys(monkeypatch):
    """Real keys from a local .env are replaced by fakes: a test that builds engines behaves the
    same on a laptop and on CI (which has no key), and can never spend money."""
    from aipet.engines import DEFAULT_KEY_ENVS

    for name in DEFAULT_KEY_ENVS.values():
        monkeypatch.setenv(name, "fake-key-for-tests")


@pytest.fixture(autouse=True)
def no_short_card_model(monkeypatch):
    """Short cards are rewritten by a model after each condensation: in tests, it is unavailable
    (the trimmed long card stands in) unless a test fakes it."""
    from aipet.memory import short_cards

    def unavailable(cfg, system, prompt):
        raise RuntimeError("no model in tests")

    monkeypatch.setattr(short_cards, "_call_llm", unavailable)
