"""Unit tests for :mod:`heardle.itunes`.

All network calls are mocked via ``respx``. The tests confirm our parsing
matches iTunes' actual response shape and that non-song rows are correctly
filtered out.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from heardle.itunes import (
    ITunesAPIError,
    _parse_release_year,
    _try_parse_track,
    fetch_track,
    search_tracks,
    search_tracks_by_artist,
    search_tracks_by_year,
)


def _sample_song(**overrides: object) -> dict[str, object]:
    """Return a well-formed iTunes song row with test-suitable defaults."""
    base: dict[str, object] = {
        "wrapperType": "track",
        "kind": "song",
        "trackId": 1440833179,
        "trackName": "Shape of You",
        "artistName": "Ed Sheeran",
        "collectionName": "÷ (Deluxe)",
        "releaseDate": "2017-01-06T12:00:00Z",
        "previewUrl": "https://audio-ssl.itunes.apple.com/foo.m4a",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _parse_release_year
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Rationale: full ISO-8601 timestamp — the common case from iTunes.
        ("2017-01-06T12:00:00Z", 2017),
        # Rationale: date-only strings also occur on older catalogue entries.
        ("1975-10-31", 1975),
        # Rationale: empty / malformed input must be rejected cleanly.
        ("", None),
        ("not-a-date", None),
        ("1800-01-01", None),
        ("3000-01-01", None),
    ],
)
def test_parse_release_year(raw: str, expected: int | None) -> None:
    assert _parse_release_year(raw) == expected


# ---------------------------------------------------------------------------
# _try_parse_track
# ---------------------------------------------------------------------------


def test_try_parse_happy_path() -> None:
    """Rationale: baseline — a well-formed song must map to a complete Track."""
    t = _try_parse_track(_sample_song())
    assert t is not None
    assert t.spotify_id == "1440833179"
    assert t.title == "Shape of You"
    assert t.primary_artist == "Ed Sheeran"
    assert t.album_name == "÷ (Deluxe)"
    assert t.release_year == 2017
    assert t.preview_url == "https://audio-ssl.itunes.apple.com/foo.m4a"
    # iTunes does not expose popularity, so the field must stay ``None``.
    assert t.popularity is None


def test_try_parse_drops_non_song_kinds() -> None:
    """Rationale: iTunes mixes music-video, book, podcast-episode rows into results
    even with ``entity=song``. Dropping them keeps the guess pool focused."""
    assert _try_parse_track(_sample_song(kind="music-video")) is None
    assert _try_parse_track(_sample_song(kind="podcast-episode")) is None


def test_try_parse_drops_missing_preview_url() -> None:
    """Rationale: a track with no preview is unplayable — drop it rather than
    letting it surface as a selectable game target that then fails silently."""
    row = _sample_song()
    del row["previewUrl"]
    assert _try_parse_track(row) is None


def test_try_parse_drops_missing_release_date() -> None:
    """Rationale: release_year feeds the year-source filter; a null year would
    silently bypass that filter."""
    row = _sample_song()
    del row["releaseDate"]
    assert _try_parse_track(row) is None


# ---------------------------------------------------------------------------
# search_tracks (autocomplete)
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_tracks_returns_parsed() -> None:
    """Rationale: the autocomplete path — confirms parameters are wired and the
    parser runs end-to-end on a mocked iTunes response."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "resultCount": 2,
                "results": [
                    _sample_song(trackId=1, trackName="Track 1"),
                    _sample_song(trackId=2, trackName="Track 2"),
                ],
            },
        )
    )
    tracks = await search_tracks("ed sheeran", limit=5)
    assert [t.spotify_id for t in tracks] == ["1", "2"]


@respx.mock
async def test_search_tracks_empty_results() -> None:
    """Rationale: iTunes returns an empty ``results`` list rather than 404 for a
    query with no matches. We must tolerate that cleanly."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(200, json={"resultCount": 0, "results": []})
    )
    assert await search_tracks("asdfghjk_nothing_matches") == []


@respx.mock
async def test_search_tracks_raises_on_5xx() -> None:
    """Rationale: non-2xx responses must surface as ``ITunesAPIError`` — the
    caller can then fall back or render a helpful error."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    with pytest.raises(ITunesAPIError) as exc:
        await search_tracks("anything")
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# search_tracks_by_artist — dedup
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_tracks_by_artist_dedups_reissues() -> None:
    """Rationale: iTunes returns the same recording on multiple albums (original
    + deluxe + compilations); a game target pool full of duplicates would make
    the same song get picked disproportionately often."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "resultCount": 3,
                "results": [
                    _sample_song(
                        trackId=1,
                        trackName="Shape of You",
                        collectionName="÷ (Deluxe)",
                    ),
                    _sample_song(
                        trackId=2,
                        trackName="Shape of You",
                        collectionName="÷",
                    ),
                    _sample_song(
                        trackId=3,
                        trackName="Perfect",
                        collectionName="÷",
                    ),
                ],
            },
        )
    )
    tracks = await search_tracks_by_artist("ed sheeran")
    assert [t.title for t in tracks] == ["Shape of You", "Perfect"]


# ---------------------------------------------------------------------------
# search_tracks_by_year — year-adjacent filter
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_tracks_by_year_filters_adjacent_years() -> None:
    """Rationale: iTunes' ``releaseYearTerm`` matches loosely — it returns rows
    from adjacent years when data is sparse. Client-side filter guarantees
    correctness."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "resultCount": 3,
                "results": [
                    _sample_song(trackId=1, releaseDate="2020-05-01"),
                    _sample_song(trackId=2, releaseDate="2019-12-31"),
                    _sample_song(trackId=3, releaseDate="2020-11-15"),
                ],
            },
        )
    )
    tracks = await search_tracks_by_year(2020)
    assert {t.spotify_id for t in tracks} == {"1", "3"}


# ---------------------------------------------------------------------------
# fetch_track
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_track_single() -> None:
    """Rationale: /lookup returns the same envelope shape as /search, but with a
    single result. Verify we peel the list correctly."""
    respx.get("https://itunes.apple.com/lookup").mock(
        return_value=httpx.Response(
            200,
            json={"resultCount": 1, "results": [_sample_song(trackId=999)]},
        )
    )
    t = await fetch_track("999")
    assert t.spotify_id == "999"


@respx.mock
async def test_fetch_track_missing_raises() -> None:
    """Rationale: iTunes' /lookup returns 200 with an empty ``results`` list
    when the id is not found; our wrapper should translate that to 404-flavoured
    error for the caller."""
    respx.get("https://itunes.apple.com/lookup").mock(
        return_value=httpx.Response(200, json={"resultCount": 0, "results": []})
    )
    with pytest.raises(ITunesAPIError) as exc:
        await fetch_track("does_not_exist")
    assert exc.value.status_code == 404
