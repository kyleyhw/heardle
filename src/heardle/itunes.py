"""iTunes Search API wrapper — free, no auth, returns 30s preview MP3 URLs.

Why this module exists
----------------------
We want a "clone and play" experience: zero Spotify Developer app, no
Premium requirement, no OAuth. iTunes Search delivers all three, at the
cost of game fidelity — Apple chooses the 30-second window (often the
chorus, not the intro). See ``docs/audio_pipeline.md`` for the full
tradeoff.

Endpoints used
--------------
- ``GET https://itunes.apple.com/search``
  - ``entity=song`` restricts to tracks (no podcasts, videos, albums).
  - ``attribute=artistTerm`` matches against the artist name.
  - ``attribute=releaseYearTerm`` matches against the release year.
- ``GET https://itunes.apple.com/lookup`` — single-track by numeric id.

Response shape
--------------
Envelope: ``{"resultCount": int, "results": [{...}, ...]}``. Track rows
include ``trackId``, ``trackName``, ``artistName``, ``collectionName``,
``releaseDate`` (ISO-8601), ``previewUrl`` (MP4/AAC, ~30s), and a
``kind`` discriminator that we filter on to skip music-video rows.

Rate limits
-----------
iTunes Search is not publicly rate-limited, but common practice is to
keep sustained request rates under ~20/min. The typical game flow is
≈1 request on game creation and one per autocomplete query (client-side
debounced to 300 ms), well under any observed threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from heardle.spotify import Track

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"

# iTunes' per-query cap. For ``search`` this is 200; autocomplete callers
# should pass a much smaller ``limit`` (e.g. 10) to keep the dropdown tidy.
MAX_LIMIT: int = 200


@dataclass(frozen=True)
class ITunesAPIError(RuntimeError):
    """Raised when iTunes returns a non-2xx response or a malformed envelope."""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return f"iTunes API error {self.status_code}: {self.detail}"


async def search_tracks(
    query: str,
    *,
    limit: int = 10,
    country: str = "US",
    http_client: httpx.AsyncClient | None = None,
) -> list[Track]:
    """Free-text song search across iTunes' catalogue, ranked by iTunes' relevance.

    This is the autocomplete endpoint. A debounced frontend call per keystroke
    is fine under iTunes' observed rate behaviour.
    """
    params = {
        "term": query,
        "entity": "song",
        "limit": min(limit, MAX_LIMIT),
        "country": country,
    }
    return await _get_tracks(SEARCH_URL, params, http_client=http_client)


async def search_tracks_by_artist(
    artist: str,
    *,
    limit: int = MAX_LIMIT,
    country: str = "US",
    http_client: httpx.AsyncClient | None = None,
) -> list[Track]:
    """Songs whose artist name matches ``artist``.

    Uses ``attribute=artistTerm`` so the term is matched against the artist
    field specifically, rather than any field. Results are deduplicated on
    ``(title, artist)`` to collapse the multiple album/reissue entries iTunes
    commonly returns for the same recording.
    """
    params = {
        "term": artist,
        "entity": "song",
        "attribute": "artistTerm",
        "limit": min(limit, MAX_LIMIT),
        "country": country,
    }
    tracks = await _get_tracks(SEARCH_URL, params, http_client=http_client)
    return _dedup_by_title_artist(tracks)


async def search_tracks_by_year(
    year: int,
    *,
    limit: int = MAX_LIMIT,
    country: str = "US",
    http_client: httpx.AsyncClient | None = None,
) -> list[Track]:
    """Songs released in the given year, per iTunes' ``releaseYearTerm`` attribute.

    Note that iTunes's year-search quality is uneven — some prolific years
    return the full ``limit``, while quieter years return fewer. The results
    are filtered client-side to guarantee ``release_year == year`` since
    iTunes sometimes returns results from adjacent years.
    """
    params = {
        "term": str(year),
        "entity": "song",
        "attribute": "releaseYearTerm",
        "limit": min(limit, MAX_LIMIT),
        "country": country,
    }
    tracks = await _get_tracks(SEARCH_URL, params, http_client=http_client)
    return [t for t in tracks if t.release_year == year]


async def fetch_track(
    track_id: str,
    *,
    country: str = "US",
    http_client: httpx.AsyncClient | None = None,
) -> Track:
    """Single-track lookup by numeric iTunes id."""
    params = {"id": track_id, "entity": "song", "country": country}
    tracks = await _get_tracks(LOOKUP_URL, params, http_client=http_client)
    if not tracks:
        raise ITunesAPIError(
            status_code=404,
            detail=f"Track {track_id} not found or has no preview.",
        )
    return tracks[0]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_tracks(
    url: str,
    params: dict[str, Any],
    *,
    http_client: httpx.AsyncClient | None,
) -> list[Track]:
    """Fetch the envelope, parse each row, drop malformed / non-song rows."""
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        response = await client.get(url, params=params, timeout=15.0)
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        raise ITunesAPIError(response.status_code, response.text[:500])
    body = response.json()
    tracks: list[Track] = []
    for raw in body.get("results", []):
        parsed = _try_parse_track(raw)
        if parsed is not None:
            tracks.append(parsed)
    return tracks


def _try_parse_track(raw: dict[str, Any]) -> Track | None:
    """Parse an iTunes response row into a :class:`Track`, dropping unusable rows.

    Rows are dropped if they are not songs (``kind != "song"``), lack a
    ``previewUrl`` (cannot be played), lack a releaseDate (cannot be filtered
    by year), or are missing any of the required identifier fields.
    """
    if raw.get("kind") != "song":
        return None
    preview_url = raw.get("previewUrl")
    if not preview_url:
        return None
    try:
        track_id = str(raw["trackId"])
        title = raw["trackName"]
        primary_artist = raw["artistName"]
    except (KeyError, TypeError):
        return None
    album_name = raw.get("collectionName", "") or ""
    release_date = raw.get("releaseDate", "") or ""
    release_year = _parse_release_year(release_date)
    if release_year is None:
        return None
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
    """Extract the year from an ISO-8601 ``releaseDate`` like ``2017-01-06T12:00:00Z``.

    Returns ``None`` on missing / malformed input, and applies the same
    sanity range used by :mod:`heardle.spotify` (1900–2100).
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


def _dedup_by_title_artist(tracks: list[Track]) -> list[Track]:
    """Preserve first occurrence of each ``(title, primary_artist)`` pair.

    iTunes returns the same song on multiple albums (original + deluxe +
    compilations). The first hit is typically the canonical release.
    """
    seen: set[tuple[str, str]] = set()
    out: list[Track] = []
    for t in tracks:
        key = (t.title, t.primary_artist)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out
