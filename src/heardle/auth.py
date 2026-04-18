"""Spotify OAuth Authorization Code flow + HMAC-signed state + Premium verification.

Three flows live here:

1. **Authorization Code** (user login): :func:`build_authorize_url`,
   :func:`exchange_code_for_tokens`, :func:`refresh_access_token`. The user-level
   access token is what the Web Playback SDK needs, so this is the primary flow.
2. **Client Credentials** (app-only, no user): :func:`get_client_credentials_token`.
   Used by integration tests and the corpus-loader script — any metadata endpoint
   that does not require a user scope.
3. **Premium verification**: :func:`assert_premium` calls ``/v1/me`` and raises
   :class:`PremiumRequiredError` if ``product != "premium"``. Called on every
   login and on every game creation.

State signing uses :class:`itsdangerous.URLSafeTimedSerializer`, which bundles
an HMAC-SHA256 signature with a timestamp in a URL-safe base64 string. This
protects the OAuth callback from CSRF and replay.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"

# OAuth state lifetime. 10 minutes is comfortably longer than any user would
# spend on the Spotify consent screen, and short enough that a leaked state
# value is not useful for replay.
STATE_MAX_AGE_SECONDS: int = 600

# Scopes required by the Heardle clone. See docs/oauth_flow.md.
DEFAULT_SCOPES: tuple[str, ...] = (
    "streaming",
    "user-read-email",
    "user-read-private",
    "playlist-read-private",
)


class PremiumRequiredError(PermissionError):
    """Raised when a logged-in user's account is not Premium.

    The Web Playback SDK silently drops playback for free-tier accounts, so we
    fail the login explicitly rather than letting the UI appear to hang.
    """


class SpotifyAuthError(RuntimeError):
    """Raised when a Spotify auth endpoint returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Spotify auth error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class TokenBundle:
    """Access + refresh tokens for an authenticated user session."""

    access_token: str
    refresh_token: str
    expires_at_epoch: float
    scope: str


@dataclass(frozen=True)
class UserInfo:
    """Subset of ``/v1/me`` response fields needed downstream."""

    user_id: str
    display_name: str
    email: str
    product: str  # "premium", "free", or "open" (a regional variant of free)
    country: str  # ISO 3166-1 alpha-2 market code; needed for top-tracks + search


def make_state_serializer(secret: str) -> URLSafeTimedSerializer:
    """Return a serializer bound to the given secret. Separate for testability."""
    return URLSafeTimedSerializer(secret_key=secret, salt="spotify-oauth-state")


def sign_state(secret: str) -> str:
    """Produce a fresh HMAC-signed state value.

    The payload is a random 32-byte nonce; the serializer adds a timestamp and
    HMAC-SHA256 signature. Caller should stash the state in the session cookie
    so the callback can compare server-known state against the round-tripped value.
    """
    nonce = secrets.token_urlsafe(32)
    return make_state_serializer(secret).dumps(nonce)


def verify_state(secret: str, state: str) -> str:
    """Verify a state returned from Spotify.

    Returns the original nonce on success; raises :class:`BadSignature` on a
    tampered signature or :class:`SignatureExpired` on a value older than
    :data:`STATE_MAX_AGE_SECONDS`.
    """
    serializer = make_state_serializer(secret)
    result = serializer.loads(state, max_age=STATE_MAX_AGE_SECONDS)
    # ``loads`` returns ``Any`` per itsdangerous typing; we know it is the nonce str.
    assert isinstance(result, str)
    return result


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    """Compose the Spotify ``/authorize`` URL the browser should redirect to."""
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(scopes),
    }
    return f"{SPOTIFY_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> TokenBundle:
    """Exchange an authorization code for ``(access, refresh)`` tokens.

    Parameters
    ----------
    code
        The authorization code returned by Spotify on the redirect callback.
    redirect_uri
        Must exactly match the URI registered in the Spotify Developer Dashboard
        and used in :func:`build_authorize_url`.
    client_id, client_secret
        The Spotify Developer app credentials.
    http_client
        Injection point for tests. When ``None``, a transient client is created
        and disposed; when provided, the caller owns the lifecycle.
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    return await _post_token(data, http_client=http_client)


async def refresh_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> TokenBundle:
    """Refresh an expired access token.

    Spotify may or may not return a new refresh token. Per their docs: "If the
    refresh_token was not sent back by Spotify, the existing one should still be
    used." We surface both behaviours transparently: the returned
    :class:`TokenBundle` always has a populated ``refresh_token``, preferring
    Spotify's new value when given and falling back to the input otherwise.
    """
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    bundle = await _post_token(data, http_client=http_client)
    # Substitute the input refresh token when Spotify returned an empty one.
    if not bundle.refresh_token:
        bundle = TokenBundle(
            access_token=bundle.access_token,
            refresh_token=refresh_token,
            expires_at_epoch=bundle.expires_at_epoch,
            scope=bundle.scope,
        )
    return bundle


async def get_client_credentials_token(
    client_id: str,
    client_secret: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, float]:
    """Obtain an app-only access token via the Client Credentials grant.

    Returns ``(access_token, expires_at_epoch)``. No refresh token exists in
    this flow — when the token expires, call again.
    """
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    bundle = await _post_token(data, http_client=http_client)
    return bundle.access_token, bundle.expires_at_epoch


async def assert_premium(
    access_token: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> UserInfo:
    """Fetch ``/v1/me`` and raise :class:`PremiumRequiredError` if not Premium.

    Returns the parsed :class:`UserInfo` on success so callers can cache it in
    the session without a second round trip.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        response = await client.get(SPOTIFY_ME_URL, headers=headers, timeout=10.0)
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        raise SpotifyAuthError(response.status_code, response.text)

    body: dict[str, Any] = response.json()
    info = UserInfo(
        user_id=body["id"],
        display_name=body.get("display_name", body["id"]),
        email=body.get("email", ""),
        product=body["product"],
        country=body["country"],
    )
    if info.product != "premium":
        raise PremiumRequiredError(
            f"User '{info.user_id}' has product '{info.product}', not 'premium'. "
            "The Spotify Web Playback SDK requires a Premium subscription."
        )
    return info


async def _post_token(
    data: dict[str, str],
    *,
    http_client: httpx.AsyncClient | None,
) -> TokenBundle:
    """Internal helper wrapping the three grant flows that all POST the token URL."""
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        response = await client.post(SPOTIFY_TOKEN_URL, data=data, timeout=10.0)
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        raise SpotifyAuthError(response.status_code, response.text)

    body: dict[str, Any] = response.json()
    # Spotify returns ``expires_in`` in seconds; we convert to an absolute epoch
    # so downstream code does not need to remember when it was issued.
    return TokenBundle(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", ""),
        expires_at_epoch=time.time() + float(body["expires_in"]),
        scope=body.get("scope", ""),
    )


# Re-export itsdangerous exception types so callers can import them from here.
__all__ = [
    "DEFAULT_SCOPES",
    "STATE_MAX_AGE_SECONDS",
    "BadSignature",
    "PremiumRequiredError",
    "SignatureExpired",
    "SpotifyAuthError",
    "TokenBundle",
    "UserInfo",
    "assert_premium",
    "build_authorize_url",
    "exchange_code_for_tokens",
    "get_client_credentials_token",
    "refresh_access_token",
    "sign_state",
    "verify_state",
]
