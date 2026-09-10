from dataclasses import replace

import pytest
from pydantic import ValidationError

from spb_assistant_api.security.browser_session import BrowserSessionError, BrowserSessionManager
from spb_assistant_api.settings import AssistantSettings

from .browser_session_fixture import API_KEY, SIGNING_KEY, browser_config


def cookie(manager, identity):
    return f"{manager.config.cookie_name}={identity.token}"


def test_random_origin_bound_identity_and_no_secret_repr():
    manager = BrowserSessionManager(browser_config(), clock=lambda: 1000)
    first, second = manager.mint(), manager.mint()
    assert first.owner_id != second.owner_id
    assert first.expires_at == 2800
    assert manager.verify_cookie(cookie(manager, first)) == first
    assert first.token not in repr(first)
    assert SIGNING_KEY not in repr(manager.config)
    assert API_KEY not in repr(manager.config)
    other = BrowserSessionManager(replace(manager.config, public_origin="http://localhost:13006"), clock=lambda: 1000)
    with pytest.raises(BrowserSessionError, match="无效"):
        other.verify_cookie(cookie(manager, first))


@pytest.mark.parametrize("changes", [
    {"public_origin": "https://example.com/path"}, {"public_origin": "https://example.com?x=1"},
    {"public_origin": "https://user:pass@example.com"}, {"public_origin": "https://example.com#x"},
    {"public_origin": "https://exam ple.com"}, {"public_origin": "http://example.com"},
    {"public_origin": "http://localhost:99999"}, {"secure": True},
    {"signing_key": "short"}, {"signing_key": ""}, {"signing_key": "x" * 32 + " "},
    {"signing_key": API_KEY}, {"previous_signing_key": SIGNING_KEY},
    {"previous_signing_key": "short"}, {"proxy_api_key": ""},
    {"ttl_seconds": 59}, {"ttl_seconds": 86401}, {"ttl_seconds": True},
])
def test_invalid_config_rejected(changes):
    with pytest.raises(ValueError):
        browser_config(**changes)


def test_secure_cookie_name_and_only_loopback_insecure():
    assert browser_config(secure=True, public_origin="https://agent.example").cookie_name == "__Host-spb-agent"
    assert browser_config(public_origin="http://[::1]:13006").cookie_name == "spb-agent-local"


def test_signature_rotation_preserves_owner_and_absolute_expiry():
    old = BrowserSessionManager(browser_config(), clock=lambda: 1000)
    identity = old.mint()
    rotated = BrowserSessionManager(browser_config(
        signing_key="new-synthetic-key-32-bytes-long-000", previous_signing_key=SIGNING_KEY,
    ), clock=lambda: 1200)
    updated, issued = rotated.establish(cookie(old, identity))
    assert (updated.owner_id, updated.expires_at) == (identity.owner_id, 2800)
    assert updated.token == identity.token and not issued
    revoked = BrowserSessionManager(replace(rotated.config, previous_signing_key=""), clock=lambda: 1200)
    with pytest.raises(BrowserSessionError):
        revoked.verify_cookie(cookie(old, identity))
    fresh = rotated.mint()
    assert revoked.verify_cookie(cookie(rotated, fresh)) == fresh


@pytest.mark.parametrize("mutation", ["tamper", "duplicate", "oversize", "truncated", "wrong_version", "leading_zero"])
def test_bad_tokens_never_silently_replaced(mutation):
    manager = BrowserSessionManager(browser_config(), clock=lambda: 1000)
    identity = manager.mint()
    value = cookie(manager, identity)
    value = {
        "tamper": value[:-1] + ("B" if value[-1] != "B" else "A"),
        "duplicate": value + "; " + value,
        "oversize": value + "; other=" + "x" * 8192,
        "truncated": value[:-3], "wrong_version": value.replace("=1.", "=2."),
        "leading_zero": value.replace(".1000.", ".01000."),
    }[mutation]
    with pytest.raises(BrowserSessionError) as failure:
        manager.establish(value)
    assert failure.value.code == "browser_session_invalid"
    assert manager.establish(value, reset=True)[0].owner_id != identity.owner_id


def test_expiry_future_token_and_reference_fail_closed():
    clock = [1000]
    manager = BrowserSessionManager(browser_config(), clock=lambda: clock[0])
    identity = manager.mint()
    assert manager.establish(None)
    for reference in (None, "bad", "a" * 64):
        with pytest.raises(BrowserSessionError) as failure:
            manager.check_reference(identity, reference)
        assert failure.value.status == 409
    manager.check_reference(identity, identity.session_ref)
    clock[0] = 900
    with pytest.raises(BrowserSessionError):
        manager.verify_cookie(cookie(manager, identity))
    clock[0] = 2800
    with pytest.raises(BrowserSessionError) as failure:
        manager.establish(cookie(manager, identity))
    assert failure.value.code == "browser_session_expired"


@pytest.mark.parametrize("origin,site,method,accepted", [
    (None, None, "GET", True), (None, "same-origin", "GET", True),
    (None, "cross-site", "GET", False), (None, None, "POST", False),
    ("http://127.0.0.1:13006", "same-origin", "POST", True),
    ("http://127.0.0.1:13006", "same-site", "DELETE", False),
    ("https://evil.invalid", "same-origin", "POST", False),
    ("null", None, "POST", False),
])
def test_origin_guard(origin, site, method, accepted):
    manager = BrowserSessionManager(browser_config())
    if accepted:
        manager.check_origin(origin=origin, fetch_site=site, method=method)
    else:
        with pytest.raises(BrowserSessionError) as failure:
            manager.check_origin(origin=origin, fetch_site=site, method=method)
        assert failure.value.status == 403


def test_settings_opt_in_and_independent_signing_key(tmp_path):
    values = dict(
        agent_enabled=True, agent_database_path=str(tmp_path / "agent.db"),
        api_keys=API_KEY, agent_browser_session_enabled=True,
        agent_browser_proxy_api_key=API_KEY, agent_browser_signing_key=SIGNING_KEY,
        agent_browser_public_origin="https://agent.example",
    )
    assert AssistantSettings(**values).browser_session_config().secure
    assert AssistantSettings().browser_session_config() is None
    for changed in (
        {"agent_enabled": False}, {"auth_enabled": False}, {"api_keys": "different"},
        {"api_keys": API_KEY + "," + SIGNING_KEY}, {"agent_database_path": "relative.db"},
    ):
        with pytest.raises(ValidationError) as error:
            AssistantSettings(**{**values, **changed})
        assert SIGNING_KEY not in str(error.value)
