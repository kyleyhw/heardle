"""Unit tests for :mod:`heardle.api` routes.

Uses ``fastapi.testclient.TestClient`` with dependency overrides to avoid
hitting Spotify. Live OAuth and playback routes (anything that would call
out to accounts.spotify.com or api.spotify.com for real) are covered by
integration tests in a later phase; here we verify URL wiring, session
handling, and game-state transitions exposed through the routes.
"""

from __future__ import annotations

from collections.abc import Generator

import pandas as pd
import pytest
from fastapi.testclient import TestClient

# ``SESSION_SECRET`` is seeded in ``tests/conftest.py`` so the ``heardle.api``
# import below succeeds without a populated ``.env``.
from heardle import api as api_mod
from heardle.auth import TokenBundle
from heardle.config import Settings
from heardle.corpus import Corpus
from heardle.game import initial_state


@pytest.fixture
def fake_settings() -> Settings:
    """A populated Settings suitable for all routes except the OAuth callback."""
    return Settings(
        spotify_client_id="fake_client_id",  # pragma: allowlist secret
        spotify_client_secret="fake_client_secret",  # pragma: allowlist secret
        spotify_redirect_uri="http://127.0.0.1:8000/callback",
        session_secret="fake_session",  # pragma: allowlist secret
        game_state_secret="fake_game_state",  # pragma: allowlist secret
        popular_corpus_path="data/popular_corpus.parquet",
        popularity_threshold=25,
        year_threshold=2000,
    )


@pytest.fixture
def fake_corpus() -> Corpus:
    """A tiny corpus that the autocomplete route can search against."""
    df = pd.DataFrame(
        [
            ("id_1", "Halo", "Beyoncé", "Sasha Fierce", 2008, 80),
            ("id_2", "Shape of You", "Ed Sheeran", "÷", 2017, 90),
            ("id_3", "Blinding Lights", "The Weeknd", "After Hours", 2020, 85),
        ],
        columns=[
            "spotify_id",
            "title",
            "primary_artist",
            "album_name",
            "release_year",
            "popularity",
        ],
    ).astype({"release_year": "int32", "popularity": "Int32"})
    return Corpus(df)


@pytest.fixture
def fake_token() -> TokenBundle:
    return TokenBundle(
        access_token="fake_access",  # pragma: allowlist secret
        refresh_token="fake_refresh",  # pragma: allowlist secret
        expires_at_epoch=9999999999.0,
        scope="streaming user-read-email",
    )


@pytest.fixture
def client(
    fake_settings: Settings,
    fake_corpus: Corpus,
    fake_token: TokenBundle,
) -> Generator[TestClient, None, None]:
    """Yield a TestClient with deps overridden and module dicts cleaned."""
    api_mod._games.clear()
    api_mod._autocomplete_pools.clear()
    api_mod._correct_pool_meta.clear()
    api_mod._target_track_meta.clear()
    api_mod._corpus_cache[0] = fake_corpus

    api_mod.app.dependency_overrides[api_mod.get_settings] = lambda: fake_settings
    api_mod.app.dependency_overrides[api_mod.get_corpus] = lambda: fake_corpus
    api_mod.app.dependency_overrides[api_mod.require_session_token] = lambda: fake_token

    test_client = TestClient(api_mod.app)
    yield test_client
    api_mod.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Index + autocomplete
# ---------------------------------------------------------------------------


def test_index_renders(client: TestClient) -> None:
    """Rationale: the landing page must respond 200 with the site heading.

    If the index fails the entire app is dead on arrival, so a smoke-level
    assertion here catches gross template or dependency wiring errors.
    """
    response = client.get("/")
    assert response.status_code == 200
    assert "Heardle" in response.text
    assert "Pick a source" in response.text


def test_autocomplete_returns_json(client: TestClient) -> None:
    """Rationale: the autocomplete route is invoked on every keystroke in
    the guess box; a silent regression here kills the whole input UX.
    """
    response = client.get("/autocomplete", params={"q": "halo"})
    assert response.status_code == 200
    results = response.json()
    assert any(r["id"] == "id_1" for r in results)
    assert results[0]["title"] == "Halo"


def test_autocomplete_empty_query_returns_empty_list(client: TestClient) -> None:
    """Rationale: empty queries must not trigger a full-corpus scan."""
    response = client.get("/autocomplete", params={"q": ""})
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Auth routes (non-live)
# ---------------------------------------------------------------------------


def test_login_redirects_to_spotify(client: TestClient) -> None:
    """Rationale: ``/auth/login`` must 302 to accounts.spotify.com with the
    expected query parameters. Missing scopes or a malformed state break the
    entire login flow at the browser level, so it's important to cover here."""
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 307 or response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.spotify.com/authorize")
    assert "client_id=fake_client_id" in location
    assert "response_type=code" in location
    assert "scope=streaming" in location


def test_login_returns_503_without_client_id(client: TestClient, fake_settings: Settings) -> None:
    """Rationale: if the user has not registered a Spotify app yet, the login
    route should surface a clean 503, not a cryptic redirect to
    accounts.spotify.com with an empty client_id."""
    blank_settings = Settings(
        spotify_client_id="",
        spotify_client_secret=fake_settings.spotify_client_secret,
        spotify_redirect_uri=fake_settings.spotify_redirect_uri,
        session_secret=fake_settings.session_secret,
        game_state_secret=fake_settings.game_state_secret,
        popular_corpus_path=fake_settings.popular_corpus_path,
        popularity_threshold=fake_settings.popularity_threshold,
        year_threshold=fake_settings.year_threshold,
    )
    api_mod.app.dependency_overrides[api_mod.get_settings] = lambda: blank_settings
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 503


def test_api_token_returns_access_token(client: TestClient, fake_token: TokenBundle) -> None:
    """Rationale: the Web Playback SDK polls this endpoint to get a token;
    the shape must remain {"access_token": "..."} for the SDK's callback."""
    response = client.get("/api/token")
    assert response.status_code == 200
    assert response.json() == {"access_token": fake_token.access_token}


def test_logout_clears_session(client: TestClient) -> None:
    """Rationale: logout must 303-redirect to "/"."""
    response = client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Game lifecycle — set up via direct dict mutation
# ---------------------------------------------------------------------------


def _seed_game(corpus: Corpus, target_id: str = "id_1") -> str:
    """Prime the module-level dicts with a game; return the game_id."""
    game_id = "test_game_0"
    api_mod._games[game_id] = initial_state(target_id)
    api_mod._autocomplete_pools[game_id] = corpus
    api_mod._target_track_meta[game_id] = corpus.get_track(target_id)
    return game_id


def test_game_page_renders_for_existing_game(client: TestClient, fake_corpus: Corpus) -> None:
    """Rationale: smoke-test for the full game template.

    A 500 here often means a missing context variable in the template — the
    sort of regression ty / ruff cannot catch since Jinja contexts are dynamic.
    """
    game_id = _seed_game(fake_corpus)
    response = client.get(f"/game/{game_id}")
    assert response.status_code == 200
    assert 'data-game-id="' in response.text
    assert "Play" in response.text


def test_game_page_404_for_unknown_id(client: TestClient) -> None:
    """Rationale: opaque id lookup must 404 rather than leaking a 500."""
    response = client.get("/game/does_not_exist")
    assert response.status_code == 404


def test_guess_correct_transitions_to_finished(client: TestClient, fake_corpus: Corpus) -> None:
    """Rationale: a correct guess at round 0 should finish the game and the
    returned partial should render the winning result section."""
    game_id = _seed_game(fake_corpus, target_id="id_1")
    response = client.post(f"/game/{game_id}/guess", data={"guess_spotify_id": "id_1"})
    assert response.status_code == 200
    assert "You got it" in response.text
    assert api_mod._games[game_id].finished is True
    assert api_mod._games[game_id].won is True


def test_guess_wrong_advances_round(client: TestClient, fake_corpus: Corpus) -> None:
    """Rationale: a wrong guess increments round_index by exactly one."""
    game_id = _seed_game(fake_corpus, target_id="id_1")
    response = client.post(f"/game/{game_id}/guess", data={"guess_spotify_id": "id_2"})
    assert response.status_code == 200
    assert api_mod._games[game_id].round_index == 1
    assert api_mod._games[game_id].finished is False


def test_skip_advances_round(client: TestClient, fake_corpus: Corpus) -> None:
    """Rationale: skip transitions the state identically to a wrong guess,
    with a ``None`` entry in ``guesses``."""
    game_id = _seed_game(fake_corpus, target_id="id_1")
    response = client.post(f"/game/{game_id}/skip")
    assert response.status_code == 200
    state = api_mod._games[game_id]
    assert state.round_index == 1
    assert state.guesses == (None,)


def test_guess_on_finished_game_returns_409(client: TestClient, fake_corpus: Corpus) -> None:
    """Rationale: game engine raises ``ValueError`` which routes map to 409."""
    game_id = _seed_game(fake_corpus, target_id="id_1")
    # Finish the game first.
    client.post(f"/game/{game_id}/guess", data={"guess_spotify_id": "id_1"})
    # Second guess must reject.
    response = client.post(f"/game/{game_id}/guess", data={"guess_spotify_id": "id_2"})
    assert response.status_code == 409


def test_autocomplete_with_game_id_uses_union_pool(client: TestClient, fake_corpus: Corpus) -> None:
    """Rationale: during a game the autocomplete pool must include the
    correct-answer pool even if it's obscure. We seed a game with a pool that
    contains a track absent from the base corpus and verify it appears."""
    from heardle.spotify import Track

    obscure = Track(
        spotify_id="obscure_id",
        title="Obscure Tune Only In Playlist",
        primary_artist="Unknown",
        album_name="Demo",
        release_year=2023,
        popularity=None,
    )
    game_id = _seed_game(fake_corpus, target_id="id_1")
    api_mod._autocomplete_pools[game_id] = fake_corpus.union_with([obscure])

    response = client.get("/autocomplete", params={"q": "obscure", "game_id": game_id})
    assert response.status_code == 200
    ids = [r["id"] for r in response.json()]
    assert "obscure_id" in ids
