"""Unit tests for :mod:`heardle.api` routes (iTunes-backed mode).

Uses ``fastapi.testclient.TestClient`` with dependency overrides and
``respx`` to intercept iTunes API calls. Covers URL wiring, session
handling, game-state transitions, the preview-URL flow, and the
``AUDIO_BACKEND`` toggle.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from heardle import api as api_mod
from heardle.api import GameSession
from heardle.config import Settings
from heardle.game import initial_state
from heardle.spotify import Track


def _sample_itunes_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "wrapperType": "track",
        "kind": "song",
        "trackId": 100,
        "trackName": "Test Track",
        "artistName": "Test Artist",
        "collectionName": "Test Album",
        "releaseDate": "2020-01-01T12:00:00Z",
        "previewUrl": "https://audio-ssl.itunes.apple.com/test.m4a",
    }
    base.update(overrides)
    return base


def _track(**overrides: object) -> Track:
    kw: dict[str, object] = {
        "spotify_id": "100",
        "title": "Test Track",
        "primary_artist": "Test Artist",
        "album_name": "Test Album",
        "release_year": 2020,
        "popularity": None,
        "preview_url": "https://audio-ssl.itunes.apple.com/test.m4a",
    }
    kw.update(overrides)
    return Track(**kw)  # type: ignore[arg-type]


@pytest.fixture
def itunes_settings() -> Settings:
    return Settings(
        audio_backend="itunes",
        spotify_client_id="",
        spotify_client_secret="",
        spotify_redirect_uri="http://127.0.0.1:8000/callback",
        session_secret="fake_session",  # pragma: allowlist secret
        game_state_secret="fake_game_state",  # pragma: allowlist secret
        popular_corpus_path="data/popular_corpus.parquet",
        popularity_threshold=25,
        year_threshold=2000,
        itunes_country="US",
    )


@pytest.fixture
def client(itunes_settings: Settings) -> Generator[TestClient, None, None]:
    api_mod._games.clear()
    api_mod.app.dependency_overrides[api_mod.get_settings] = lambda: itunes_settings
    try:
        yield TestClient(api_mod.app)
    finally:
        api_mod.app.dependency_overrides.clear()
        api_mod._games.clear()


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def test_index_renders(client: TestClient) -> None:
    """Rationale: landing page must respond 200; catches gross template wiring bugs."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Heardle" in response.text


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------


def test_autocomplete_empty_query_returns_empty(client: TestClient) -> None:
    """Rationale: empty query must not trigger an iTunes round-trip."""
    response = client.get("/autocomplete", params={"q": ""})
    assert response.status_code == 200
    assert response.json() == []


@respx.mock
def test_autocomplete_proxies_to_itunes(client: TestClient) -> None:
    """Rationale: the keystroke-time autocomplete hits iTunes and reshapes the
    response into ``{id, title, artist, year}`` rows the frontend expects."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "resultCount": 1,
                "results": [
                    _sample_itunes_row(trackId=42, trackName="Hello", artistName="Adele"),
                ],
            },
        )
    )
    response = client.get("/autocomplete", params={"q": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body == [
        {"id": "42", "title": "Hello", "artist": "Adele", "year": 2020},
    ]


@respx.mock
def test_autocomplete_merges_game_correct_pool(client: TestClient) -> None:
    """Rationale: the correct answer must be findable in autocomplete even if
    iTunes' global ranking buries it — we union the game's correct_pool on
    substring match before deduping."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "resultCount": 1,
                "results": [_sample_itunes_row(trackId=999, trackName="Noise")],
            },
        )
    )
    target = _track(spotify_id="obscure", title="Obscure Deep Cut", primary_artist="X")
    api_mod._games["game_abc"] = GameSession(
        state=initial_state(target.spotify_id),
        target=target,
        correct_pool={target.spotify_id: target},
    )
    response = client.get("/autocomplete", params={"q": "obscure", "game_id": "game_abc"})
    ids = [row["id"] for row in response.json()]
    assert "obscure" in ids


# ---------------------------------------------------------------------------
# Game new
# ---------------------------------------------------------------------------


@respx.mock
def test_game_new_artist_source(client: TestClient) -> None:
    """Rationale: the artist-source flow should call iTunes, pick a target,
    store the session, and 303-redirect to the game page."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "resultCount": 2,
                "results": [
                    _sample_itunes_row(trackId=1, trackName="A"),
                    _sample_itunes_row(trackId=2, trackName="B"),
                ],
            },
        )
    )
    response = client.post(
        "/game/new",
        data={"source_type": "artist", "source_value": "ed sheeran"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    # One game must now exist with a target drawn from the pool.
    assert len(api_mod._games) == 1
    session = next(iter(api_mod._games.values()))
    assert session.target.spotify_id in {"1", "2"}


@respx.mock
def test_game_new_empty_pool_returns_400(client: TestClient) -> None:
    """Rationale: an artist name that resolves to zero playable tracks must
    surface a 400 rather than a 500 with an empty-list crash."""
    respx.get("https://itunes.apple.com/search").mock(
        return_value=httpx.Response(200, json={"resultCount": 0, "results": []})
    )
    response = client.post(
        "/game/new",
        data={"source_type": "artist", "source_value": "asdfghjk_nomatch"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_game_new_rejects_spotify_backend(itunes_settings: Settings) -> None:
    """Rationale: ``AUDIO_BACKEND=spotify`` is reserved for a future re-enable;
    the current code base must 503 that branch rather than silently proceeding."""
    spotify_settings = replace(itunes_settings, audio_backend="spotify")
    api_mod._games.clear()
    api_mod.app.dependency_overrides[api_mod.get_settings] = lambda: spotify_settings
    try:
        with TestClient(api_mod.app) as c:
            response = c.post(
                "/game/new",
                data={"source_type": "artist", "source_value": "x"},
                follow_redirects=False,
            )
        assert response.status_code == 503
    finally:
        api_mod.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Game page / guess / skip / preview
# ---------------------------------------------------------------------------


def _seed_game(target: Track | None = None) -> str:
    """Prime ``_games`` with a fresh session; return the game_id."""
    if target is None:
        target = _track()
    gid = "test_game_0"
    api_mod._games[gid] = GameSession(
        state=initial_state(target.spotify_id),
        target=target,
        correct_pool={target.spotify_id: target},
    )
    return gid


def test_game_page_renders(client: TestClient) -> None:
    """Rationale: smoke-test for the full game template with a seeded session."""
    game_id = _seed_game()
    response = client.get(f"/game/{game_id}")
    assert response.status_code == 200
    assert 'data-game-id="' in response.text


def test_game_page_404_for_unknown_id(client: TestClient) -> None:
    response = client.get("/game/does_not_exist")
    assert response.status_code == 404


def test_guess_correct_transitions_to_finished(client: TestClient) -> None:
    """Rationale: correct guess at round 0 ends the game as a win."""
    target = _track(spotify_id="target_id")
    game_id = _seed_game(target)
    response = client.post(f"/game/{game_id}/guess", data={"guess_track_id": "target_id"})
    assert response.status_code == 200
    assert api_mod._games[game_id].state.finished is True
    assert api_mod._games[game_id].state.won is True


def test_guess_wrong_advances_round(client: TestClient) -> None:
    game_id = _seed_game(_track(spotify_id="target_id"))
    response = client.post(f"/game/{game_id}/guess", data={"guess_track_id": "some_other_id"})
    assert response.status_code == 200
    assert api_mod._games[game_id].state.round_index == 1
    assert api_mod._games[game_id].state.finished is False


def test_skip_advances_round(client: TestClient) -> None:
    game_id = _seed_game()
    response = client.post(f"/game/{game_id}/skip")
    assert response.status_code == 200
    assert api_mod._games[game_id].state.round_index == 1
    assert api_mod._games[game_id].state.guesses == (None,)


def test_guess_on_finished_game_returns_409(client: TestClient) -> None:
    target = _track(spotify_id="target_id")
    game_id = _seed_game(target)
    client.post(f"/game/{game_id}/guess", data={"guess_track_id": "target_id"})
    response = client.post(f"/game/{game_id}/guess", data={"guess_track_id": "other"})
    assert response.status_code == 409


def test_preview_returns_url(client: TestClient) -> None:
    """Rationale: the Play button fetches preview_url via this endpoint.

    Keeps the URL out of the rendered HTML; reading it still requires an
    explicit request which a casual cheater would miss.
    """
    target = _track(preview_url="https://example.com/foo.m4a")
    game_id = _seed_game(target)
    response = client.get(f"/game/{game_id}/preview")
    assert response.status_code == 200
    assert response.json() == {"preview_url": "https://example.com/foo.m4a"}


def test_preview_404_for_unknown_game(client: TestClient) -> None:
    response = client.get("/game/does_not_exist/preview")
    assert response.status_code == 404
