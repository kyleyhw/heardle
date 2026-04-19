"""FastAPI routes — iTunes-backed by default, with a toggle to Spotify (future).

Architecture
------------
- Session state is a signed cookie managed by Starlette's ``SessionMiddleware``;
  in iTunes mode it holds only ``active_game_id``.
- Game state lives in a module-level dict keyed by a random game id, along
  with the full target Track (including ``preview_url``) and the
  correct-answer pool for guess-row display. The target's ``preview_url`` is
  never sent to the browser in HTML; it is fetched via a separate endpoint
  only when the player clicks Play. (It is technically visible in the
  network tab afterwards, which is acceptable for a hobby single-player
  game.)
- Settings and the current audio backend are resolved via FastAPI
  dependencies so tests can override them without touching the environment.

Audio-backend toggle
--------------------
``AUDIO_BACKEND`` env var selects the backend. Default is ``itunes``.
``spotify`` is reserved for a future re-enablement — currently raises 503.
"""

from __future__ import annotations

import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from heardle import itunes as itunes_mod
from heardle.config import Settings, load_settings
from heardle.game import GameState, apply_guess, clip_length_for, initial_state
from heardle.game import score as game_score
from heardle.game import skip_round as game_skip
from heardle.spotify import Track

logger = logging.getLogger("heardle")


# ---------------------------------------------------------------------------
# Game session dataclass
# ---------------------------------------------------------------------------


@dataclass
class GameSession:
    """Everything the server knows about one in-flight game.

    Attributes
    ----------
    state
        Pure game state (round index, guesses, finished/won). Lives in
        :mod:`heardle.game`.
    target
        The full :class:`Track` the player is guessing. Server-only — the
        ``preview_url`` inside is surfaced to the browser only through the
        ``/game/{id}/play`` endpoint.
    correct_pool
        Dict keyed by ``track_id`` of every track that could have been the
        target for this game. Used to render human-readable guess rows for
        wrong guesses (when the id is in the pool) and for fallback
        autocomplete.
    """

    state: GameState
    target: Track
    correct_pool: dict[str, Track] = field(default_factory=dict)


_games: dict[str, GameSession] = {}

_SESSION_GAME_ID = "active_game_id"


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@lru_cache
def get_settings() -> Settings:
    """Return the loaded :class:`Settings`. Cached — tests override this dependency."""
    return load_settings()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log the chosen backend on startup; clear game state on shutdown."""
    settings = get_settings()
    logger.info("Heardle starting up: audio_backend=%s", settings.audio_backend)
    if settings.audio_backend == "spotify":
        logger.warning(
            "AUDIO_BACKEND=spotify is reserved for a future toggle. "
            "All game-creation routes will return 503 until this is wired up."
        )
    yield
    _games.clear()


# ---------------------------------------------------------------------------
# Application construction
# ---------------------------------------------------------------------------


app = FastAPI(title="Heardle", version="0.2.0", lifespan=lifespan)

# SessionMiddleware's secret must resolve at app-construction time, before
# lifespan runs. We call ``load_settings`` directly for that single value;
# everything else goes through the cached ``get_settings`` dependency.
_session_secret = load_settings().session_secret
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
)

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    """Render the source-selector form."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {"backend": settings.audio_backend},
    )


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------


@app.post("/game/new")
async def game_new(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    source_type: Annotated[str, Form()],
    source_value: Annotated[str, Form()],
) -> RedirectResponse:
    """Build 𝒞 from the requested source, pick a target uniformly, redirect to /game/{id}."""
    if settings.audio_backend != "itunes":
        raise HTTPException(
            status_code=503,
            detail=(
                f"audio_backend={settings.audio_backend!r} is not currently "
                "wired up. Set AUDIO_BACKEND=itunes or unset the env var."
            ),
        )

    correct_pool = await _build_correct_pool_itunes(
        source_type=source_type,
        source_value=source_value,
        country=settings.itunes_country,
    )
    if not correct_pool:
        raise HTTPException(
            status_code=400,
            detail=(
                "That source resolved to zero playable tracks. Try a different "
                "artist name or year."
            ),
        )

    # Use ``random`` rather than ``numpy`` here — we do not pay the numpy import
    # cost on the hot path when a simple uniform pick over a small list is enough.
    target = random.choice(correct_pool)

    game_id = _new_game_id()
    _games[game_id] = GameSession(
        state=initial_state(target.spotify_id),
        target=target,
        correct_pool={t.spotify_id: t for t in correct_pool},
    )
    request.session[_SESSION_GAME_ID] = game_id
    return RedirectResponse(url=f"/game/{game_id}", status_code=303)


@app.get("/game/{game_id}", response_class=HTMLResponse)
async def game_page(request: Request, game_id: str) -> HTMLResponse:
    """Render the full game page for the given id."""
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    return templates.TemplateResponse(
        request,
        "game.html",
        _game_context(game_id, session),
    )


@app.post("/game/{game_id}/guess", response_class=HTMLResponse)
async def game_guess(
    request: Request,
    game_id: str,
    guess_track_id: Annotated[str, Form()],
) -> HTMLResponse:
    """Apply a guess and return the updated game body partial."""
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    try:
        new_state = apply_guess(session.state, guess_track_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.state = new_state
    return templates.TemplateResponse(
        request,
        "partials/game_body.html",
        _game_context(game_id, session),
    )


@app.post("/game/{game_id}/skip", response_class=HTMLResponse)
async def game_skip_route(request: Request, game_id: str) -> HTMLResponse:
    """Skip the current round and return the updated game body partial."""
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    try:
        new_state = game_skip(session.state)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.state = new_state
    return templates.TemplateResponse(
        request,
        "partials/game_body.html",
        _game_context(game_id, session),
    )


@app.get("/game/{game_id}/preview")
async def game_preview(game_id: str) -> JSONResponse:
    """Return the target track's preview URL.

    The URL is only released after the client explicitly requests it — we do
    not embed it in the page HTML where a casual inspector could read it. It
    is still visible in the network tab after the first play, which is an
    acceptable leak for a hobby single-player game.
    """
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    if session.state.finished:
        # After the game ends the target is revealed in the result panel
        # anyway, so exposing the URL post-finish is harmless.
        pass
    preview_url = session.target.preview_url
    if not preview_url:
        raise HTTPException(status_code=503, detail="Target has no preview URL.")
    return JSONResponse({"preview_url": preview_url})


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------


@app.get("/autocomplete")
async def autocomplete(
    settings: Annotated[Settings, Depends(get_settings)],
    q: str = "",
    game_id: str | None = None,
) -> JSONResponse:
    """Return up to 10 track matches for the user's partial guess, as JSON.

    In iTunes mode we proxy to iTunes' own relevance-ranked search. When a
    game id is supplied we also sweep the game's correct_pool for matches
    and merge them in, so the target is always findable even if iTunes'
    global ranking buries it.
    """
    query = q.strip()
    if not query:
        return JSONResponse([])
    if settings.audio_backend != "itunes":
        return JSONResponse([])

    # Fetch live suggestions from iTunes. Caller is expected to debounce.
    async with httpx.AsyncClient() as client:
        live = await itunes_mod.search_tracks(
            query, limit=10, country=settings.itunes_country, http_client=client
        )

    # Supplement with fuzzy-ish substring matches from the correct pool, so
    # that even if iTunes doesn't rank the target in the top 10, it still
    # surfaces when the user types part of its title.
    pool_matches: list[Track] = []
    if game_id and game_id in _games:
        needle = query.lower()
        for t in _games[game_id].correct_pool.values():
            haystack = f"{t.title} {t.primary_artist}".lower()
            if needle in haystack:
                pool_matches.append(t)

    combined = _dedup_by_id(pool_matches + live)[:10]
    return JSONResponse(
        [
            {
                "id": t.spotify_id,
                "title": t.title,
                "artist": t.primary_artist,
                "year": t.release_year,
            }
            for t in combined
        ]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _build_correct_pool_itunes(
    *,
    source_type: str,
    source_value: str,
    country: str,
) -> list[Track]:
    """Resolve the source selection into a list of candidate targets (iTunes mode)."""
    async with httpx.AsyncClient() as client:
        if source_type == "artist":
            return await itunes_mod.search_tracks_by_artist(
                source_value, country=country, http_client=client
            )
        if source_type == "year":
            try:
                year = int(source_value)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Year must be an integer.") from e
            return await itunes_mod.search_tracks_by_year(year, country=country, http_client=client)
        if source_type == "search":
            return await itunes_mod.search_tracks(
                source_value, limit=50, country=country, http_client=client
            )
    raise HTTPException(status_code=400, detail=f"Unknown source_type: {source_type}")


def _dedup_by_id(tracks: list[Track]) -> list[Track]:
    """Preserve first occurrence of each track_id."""
    seen: set[str] = set()
    out: list[Track] = []
    for t in tracks:
        if t.spotify_id in seen:
            continue
        seen.add(t.spotify_id)
        out.append(t)
    return out


def _new_game_id() -> str:
    """Cryptographically-random, URL-safe game id."""
    import secrets as _secrets

    return _secrets.token_urlsafe(12)


def _game_context(game_id: str, session: GameSession) -> dict[str, object]:
    """Build the Jinja context shared by the full-page and partial templates."""
    state = session.state
    # Guess-display lookup only needs to contain ids that were actually
    # submitted; it produces {"title": ..., "artist": ...} rows in the UI.
    guess_lookup: dict[str, dict[str, str]] = {}
    for guess in state.guesses:
        if guess is None or guess in guess_lookup:
            continue
        if guess in session.correct_pool:
            t = session.correct_pool[guess]
            guess_lookup[guess] = {"title": t.title, "artist": t.primary_artist}
    return {
        "game_id": game_id,
        "state": state,
        "clip_length_seconds": clip_length_for(state.round_index) if not state.finished else 0,
        "target": session.target if state.finished else None,
        "score": game_score(state) if state.finished else None,
        "guess_lookup": guess_lookup,
    }
