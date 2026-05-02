"""Unit tests for :mod:`heardle.deezer`.

All network calls are mocked via ``respx``. The tests confirm our parsing
matches Deezer's actual response shape (probed live before writing the
module — see module docstring of ``heardle.deezer``), that empty-preview
rows are dropped before they can become unwinnable game targets, and that
the year-search bulk-fetch correctly filters by true ``release_date``.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from heardle.deezer import (
    DeezerAPIError,
    _parse_release_year,
    _try_parse_track,
    fetch_track,
    search_tracks,
    search_tracks_by_artist,
    search_tracks_by_year,
)


def _sample_row(**overrides: object) -> dict[str, object]:
    """Return a well-formed Deezer search-result row with test-suitable defaults.

    Mirrors the live shape probed via ``GET /search?q=shape+of+you`` —
    notably ``preview`` (not ``preview_url``), nested ``artist.name``,
    nested ``album.title``, and the ``"type": "track"`` discriminator.
    """
    base: dict[str, object] = {
        "id": 142986204,
        "title": "Shape of You",
        "duration": 233,
        "preview": "https://cdnt-preview.dzcdn.net/api/1/.../preview.mp3",
        "artist": {"id": 384236, "name": "Ed Sheeran"},
        "album": {"id": 15478674, "title": "÷ (Deluxe)"},
        "type": "track",
    }
    base.update(overrides)
    return base


def _sample_track_row(**overrides: object) -> dict[str, object]:
    """Top-level ``/track/{id}`` shape: same as a search row plus ``release_date``."""
    base = _sample_row(**overrides)
    base.setdefault("release_date", "2017-03-03")
    return base


# ---------------------------------------------------------------------------
# _parse_release_year
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Rationale: canonical Deezer shape — a date-only string ``YYYY-MM-DD``.
        ("2017-03-03", 2017),
        # Rationale: just-the-year representations are rare but seen on older
        # catalogue entries; the head-slice approach handles them too.
        ("1975", 1975),
        # Rationale: empty / malformed input must reject cleanly rather than crash.
        ("", None),
        ("not-a-date", None),
        # Rationale: out-of-range sentinels guard against malformed metadata
        # leaking through to game logic with a nonsense year.
        ("1800-01-01", None),
        ("3000-01-01", None),
    ],
)
def test_parse_release_year(raw: str, expected: int | None) -> None:
    assert _parse_release_year(raw) == expected


# ---------------------------------------------------------------------------
# _try_parse_track
# ---------------------------------------------------------------------------


def test_try_parse_happy_path_search_row() -> None:
    """Rationale: baseline — a well-formed search row maps to a Track with
    ``release_year=None`` (search envelope does not include release_date)."""
    t = _try_parse_track(_sample_row())
    assert t is not None
    assert t.spotify_id == "142986204"
    assert t.title == "Shape of You"
    assert t.primary_artist == "Ed Sheeran"
    assert t.album_name == "÷ (Deluxe)"
    assert t.preview_url == "https://cdnt-preview.dzcdn.net/api/1/.../preview.mp3"
    # No release_date in /search rows → year stays None for downstream display.
    assert t.release_year is None
    # Deezer does not expose a popularity score on the public API.
    assert t.popularity is None


def test_try_parse_happy_path_track_row_populates_year() -> None:
    """Rationale: the ``/track/{id}`` envelope includes ``release_date``,
    which must round-trip through to ``Track.release_year``."""
    t = _try_parse_track(_sample_track_row())
    assert t is not None
    assert t.release_year == 2017


def test_try_parse_drops_empty_preview() -> None:
    """Rationale: Deezer signals rights-restricted previews with an empty
    string ``""`` rather than null. A track with no playable preview is an
    unwinnable game target — drop it at parse time so the uniform sample
    over the correct pool can never select it."""
    row = _sample_row(preview="")
    assert _try_parse_track(row) is None


def test_try_parse_drops_missing_preview() -> None:
    """Rationale: defensive — if the field is missing entirely (envelope
    drift), behaviour must mirror the empty-string case."""
    row = _sample_row()
    del row["preview"]
    assert _try_parse_track(row) is None


def test_try_parse_drops_non_track_type() -> None:
    """Rationale: ``/search`` mixes albums, artists, podcasts, and playlists
    into related-entity envelopes when the query straddles types. We filter
    on the ``type`` discriminator to keep the guess pool song-only."""
    assert _try_parse_track(_sample_row(type="album")) is None
    assert _try_parse_track(_sample_row(type="podcast")) is None


def test_try_parse_drops_missing_artist() -> None:
    """Rationale: a track row with no artist is malformed; the autocomplete
    label and result rendering both depend on a non-empty artist string."""
    row = _sample_row()
    del row["artist"]
    assert _try_parse_track(row) is None


# ---------------------------------------------------------------------------
# search_tracks
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_tracks_returns_parsed() -> None:
    """Rationale: end-to-end of the autocomplete path — confirms parameters
    are wired and the parser runs against a mocked Deezer envelope."""
    respx.get("https://api.deezer.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _sample_row(id=1, title="Track 1"),
                    _sample_row(id=2, title="Track 2"),
                ],
                "total": 2,
            },
        )
    )
    tracks = await search_tracks("ed sheeran", limit=5)
    assert [t.spotify_id for t in tracks] == ["1", "2"]


@respx.mock
async def test_search_tracks_drops_empty_previews_inline() -> None:
    """Rationale: the parse-time preview filter must operate on each row of
    the search envelope independently; one empty-preview row should not
    cause adjacent playable rows to be discarded."""
    respx.get("https://api.deezer.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _sample_row(id=1, title="Playable"),
                    _sample_row(id=2, title="Restricted", preview=""),
                    _sample_row(id=3, title="Also Playable"),
                ],
                "total": 3,
            },
        )
    )
    tracks = await search_tracks("anything")
    assert [t.spotify_id for t in tracks] == ["1", "3"]


@respx.mock
async def test_search_tracks_empty_results() -> None:
    """Rationale: Deezer returns ``{"data": [], "total": 0}`` for unmatched
    queries; tolerate it cleanly rather than raising."""
    respx.get("https://api.deezer.com/search").mock(
        return_value=httpx.Response(200, json={"data": [], "total": 0})
    )
    assert await search_tracks("asdfghjk_nothing_matches") == []


@respx.mock
async def test_search_tracks_raises_on_5xx() -> None:
    """Rationale: non-2xx responses must surface as ``DeezerAPIError`` so the
    route layer can render a helpful error rather than swallowing silently."""
    respx.get("https://api.deezer.com/search").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    with pytest.raises(DeezerAPIError) as exc:
        await search_tracks("anything")
    assert exc.value.status_code == 503


@respx.mock
async def test_search_tracks_raises_on_error_envelope() -> None:
    """Rationale: Deezer signals quota / parameter errors with a 200 status
    and ``{"error": {"code": N, "message": ...}}``. Translate these to the
    same exception type as HTTP errors so callers handle them uniformly."""
    respx.get("https://api.deezer.com/search").mock(
        return_value=httpx.Response(
            200,
            json={"error": {"code": 4, "message": "Quota limit exceeded", "type": "Exception"}},
        )
    )
    with pytest.raises(DeezerAPIError) as exc:
        await search_tracks("anything")
    assert exc.value.status_code == 4


# ---------------------------------------------------------------------------
# search_tracks_by_artist — two-hop (resolve id, then top tracks)
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_tracks_by_artist_two_hop() -> None:
    """Rationale: artist-source flow does ``/search/artist`` to resolve the
    name to an id, then ``/artist/{id}/top`` for the canonical top tracks.
    Verify both hops fire and the parser runs on the second envelope."""
    respx.get("https://api.deezer.com/search/artist").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": 384236, "name": "Ed Sheeran"}], "total": 1},
        )
    )
    respx.get("https://api.deezer.com/artist/384236/top").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _sample_row(id=1, title="Shape of You"),
                    _sample_row(id=2, title="Perfect"),
                ],
                "total": 2,
            },
        )
    )
    tracks = await search_tracks_by_artist("ed sheeran", limit=10)
    assert [t.title for t in tracks] == ["Shape of You", "Perfect"]


@respx.mock
async def test_search_tracks_by_artist_no_match_returns_empty() -> None:
    """Rationale: an artist query that resolves to zero artists must short-
    circuit and return ``[]`` — the route layer maps that to a 400."""
    respx.get("https://api.deezer.com/search/artist").mock(
        return_value=httpx.Response(200, json={"data": [], "total": 0})
    )
    assert await search_tracks_by_artist("asdfghjk_no_artist") == []


# ---------------------------------------------------------------------------
# search_tracks_by_year — bulk-hydrate + filter
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_tracks_by_year_filters_by_true_release_date() -> None:
    """Rationale: Deezer's ``/search?q=YYYY`` returns free-text matches that
    can include a 2020 remix of a 1990 track. We must hydrate each row via
    ``/track/{id}`` and keep only the rows whose true ``release_date`` year
    equals the requested year."""
    respx.get("https://api.deezer.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _sample_row(id=1),
                    _sample_row(id=2),
                    _sample_row(id=3),
                ],
                "total": 3,
            },
        )
    )
    track_url_re = re.compile(r"^https://api\.deezer\.com/track/(\d+)$")

    def _track_responder(request: httpx.Request) -> httpx.Response:
        match = track_url_re.match(str(request.url))
        assert match is not None
        track_id = int(match.group(1))
        # IDs 1 and 3 are genuinely 2020 releases; ID 2 is a 2019 track that
        # only matches the year string by coincidence (e.g. title contains "2020").
        year_for_id = {1: "2020-05-01", 2: "2019-12-31", 3: "2020-11-15"}
        return httpx.Response(
            200, json=_sample_track_row(id=track_id, release_date=year_for_id[track_id])
        )

    respx.get(track_url_re).mock(side_effect=_track_responder)
    tracks = await search_tracks_by_year(2020, limit=10)
    assert {t.spotify_id for t in tracks} == {"1", "3"}


@respx.mock
async def test_search_tracks_by_year_drops_lookup_failures() -> None:
    """Rationale: a single failing per-row ``/track/{id}`` lookup must not
    abort the whole batch — drop the offending row and keep the rest."""
    respx.get("https://api.deezer.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [_sample_row(id=10), _sample_row(id=11)],
                "total": 2,
            },
        )
    )
    track_url_re = re.compile(r"^https://api\.deezer\.com/track/(\d+)$")

    def _responder(request: httpx.Request) -> httpx.Response:
        match = track_url_re.match(str(request.url))
        assert match is not None
        track_id = int(match.group(1))
        if track_id == 11:
            return httpx.Response(500, text="Backend exploded")
        return httpx.Response(200, json=_sample_track_row(id=track_id, release_date="2020-01-01"))

    respx.get(track_url_re).mock(side_effect=_responder)
    tracks = await search_tracks_by_year(2020, limit=10)
    assert [t.spotify_id for t in tracks] == ["10"]


# ---------------------------------------------------------------------------
# fetch_track
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_track_single() -> None:
    """Rationale: ``/track/{id}`` returns a flat track envelope; verify we
    parse it and surface a populated ``release_year``."""
    respx.get("https://api.deezer.com/track/999").mock(
        return_value=httpx.Response(200, json=_sample_track_row(id=999))
    )
    t = await fetch_track("999")
    assert t.spotify_id == "999"
    assert t.release_year == 2017


@respx.mock
async def test_fetch_track_missing_raises() -> None:
    """Rationale: an unknown track id returns a Deezer error envelope at 200
    status; our wrapper surfaces it as ``DeezerAPIError`` so callers can
    map to a 404-flavoured response."""
    respx.get("https://api.deezer.com/track/does_not_exist").mock(
        return_value=httpx.Response(
            200,
            json={"error": {"code": 800, "message": "no data", "type": "DataException"}},
        )
    )
    with pytest.raises(DeezerAPIError) as exc:
        await fetch_track("does_not_exist")
    assert exc.value.status_code == 800


@respx.mock
async def test_fetch_track_empty_preview_raises_404() -> None:
    """Rationale: a track with an empty preview cannot drive game playback;
    the wrapper must reject it cleanly so a caller cannot accidentally seed
    an unwinnable game session by id-lookup."""
    respx.get("https://api.deezer.com/track/555").mock(
        return_value=httpx.Response(200, json=_sample_track_row(id=555, preview=""))
    )
    with pytest.raises(DeezerAPIError) as exc:
        await fetch_track("555")
    assert exc.value.status_code == 404
