"""Unit tests for :mod:`heardle.auth`.

All network calls are mocked via ``respx``. The tests verify the logical
contract of each function, not Spotify's server-side behaviour — that is covered
by ``tests/integration/test_spotify.py``.

Input selection rationale is given inline at each test case, per global testing
standards.
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx
from itsdangerous import BadSignature, SignatureExpired

from heardle.auth import (
    PremiumRequiredError,
    SpotifyAuthError,
    assert_premium,
    build_authorize_url,
    exchange_code_for_tokens,
    get_client_credentials_token,
    refresh_access_token,
    sign_state,
    verify_state,
)

TEST_SECRET = "unit-test-secret-not-used-in-production"  # pragma: allowlist secret
TEST_CLIENT_ID = "test_client_id"  # pragma: allowlist secret
TEST_CLIENT_SECRET = "test_client_secret"  # pragma: allowlist secret
TEST_REDIRECT_URI = "http://127.0.0.1:8000/callback"


# ---------------------------------------------------------------------------
# State signing / verification
# ---------------------------------------------------------------------------


def test_sign_and_verify_roundtrip() -> None:
    """Well-formed state should round-trip.

    Rationale: establishes the happy path before testing tamper / replay cases.
    """
    state = sign_state(TEST_SECRET)
    nonce = verify_state(TEST_SECRET, state)
    assert isinstance(nonce, str)
    assert len(nonce) > 0


def test_verify_rejects_tampered_state() -> None:
    """A state modified even by one character must fail verification.

    Rationale: guards against an attacker forging a callback URL. We flip
    a character in the middle of the signed payload (rather than at the
    very end) because the last character of an unpadded URL-safe-base64
    encoding only contributes 2–4 bits and can sometimes be altered
    without changing the decoded bytes — a base64 artefact, not an HMAC
    weakness, but it would cause spurious flakes here.
    """
    state = sign_state(TEST_SECRET)
    middle = len(state) // 2
    tampered = state[:middle] + ("A" if state[middle] != "A" else "B") + state[middle + 1 :]
    with pytest.raises(BadSignature):
        verify_state(TEST_SECRET, tampered)


def test_verify_rejects_wrong_secret() -> None:
    """State signed with one secret must not verify under another.

    Rationale: the HMAC secret is the only thing standing between us and
    forgery; a unit test makes the dependency explicit.
    """
    state = sign_state(TEST_SECRET)
    with pytest.raises(BadSignature):
        verify_state("different-secret", state)


def test_verify_rejects_expired_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """State older than ``STATE_MAX_AGE_SECONDS`` must be rejected.

    Rationale: blocks replay of a stolen state value after the user has walked
    away from the consent page. We simulate the passage of time by patching the
    ``itsdangerous`` timestamper rather than actually sleeping.
    """
    state = sign_state(TEST_SECRET)
    # Push clock forward by more than STATE_MAX_AGE_SECONDS (600 s).
    real_time = time.time
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: real_time() + 700)
    with pytest.raises(SignatureExpired):
        verify_state(TEST_SECRET, state)


# ---------------------------------------------------------------------------
# Authorize URL
# ---------------------------------------------------------------------------


def test_build_authorize_url_encodes_params() -> None:
    """All OAuth parameters must be percent-encoded and scopes space-joined.

    Rationale: Spotify rejects malformed ``/authorize`` requests silently with
    a generic error page; catching this here beats debugging it in the browser.
    """
    url = build_authorize_url(
        client_id=TEST_CLIENT_ID,
        redirect_uri=TEST_REDIRECT_URI,
        state="SIGNED_STATE",
        scopes=("streaming", "user-read-email"),
    )
    assert url.startswith("https://accounts.spotify.com/authorize?")
    assert "client_id=test_client_id" in url
    assert "response_type=code" in url
    # Redirect URI must be percent-encoded.
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8000%2Fcallback" in url
    assert "state=SIGNED_STATE" in url
    assert "scope=streaming+user-read-email" in url


# ---------------------------------------------------------------------------
# Token exchange / refresh / client credentials
# ---------------------------------------------------------------------------


@respx.mock
async def test_exchange_code_for_tokens_happy_path() -> None:
    """A valid authorization code yields a populated :class:`TokenBundle`.

    Rationale: asserts the mapping from Spotify's response JSON to our
    dataclass, including the ``expires_in`` → ``expires_at_epoch`` conversion.
    """
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ACC_TOKEN",
                "refresh_token": "REF_TOKEN",
                "expires_in": 3600,
                "scope": "streaming user-read-email",
                "token_type": "Bearer",
            },
        )
    )

    before = time.time()
    bundle = await exchange_code_for_tokens(
        code="AUTH_CODE",
        redirect_uri=TEST_REDIRECT_URI,
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
    )
    after = time.time()

    assert bundle.access_token == "ACC_TOKEN"
    assert bundle.refresh_token == "REF_TOKEN"
    assert bundle.scope == "streaming user-read-email"
    # ``expires_at_epoch`` must lie in [now + 3600, now + 3600 + epsilon].
    assert before + 3600 <= bundle.expires_at_epoch <= after + 3600


@respx.mock
async def test_exchange_code_for_tokens_raises_on_4xx() -> None:
    """A 400 response (e.g. invalid_grant) must raise ``SpotifyAuthError``.

    Rationale: verifies error propagation rather than silent empty return.
    """
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(400, text='{"error":"invalid_grant"}')
    )

    with pytest.raises(SpotifyAuthError) as exc:
        await exchange_code_for_tokens(
            code="BAD_CODE",
            redirect_uri=TEST_REDIRECT_URI,
            client_id=TEST_CLIENT_ID,
            client_secret=TEST_CLIENT_SECRET,
        )
    assert exc.value.status_code == 400


@respx.mock
async def test_refresh_reuses_input_when_server_omits_refresh_token() -> None:
    """When Spotify omits a new refresh_token on refresh, we keep the input one.

    Rationale: this is a documented Spotify behaviour — a regression here would
    cause the user's session to become unrefreshable after the first refresh.
    """
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "NEW_ACC",
                # No ``refresh_token`` field, matching Spotify's documented case.
                "expires_in": 3600,
                "scope": "streaming",
                "token_type": "Bearer",
            },
        )
    )

    bundle = await refresh_access_token(
        refresh_token="OLD_REF",
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
    )
    assert bundle.access_token == "NEW_ACC"
    assert bundle.refresh_token == "OLD_REF"


@respx.mock
async def test_get_client_credentials_token() -> None:
    """Client credentials grant returns ``(token, expires_at_epoch)``.

    Rationale: used by the corpus loader + integration tests; needs its own
    coverage because the return shape differs from user-flow tokens.
    """
    respx.post("https://accounts.spotify.com/api/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "CC_TOKEN",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )
    )

    before = time.time()
    token, expires_at = await get_client_credentials_token(
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
    )
    after = time.time()

    assert token == "CC_TOKEN"
    assert before + 3600 <= expires_at <= after + 3600


# ---------------------------------------------------------------------------
# Premium verification
# ---------------------------------------------------------------------------


@respx.mock
async def test_assert_premium_passes_for_premium_user() -> None:
    """Happy path — Premium user. Rationale: the Spotify Web Playback SDK only
    emits audio for Premium accounts; this is the only login state that lets the
    game run."""
    respx.get("https://api.spotify.com/v1/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "kyleyhw",
                "display_name": "Kyle",
                "email": "kyleyhw@example.com",
                "product": "premium",
                "country": "GB",
            },
        )
    )

    info = await assert_premium("ACC_TOKEN")
    assert info.product == "premium"
    assert info.country == "GB"


@respx.mock
async def test_assert_premium_raises_for_free_user() -> None:
    """A free-tier user must trigger :class:`PremiumRequiredError`.

    Rationale: without this guard, a free user sees the Web Playback SDK
    silently refuse to emit audio — an opaque failure mode. Raising early lets
    the UI show a clean "Premium required" message.
    """
    respx.get("https://api.spotify.com/v1/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "free_user",
                "display_name": "Free User",
                "email": "u@example.com",
                "product": "free",
                "country": "US",
            },
        )
    )

    with pytest.raises(PremiumRequiredError):
        await assert_premium("ACC_TOKEN")


@respx.mock
async def test_assert_premium_raises_on_non_200() -> None:
    """An expired or bogus token yields a ``SpotifyAuthError``.

    Rationale: ``/v1/me`` returns 401 when the access token is bad. The auth
    module should not paper over this as "not Premium" — callers need to
    distinguish so they can trigger a refresh.
    """
    respx.get("https://api.spotify.com/v1/me").mock(
        return_value=httpx.Response(401, text='{"error":{"status":401}}')
    )

    with pytest.raises(SpotifyAuthError) as exc:
        await assert_premium("EXPIRED_TOKEN")
    assert exc.value.status_code == 401
