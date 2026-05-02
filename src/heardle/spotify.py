"""Async wrapper over the Spotify Web API.

Stateless: every function accepts an access token explicitly so the module has
no global state and is trivially mockable in tests. 401 handling is deliberately
left to the caller — the FastAPI route layer owns the refresh-and-retry logic
so it can update the session cookie atomically.

All endpoints handled here work under either user-OAuth tokens or Client
Credentials tokens, except where noted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

SPOTIFY_API_BASE = "https://api.spotify.com/v1"

# Spotify's per-page cap for playlist tracks and search results.
MAX_PAGE_SIZE: int = 50

# Upper bound on total tracks to return from a paginated endpoint. Playlists
# cap out around 11,000 tracks on Spotify's side; we stop earlier to keep
# memory bounded — large enough that typical playlists complete, small enough
# that pathological ones do not blow up a game session.
DEFAULT_PAGINATION_CAP: int = 2_000


@dataclass(frozen=True)
class Track:
    """Provider-agnostic track representation used by the game and corpus modules.

    The field name ``spotify_id`` is historical — in iTunes- or Deezer-backed
    mode it holds the provider's track id as a string. The game logic only
    cares about string equality, not provider semantics. The optional
    :attr:`preview_url` is populated by backends that expose a ready-to-play
    audio URL (iTunes, Deezer); Spotify-backed tracks leave it ``None``
    because playback goes through the Web Playback SDK instead.

    ``popularity`` is ``None`` when the Spotify app is in Development Mode
    under the February-2026 API revision, which dropped the field from that
    tier; iTunes- and Deezer-backed tracks also leave it ``None`` since
    neither provider exposes an equivalent.

    ``release_year`` is ``None`` when the backend's bulk endpoints do not
    include release-date metadata (Deezer's ``/search`` and ``/artist/{id}/top``
    only return album.id, not release_date). Year-based searches and
    single-track lookups always populate it.
    """

    spotify_id: str
    title: str
    primary_artist: str
    album_name: str
    release_year: int | None
    popularity: int | None
    preview_url: str | None = None


class SpotifyAPIError(RuntimeError):
    """Raised when a Spotify Web API endpoint returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Spotify API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


async def fetch_playlist_tracks(
    playlist_id: str,
    access_token: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    cap: int = DEFAULT_PAGINATION_CAP,
) -> list[Track]:
    """Return all tracks in a playlist, following ``next`` pagination links.

    Stops at ``cap`` tracks even if the playlist is longer, to keep memory bounded.
    Filters out ``None`` entries (Spotify inserts these for deleted/unavailable
    items) and tracks with missing critical fields.
    """
    url: str | None = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks?limit={MAX_PAGE_SIZE}"
    tracks: list[Track] = []

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        while url is not None and len(tracks) < cap:
            body = await _get_json(client, url, access_token)
            for item in body.get("items", []):
                # Playlist items wrap the track object; episodes have ``type != "track"``.
                raw_track = item.get("track")
                if raw_track is None or raw_track.get("type") != "track":
                    continue
                parsed = _try_parse_track(raw_track)
                if parsed is not None:
                    tracks.append(parsed)
            url = body.get("next")
    finally:
        if owns_client:
            await client.aclose()

    return tracks[:cap]


async def fetch_artist_top_tracks(
    artist_id: str,
    access_token: str,
    *,
    market: str = "US",
    http_client: httpx.AsyncClient | None = None,
) -> list[Track]:
    """Return the artist's top tracks per Spotify's own ranking, bounded by market.

    The ``market`` parameter matters: popularity rankings differ by country. The
    API returns at most 10 tracks regardless of ``limit``, so no pagination is
    needed here.
    """
    url = f"{SPOTIFY_API_BASE}/artists/{artist_id}/top-tracks?market={market}"
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        body = await _get_json(client, url, access_token)
    finally:
        if owns_client:
            await client.aclose()

    tracks: list[Track] = []
    for raw_track in body.get("tracks", []):
        parsed = _try_parse_track(raw_track)
        if parsed is not None:
            tracks.append(parsed)
    return tracks


async def search_tracks_by_year(
    year: int,
    access_token: str,
    *,
    market: str = "US",
    http_client: httpx.AsyncClient | None = None,
    cap: int = DEFAULT_PAGINATION_CAP,
) -> list[Track]:
    """Return tracks whose Spotify-indexed release year equals ``year``.

    Uses the ``year:`` search qualifier. Results are ranked by Spotify's own
    relevance algorithm (roughly: popularity + freshness). Paginated via the
    ``next`` link in the ``tracks`` envelope.
    """
    query = f"year:{year}"
    url: str | None = (
        f"{SPOTIFY_API_BASE}/search?q={query}" f"&type=track&market={market}&limit={MAX_PAGE_SIZE}"
    )
    tracks: list[Track] = []

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        while url is not None and len(tracks) < cap:
            body = await _get_json(client, url, access_token)
            items_envelope = body.get("tracks", {})
            for raw_track in items_envelope.get("items", []):
                parsed = _try_parse_track(raw_track)
                if parsed is not None:
                    tracks.append(parsed)
            url = items_envelope.get("next")
    finally:
        if owns_client:
            await client.aclose()

    return tracks[:cap]


async def fetch_track(
    track_id: str,
    access_token: str,
    *,
    market: str = "US",
    http_client: httpx.AsyncClient | None = None,
) -> Track:
    """Return a single track by Spotify id. Raises ``SpotifyAPIError`` on 404."""
    url = f"{SPOTIFY_API_BASE}/tracks/{track_id}?market={market}"
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        body = await _get_json(client, url, access_token)
    finally:
        if owns_client:
            await client.aclose()

    parsed = _try_parse_track(body)
    if parsed is None:
        raise SpotifyAPIError(
            200,
            f"Track {track_id} returned a 200 but could not be parsed — "
            "likely missing required fields.",
        )
    return parsed


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    access_token: str,
) -> dict[str, Any]:
    """Issue a Bearer-authenticated GET and return the parsed JSON body."""
    headers = {"Authorization": f"Bearer {access_token}"}
    response = await client.get(url, headers=headers, timeout=15.0)
    if response.status_code != 200:
        raise SpotifyAPIError(response.status_code, response.text)
    body: dict[str, Any] = response.json()
    return body


def _try_parse_track(raw: dict[str, Any]) -> Track | None:
    """Parse a Spotify track JSON into a :class:`Track`, or ``None`` on missing fields.

    Spotify is permissive about which fields may be absent (regional restrictions,
    incomplete metadata on obscure releases). Rather than raising mid-stream we
    return ``None`` and let the caller skip silently — any track that cannot be
    represented cleanly is a track that cannot be played, so dropping it is the
    correct behaviour for the game loop.
    """
    try:
        spotify_id = raw["id"]
        title = raw["name"]
        artists = raw.get("artists") or []
        if not artists:
            return None
        primary_artist = artists[0]["name"]
        album = raw.get("album") or {}
        album_name = album.get("name", "")
        release_date = album.get("release_date", "")
        release_year = _parse_release_year(release_date)
        if release_year is None:
            return None
        raw_popularity = raw.get("popularity")
        popularity = int(raw_popularity) if raw_popularity is not None else None
    except (KeyError, TypeError, ValueError):
        return None

    return Track(
        spotify_id=spotify_id,
        title=title,
        primary_artist=primary_artist,
        album_name=album_name,
        release_year=release_year,
        popularity=popularity,
    )


def _parse_release_year(release_date: str) -> int | None:
    """Extract the year from a Spotify ``release_date`` string.

    Spotify formats are ``"YYYY"``, ``"YYYY-MM"``, or ``"YYYY-MM-DD"`` depending
    on the track's ``release_date_precision``. We only need the year.
    """
    if not release_date:
        return None
    head = release_date.split("-", 1)[0]
    try:
        year = int(head)
    except ValueError:
        return None
    # Sanity bound — reject absurd values that would indicate malformed metadata.
    # Spotify began operations in 2006 so most modern tracks post-date that, but
    # the catalogue includes remastered historical recordings back to the early
    # 20th century; 1900 is a conservative floor.
    if year < 1900 or year > 2100:
        return None
    return year
