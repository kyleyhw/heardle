"""Unit tests for :mod:`heardle.spotify`.

All network calls are mocked via ``respx``. Covers:
- ``_try_parse_track`` parsing edge cases (missing fields, regional variants).
- ``_parse_release_year`` handling of Spotify's three release_date precisions.
- Paginated and non-paginated endpoints end-to-end.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from heardle.spotify import (
    SpotifyAPIError,
    Track,
    _parse_release_year,
    _try_parse_track,
    fetch_artist_top_tracks,
    fetch_playlist_tracks,
    fetch_track,
    search_tracks_by_year,
)

TEST_TOKEN = "test_access_token"


# ---------------------------------------------------------------------------
# _parse_release_year
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "release_date,expected",
    [
        # Rationale: Spotify's three documented precisions.
        ("2024-03-15", 2024),
        ("2024-03", 2024),
        ("2024", 2024),
        # Rationale: remastered historical recordings do exist on Spotify.
        ("1955", 1955),
        # Rationale: malformed / empty / absurd inputs must fail cleanly.
        ("", None),
        ("not-a-date", None),
        ("1800", None),  # pre-recording era, almost certainly malformed metadata
        ("3000", None),  # clearly nonsense
    ],
)
def test_parse_release_year(release_date: str, expected: int | None) -> None:
    assert _parse_release_year(release_date) == expected


# ---------------------------------------------------------------------------
# _try_parse_track
# ---------------------------------------------------------------------------


def _minimal_track_json(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "spotify_id_xyz",
        "name": "Track Title",
        # ``type`` is the discriminator Spotify sets on every track object; the
        # playlist-items filter in ``fetch_playlist_tracks`` relies on it to skip
        # podcast episodes, so test fixtures must match the real schema.
        "type": "track",
        "artists": [{"name": "Primary Artist"}, {"name": "Feature Artist"}],
        "album": {"name": "Album Name", "release_date": "2020-07-01"},
        "popularity": 42,
    }
    base.update(overrides)
    return base


def test_try_parse_track_happy_path() -> None:
    """A well-formed payload parses to a fully-populated ``Track``.

    Rationale: baseline — establishes the mapping from Spotify JSON fields to
    our dataclass before edge cases below.
    """
    result = _try_parse_track(_minimal_track_json())
    assert result == Track(
        spotify_id="spotify_id_xyz",
        title="Track Title",
        primary_artist="Primary Artist",
        album_name="Album Name",
        release_year=2020,
        popularity=42,
    )


def test_try_parse_track_handles_null_popularity() -> None:
    """A ``popularity: None`` must parse; ``popularity`` is optional post-Feb-2026.

    Rationale: apps in Development Mode no longer receive this field — a dropped
    track would silently shrink the corpus. See ``docs/corpus_threshold.md``.
    """
    result = _try_parse_track(_minimal_track_json(popularity=None))
    assert result is not None
    assert result.popularity is None


def test_try_parse_track_returns_none_on_missing_artists() -> None:
    """A track with no artists is unplayable in a guess game — drop it.

    Rationale: such entries appear for some region-locked or stripped catalogue
    items. Returning ``None`` rather than raising keeps stream parsing robust.
    """
    assert _try_parse_track(_minimal_track_json(artists=[])) is None


def test_try_parse_track_returns_none_on_missing_release_date() -> None:
    """No release date → cannot be placed in any year filter — drop it.

    Rationale: year-search correctness depends on release_year being present.
    """
    broken = _minimal_track_json(album={"name": "X", "release_date": ""})
    assert _try_parse_track(broken) is None


# ---------------------------------------------------------------------------
# fetch_playlist_tracks
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_playlist_tracks_single_page() -> None:
    """A playlist that fits in one page returns all its tracks.

    Rationale: covers the no-pagination path without the extra complexity of
    ``next`` link handling.
    """
    respx.get("https://api.spotify.com/v1/playlists/PLID/tracks?limit=50").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"track": _minimal_track_json(id="t1", name="Track 1")},
                    {"track": _minimal_track_json(id="t2", name="Track 2")},
                ],
                "next": None,
            },
        )
    )

    tracks = await fetch_playlist_tracks("PLID", TEST_TOKEN)
    assert [t.spotify_id for t in tracks] == ["t1", "t2"]
    assert tracks[0].title == "Track 1"


@respx.mock
async def test_fetch_playlist_tracks_follows_next_link() -> None:
    """Pagination is followed until ``next`` is null.

    Rationale: a playlist over 50 tracks returns a ``next`` URL; missing this
    causes silent truncation, which is the worst failure mode for a guess game.
    """
    respx.get("https://api.spotify.com/v1/playlists/PLID/tracks?limit=50").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"track": _minimal_track_json(id="t1")}],
                "next": "https://api.spotify.com/v1/playlists/PLID/tracks?offset=50&limit=50",
            },
        )
    )
    respx.get("https://api.spotify.com/v1/playlists/PLID/tracks?offset=50&limit=50").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"track": _minimal_track_json(id="t2")}], "next": None},
        )
    )

    tracks = await fetch_playlist_tracks("PLID", TEST_TOKEN)
    assert [t.spotify_id for t in tracks] == ["t1", "t2"]


@respx.mock
async def test_fetch_playlist_tracks_skips_episodes_and_deleted() -> None:
    """Non-track items and ``track: null`` items must be skipped.

    Rationale: Spotify's playlist endpoint returns podcasts (``type: episode``)
    and null placeholders for deleted items; either would break downstream code
    that assumes a ``Track``.
    """
    respx.get("https://api.spotify.com/v1/playlists/PLID/tracks?limit=50").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"track": None},
                    {"track": {"type": "episode", "id": "episode_id"}},
                    {"track": _minimal_track_json(id="valid_track")},
                ],
                "next": None,
            },
        )
    )

    tracks = await fetch_playlist_tracks("PLID", TEST_TOKEN)
    assert len(tracks) == 1
    assert tracks[0].spotify_id == "valid_track"


@respx.mock
async def test_fetch_playlist_tracks_raises_on_error() -> None:
    """A 404 playlist propagates as ``SpotifyAPIError``.

    Rationale: the route layer converts this to an HTTP response; the wrapper
    must not paper over it.
    """
    respx.get("https://api.spotify.com/v1/playlists/BAD/tracks?limit=50").mock(
        return_value=httpx.Response(404, text='{"error":{"status":404}}')
    )

    with pytest.raises(SpotifyAPIError) as exc:
        await fetch_playlist_tracks("BAD", TEST_TOKEN)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# fetch_artist_top_tracks
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_artist_top_tracks_default_market() -> None:
    """Returns the ``tracks`` list parsed through ``_try_parse_track``.

    Rationale: this endpoint is not paginated, so the parsing path is all we
    need to verify once the market parameter is in place.
    """
    respx.get("https://api.spotify.com/v1/artists/ARTID/top-tracks?market=US").mock(
        return_value=httpx.Response(
            200,
            json={
                "tracks": [
                    _minimal_track_json(id="a1"),
                    _minimal_track_json(id="a2"),
                ]
            },
        )
    )

    tracks = await fetch_artist_top_tracks("ARTID", TEST_TOKEN)
    assert [t.spotify_id for t in tracks] == ["a1", "a2"]


# ---------------------------------------------------------------------------
# search_tracks_by_year
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_tracks_by_year_paginates() -> None:
    """Search pagination uses the ``tracks.next`` nested envelope.

    Rationale: the ``/search`` response shape differs from ``/playlists`` — the
    pagination link is nested one level deeper. Easy regression.
    """
    respx.get("https://api.spotify.com/v1/search?q=year:2023&type=track&market=US&limit=50").mock(
        return_value=httpx.Response(
            200,
            json={
                "tracks": {
                    "items": [_minimal_track_json(id="s1")],
                    "next": (
                        "https://api.spotify.com/v1/search"
                        "?q=year:2023&type=track&market=US&limit=50&offset=50"
                    ),
                }
            },
        )
    )
    respx.get(
        "https://api.spotify.com/v1/search" "?q=year:2023&type=track&market=US&limit=50&offset=50"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "tracks": {
                    "items": [_minimal_track_json(id="s2")],
                    "next": None,
                }
            },
        )
    )

    tracks = await search_tracks_by_year(2023, TEST_TOKEN)
    assert [t.spotify_id for t in tracks] == ["s1", "s2"]


# ---------------------------------------------------------------------------
# fetch_track
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_track_single() -> None:
    """Single-track fetch returns a parsed ``Track``.

    Rationale: used on the game-new path to confirm the target actually plays in
    the user's market before the SDK tries it.
    """
    respx.get("https://api.spotify.com/v1/tracks/TID?market=US").mock(
        return_value=httpx.Response(200, json=_minimal_track_json(id="TID"))
    )

    track = await fetch_track("TID", TEST_TOKEN)
    assert track.spotify_id == "TID"
