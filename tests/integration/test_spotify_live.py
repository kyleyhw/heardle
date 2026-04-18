"""Integration tests that hit the real Spotify Web API.

Gated on credentials: each test is skipped when ``SPOTIFY_CLIENT_ID`` /
``SPOTIFY_CLIENT_SECRET`` are not set in the environment. Uses the Client
Credentials flow so no interactive user login is required — the endpoints
exercised (public playlist read, artist top-tracks, year search) all accept
app-only tokens.

These tests confirm that our wrapper's assumptions match Spotify's actual
response shape. They should pass after any Phase 2 code change that touches
:mod:`heardle.spotify` or :mod:`heardle.auth`.
"""

from __future__ import annotations

import os

import httpx
import pytest

from heardle.auth import get_client_credentials_token
from heardle.spotify import (
    fetch_artist_top_tracks,
    fetch_playlist_tracks,
    search_tracks_by_year,
)

# Spotify's own "Today's Top Hits" editorial playlist — stable, always public,
# consistently populated. Using an editorial playlist rather than a user-owned
# one avoids the risk of the list disappearing when a user deletes it.
TODAYS_TOP_HITS_PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"

# Ed Sheeran — a consistently high-popularity artist whose top-tracks
# endpoint reliably returns 10 results in any major market.
ED_SHEERAN_ARTIST_ID = "6eUKZXaKkcviH0Ku9w2n3V"


_requires_credentials = pytest.mark.skipif(
    not (os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET")),
    reason="SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set for integration tests",
)


@pytest.fixture
async def access_token() -> str:
    """Obtain an app-only access token via Client Credentials."""
    token, _ = await get_client_credentials_token(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
    )
    return token


@_requires_credentials
async def test_fetch_todays_top_hits_playlist(access_token: str) -> None:
    """The Today's Top Hits playlist should return a non-empty list of parsed tracks.

    Rationale: exercises the pagination path and confirms our parser handles
    the real-world mix of feature artists, remastered tracks, and varied release
    date precisions.
    """
    async with httpx.AsyncClient() as client:
        tracks = await fetch_playlist_tracks(
            TODAYS_TOP_HITS_PLAYLIST_ID, access_token, http_client=client
        )

    assert len(tracks) > 0
    # Spotify editorial playlists typically run 50+ tracks.
    assert len(tracks) >= 25
    # Every returned track must have the fields we require downstream.
    for t in tracks:
        assert t.spotify_id
        assert t.title
        assert t.primary_artist
        assert 1900 <= t.release_year <= 2100


@_requires_credentials
async def test_fetch_artist_top_tracks_live(access_token: str) -> None:
    """Artist top-tracks returns exactly what Spotify ranks top, up to 10.

    Rationale: smoke-check against a high-profile artist — if this fails, any
    artist-input path in the game is broken.
    """
    async with httpx.AsyncClient() as client:
        tracks = await fetch_artist_top_tracks(
            ED_SHEERAN_ARTIST_ID, access_token, http_client=client
        )

    assert 1 <= len(tracks) <= 10
    assert all(t.primary_artist for t in tracks)


@_requires_credentials
async def test_search_tracks_by_year_live(access_token: str) -> None:
    """Year-search for a recent year returns a non-empty, bounded list.

    Rationale: year-search is an input mode advertised in the UI; verifying it
    returns plausible results for a known-populated year catches the most
    likely breakage (query-string formatting, envelope shape).
    """
    async with httpx.AsyncClient() as client:
        tracks = await search_tracks_by_year(2020, access_token, http_client=client, cap=100)

    assert len(tracks) >= 50
    assert all(t.release_year == 2020 for t in tracks)
