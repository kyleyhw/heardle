"""Async wrapper over the Spotify Web API.

Phase 2 implementation target. Exposes track / playlist / artist-top-tracks /
year-search metadata endpoints. All functions here take an access token explicitly
so that the module stays stateless.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    """Minimal track representation used downstream by the game and corpus modules."""

    spotify_id: str
    title: str
    primary_artist: str
    album_name: str
    release_year: int
    popularity: int | None  # may be ``None`` under Development-Mode apps post-Feb-2026


async def fetch_playlist_tracks(playlist_id: str, access_token: str) -> list[Track]:
    """Fetch all tracks in a playlist (handling pagination). Phase 2."""
    raise NotImplementedError("Implemented in Phase 2.")


async def fetch_artist_top_tracks(artist_id: str, access_token: str) -> list[Track]:
    """Fetch the artist's top tracks (Spotify's own ranking). Phase 2."""
    raise NotImplementedError("Implemented in Phase 2.")


async def search_tracks_by_year(year: int, access_token: str, limit: int = 50) -> list[Track]:
    """Search for tracks released in a given year. Phase 2."""
    raise NotImplementedError("Implemented in Phase 2.")
