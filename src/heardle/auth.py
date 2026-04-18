"""Spotify OAuth Authorization Code flow.

Phase 2 implementation target. This module intentionally contains only type stubs so
the rest of the package can import from it and so tests can mock it; the flow itself
is built out in Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenBundle:
    """Access + refresh tokens returned from the Spotify token endpoint."""

    access_token: str
    refresh_token: str
    expires_at_epoch: float
    scope: str


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> TokenBundle:
    """Exchange an authorization code for access + refresh tokens. Phase 2."""
    raise NotImplementedError("Implemented in Phase 2.")


async def refresh_access_token(refresh_token: str) -> TokenBundle:
    """Refresh an expired access token. Phase 2."""
    raise NotImplementedError("Implemented in Phase 2.")


async def assert_premium(access_token: str) -> None:
    """Raise ``PermissionError`` if the user's account is not Premium. Phase 2."""
    raise NotImplementedError("Implemented in Phase 2.")
