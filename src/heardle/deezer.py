"""Deezer Public API wrapper — free, no auth, returns 30-second preview MP3 URLs.

Why this module exists
----------------------
Deezer's public API gives us three properties that iTunes does not:

1. **Larger global catalogue.** Deezer's search index includes ~120 M tracks
   across all major regional licensors, versus iTunes' US-biased subset.
2. **More generous rate limit.** Deezer caps at 50 requests / 5 s per IP
   (~10 req/s sustained), versus iTunes' ~20/min.
3. **Native autocomplete endpoint** at ``/search/autocomplete`` (not currently
   used here — we substring-sweep the correct pool client-side, identical to
   the iTunes path, so the two backends are interchangeable downstream).

Tradeoffs vs. iTunes
--------------------
- Deezer's preview is selected by their backend (often the chorus, sometimes
  a verse). iTunes has the same problem; only the deferred Spotify
  Web-Playback path gives canonical $T[0, d_i]$ intro reveal.
- Deezer's ``/search`` and ``/artist/{id}/top`` envelopes do **not** include
  ``release_date`` — only ``album.id``. To populate ``Track.release_year`` we
  must additionally fetch ``/track/{id}`` for each row. That is acceptable
  for the year-source query path (filter requires the year), but wasteful for
  artist/search paths where year is purely cosmetic — so for those paths we
  leave ``release_year = None`` and the templates render artist alone.

Endpoints used
--------------
- ``GET https://api.deezer.com/search?q={query}&limit={N}``
  Free-text search across title + artist + album. Returns ``{"data": [...]}``.
- ``GET https://api.deezer.com/search/artist?q={artist}&limit=1``
  Resolve an artist name to a numeric id, picking the highest-fan-count match.
- ``GET https://api.deezer.com/artist/{id}/top?limit={N}``
  Top tracks for the resolved artist, ranked by Deezer's own popularity score.
- ``GET https://api.deezer.com/track/{id}``
  Single-track lookup; the only endpoint that includes ``release_date``.

Preview availability
--------------------
A non-trivial fraction of tracks (rights-restricted, regional licensing, very
new releases) return ``"preview": ""`` — empty string, not null. We treat that
as "unplayable" and drop the row at parse time. This means the correct-answer
pool $\\mathcal{C}$ as exposed to the game is already restricted to playable
tracks, so the uniform sample $t \\sim \\mathrm{Uniform}(\\mathcal{C})$ never
selects an unwinnable target.

Rate limit handling
-------------------
Year-source queries fan out N concurrent ``/track/{id}`` lookups to populate
release_year. We bound concurrency with an :class:`asyncio.Semaphore` of
``MAX_CONCURRENT_LOOKUPS = 8`` to stay under Deezer's 50-req-per-5-s ceiling
even if the autocomplete is also active.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from heardle.spotify import Track

API_BASE = "https://api.deezer.com"
SEARCH_URL = f"{API_BASE}/search"
ARTIST_SEARCH_URL = f"{API_BASE}/search/artist"

# Deezer's per-call cap on ``limit`` is 100 for /search and 100 for
# /artist/{id}/top; their pagination uses ``index`` for offsets but for the
# game's needs (~50 candidate tracks per artist) we stay within one page.
MAX_LIMIT: int = 100

# Bound for the year-search bulk-fetch. See module docstring; chosen well below
# Deezer's 50/5s ceiling so the game's autocomplete traffic still has headroom.
MAX_CONCURRENT_LOOKUPS: int = 8


@dataclass(frozen=True)
class DeezerAPIError(RuntimeError):
    """Raised when Deezer returns a non-2xx response or an error envelope."""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return f"Deezer API error {self.status_code}: {self.detail}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def search_tracks(
    query: str,
    *,
    limit: int = 10,
    http_client: httpx.AsyncClient | None = None,
) -> list[Track]:
    """Free-text track search across Deezer's catalogue, ranked by Deezer relevance.

    Used for the autocomplete path and for the "search term" source type. Year
    is left ``None`` on the returned tracks because ``/search`` does not expose
    ``release_date``; callers that need the year (e.g. year-source filtering)
    must subsequently call :func:`fetch_track` per row.
    """
    params = {"q": query, "limit": min(limit, MAX_LIMIT)}
    return await _get_tracks(SEARCH_URL, params, http_client=http_client)


async def search_tracks_by_artist(
    artist: str,
    *,
    limit: int = 50,
    http_client: httpx.AsyncClient | None = None,
) -> list[Track]:
    """Resolve ``artist`` to its top ``limit`` tracks per Deezer's popularity ranking.

    Two-hop:

    1. ``/search/artist?q={artist}&limit=1`` — pick the highest-ranked artist
       match. Deezer's relevance ranking on a clean artist name is reliable;
       the first hit is almost always the canonical artist.
    2. ``/artist/{id}/top?limit={N}`` — fetch the top tracks for that artist.

    Returned tracks have ``release_year = None`` (Deezer's top endpoint omits
    ``release_date``). This is fine for game play since gameplay only needs
    ``preview_url``; year is purely a display field.
    """
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        artist_response = await client.get(
            ARTIST_SEARCH_URL,
            params={"q": artist, "limit": 1},
            timeout=15.0,
        )
        if artist_response.status_code != 200:
            raise DeezerAPIError(artist_response.status_code, artist_response.text[:500])
        artist_body = artist_response.json()
        _raise_if_error_envelope(artist_body)
        candidates = artist_body.get("data", [])
        if not candidates:
            return []
        artist_id = candidates[0]["id"]
        top_url = f"{API_BASE}/artist/{artist_id}/top"
        return await _get_tracks(
            top_url,
            {"limit": min(limit, MAX_LIMIT)},
            http_client=client,
        )
    finally:
        if owns_client:
            await client.aclose()


async def search_tracks_by_year(
    year: int,
    *,
    limit: int = 50,
    http_client: httpx.AsyncClient | None = None,
) -> list[Track]:
    """Tracks whose release year equals ``year``.

    Deezer's search does not expose a true date filter (its ``date:`` qualifier
    is fuzzy free-text — empirically a track titled "(2020 Mix)" matches
    ``date:"2020"`` regardless of actual release year). We therefore:

    1. ``/search?q={year}`` — Deezer's free-text relevance over the year string.
    2. For each result, ``/track/{id}`` to obtain the true ``release_date``.
    3. Keep only rows whose parsed year equals the requested ``year``.

    Bulk-fetch concurrency is bounded by :data:`MAX_CONCURRENT_LOOKUPS`.
    """
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        # Pull a generous candidate set (3x the target ``limit``), since the
        # year filter typically rejects 50–80% of free-text matches.
        candidate_limit = min(limit * 3, MAX_LIMIT)
        candidates = await _get_tracks(
            SEARCH_URL,
            {"q": str(year), "limit": candidate_limit},
            http_client=client,
        )

        sem = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

        async def _hydrate(t: Track) -> Track | None:
            async with sem:
                try:
                    full = await fetch_track(t.spotify_id, http_client=client)
                except DeezerAPIError:
                    return None
            if full.release_year != year:
                return None
            return full

        hydrated = await asyncio.gather(*[_hydrate(t) for t in candidates])
        kept = [t for t in hydrated if t is not None]
        return kept[:limit]
    finally:
        if owns_client:
            await client.aclose()


async def fetch_track(
    track_id: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> Track:
    """Single-track lookup by numeric Deezer id.

    Unlike ``/search``, ``/track/{id}`` includes ``release_date``, so the
    returned :class:`Track` has a populated ``release_year``.
    """
    url = f"{API_BASE}/track/{track_id}"
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        response = await client.get(url, timeout=15.0)
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        raise DeezerAPIError(response.status_code, response.text[:500])
    body = response.json()
    _raise_if_error_envelope(body)
    parsed = _try_parse_track(body)
    if parsed is None:
        raise DeezerAPIError(
            status_code=404,
            detail=f"Track {track_id} not found or has no playable preview.",
        )
    return parsed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_tracks(
    url: str,
    params: dict[str, Any],
    *,
    http_client: httpx.AsyncClient | None,
) -> list[Track]:
    """Fetch a Deezer list envelope, parse each row, drop unparseable rows."""
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        response = await client.get(url, params=params, timeout=15.0)
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        raise DeezerAPIError(response.status_code, response.text[:500])
    body = response.json()
    _raise_if_error_envelope(body)
    tracks: list[Track] = []
    for raw in body.get("data", []):
        parsed = _try_parse_track(raw)
        if parsed is not None:
            tracks.append(parsed)
    return tracks


def _try_parse_track(raw: dict[str, Any]) -> Track | None:
    """Parse a Deezer track row into a :class:`Track`, dropping unusable rows.

    Rows are dropped if the response signals a non-track entity, the preview
    URL is empty (Deezer uses ``""`` for rights-restricted previews — we treat
    that as unplayable), or any required identifier field is missing.
    """
    # Top-level /track/{id} responses do not include the discriminator key
    # ``type``; list-envelope rows do. Accept both shapes.
    if raw.get("type", "track") != "track":
        return None
    preview_url = raw.get("preview")
    if not preview_url:
        return None
    try:
        track_id = str(raw["id"])
        title = raw["title"]
        artist_blob = raw["artist"]
        primary_artist = artist_blob["name"]
    except (KeyError, TypeError):
        return None
    album_blob = raw.get("album") or {}
    album_name = album_blob.get("title", "") or ""
    release_date = raw.get("release_date", "") or ""
    release_year = _parse_release_year(release_date) if release_date else None
    return Track(
        spotify_id=track_id,
        title=title,
        primary_artist=primary_artist,
        album_name=album_name,
        release_year=release_year,
        popularity=None,
        preview_url=preview_url,
    )


def _parse_release_year(release_date: str) -> int | None:
    """Extract the year from a Deezer ``release_date`` like ``2017-03-03``.

    Returns ``None`` on missing / malformed input or values outside [1900, 2100].
    """
    if not release_date:
        return None
    head = release_date[:4]
    try:
        year = int(head)
    except ValueError:
        return None
    if year < 1900 or year > 2100:
        return None
    return year


def _raise_if_error_envelope(body: Any) -> None:
    """Deezer signals errors with a 200 + ``{"error": {"code": N, "message": "..."}}``.

    The ``/search``, ``/artist/{id}/top``, and ``/track/{id}`` endpoints all
    return a 200 status even when the request is malformed (unknown track id,
    invalid query syntax, etc.); the failure shows up as an ``error`` key in
    the JSON envelope. Translating that to an exception keeps the public API
    of this module symmetric with the HTTP-error path.
    """
    if isinstance(body, dict) and "error" in body:
        err = body["error"]
        code = err.get("code", 0) if isinstance(err, dict) else 0
        message = err.get("message", "Unknown Deezer error") if isinstance(err, dict) else str(err)
        raise DeezerAPIError(status_code=int(code), detail=str(message))
