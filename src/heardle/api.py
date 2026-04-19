"""FastAPI routes — iTunes-backed by default, with a toggle to Spotify (future).

Architecture
------------
- Session state is a signed cookie managed by Starlette's ``SessionMiddleware``;
  in iTunes mode it holds only ``active_game_id``.
- A *session* spans multiple songs drawn without replacement from the chosen
  source pool. Each *song* is one Heardle puzzle with the canonical
  1-2-4-7-11-16 s clip-reveal schedule. Session ends when the user clicks
  End, or when the remaining pool is exhausted after the final song.
- Game state lives in a module-level dict keyed by a random game id, along
  with the current target Track (including ``preview_url``, server-only),
  the remaining pool, and the history of completed songs.
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
import secrets
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
# Session / result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SongResult:
    """Outcome of one completed song, recorded into the session history."""

    target: Track
    guesses: tuple[str | None, ...]
    won: bool
    score: int


@dataclass
class GameSession:
    """Server-side state for one multi-song session.

    Attributes
    ----------
    state
        Pure game state for the *current* song (round index, guesses,
        finished/won). Replaced on each advance to a new song.
    target
        The full :class:`Track` for the current song — includes the
        ``preview_url`` which is released to the browser only via the
        ``/game/{id}/preview`` route.
    correct_pool
        Every track that could have been a target in this session, keyed
        by id. Used for autocomplete-pool union and to render wrong-guess
        rows with readable title + artist labels.
    remaining_pool
        Tracks not yet played this session. A song is popped off this
        list on each advance (without replacement).
    history
        List of :class:`SongResult` entries, one per completed song in
        the order they were played.
    source_type, source_value
        The source selection the user made (``artist`` / ``year`` /
        ``search`` + value). Kept on the session so the scoreboard can
        render the context without re-asking.
    session_finished
        Set True when the user explicitly ends the session or when a
        Next is attempted with an empty remaining_pool.
    """

    state: GameState
    target: Track
    correct_pool: dict[str, Track]
    remaining_pool: list[Track]
    history: list[SongResult]
    source_type: str
    source_value: str
    session_finished: bool = field(default=False)


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


app = FastAPI(title="Heardle", version="0.3.0", lifespan=lifespan)

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
    """Start a new multi-song session and redirect to the first song's page."""
    if settings.audio_backend != "itunes":
        raise HTTPException(
            status_code=503,
            detail=(
                f"audio_backend={settings.audio_backend!r} is not currently "
                "wired up. Set AUDIO_BACKEND=itunes or unset the env var."
            ),
        )

    pool = await _build_correct_pool_itunes(
        source_type=source_type,
        source_value=source_value,
        country=settings.itunes_country,
    )
    if not pool:
        raise HTTPException(
            status_code=400,
            detail=(
                "That source resolved to zero playable tracks. Try a different "
                "artist name or year."
            ),
        )

    # Shuffle first so the play order is not iTunes' ranked order. Then pop the
    # first song as the initial target; the rest become the remaining pool.
    random.shuffle(pool)
    first_target = pool.pop()

    game_id = _new_game_id()
    _games[game_id] = GameSession(
        state=initial_state(first_target.spotify_id),
        target=first_target,
        correct_pool={t.spotify_id: t for t in [first_target, *pool]},
        remaining_pool=pool,
        history=[],
        source_type=source_type,
        source_value=source_value,
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
    """Apply a guess to the current song and return the updated body partial."""
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    if session.session_finished:
        raise HTTPException(status_code=409, detail="Session has ended.")
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
    """Skip the current round and return the updated body partial."""
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    if session.session_finished:
        raise HTTPException(status_code=409, detail="Session has ended.")
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


@app.post("/game/{game_id}/next", response_class=HTMLResponse)
async def game_next(request: Request, game_id: str) -> HTMLResponse:
    """Advance to the next song in the session.

    Only valid when the current song is finished and the session itself is not.
    When the remaining pool is empty, this transitions the session into its
    finished state (scoreboard view).
    """
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    if session.session_finished:
        raise HTTPException(status_code=409, detail="Session has ended.")
    if not session.state.finished:
        raise HTTPException(status_code=409, detail="Current song is not finished.")
    _record_current_song_result(session)
    if not session.remaining_pool:
        session.session_finished = True
    else:
        _draw_next_song(session)
    return templates.TemplateResponse(
        request,
        "partials/game_body.html",
        _game_context(game_id, session),
    )


@app.post("/game/{game_id}/end", response_class=HTMLResponse)
async def game_end(request: Request, game_id: str) -> HTMLResponse:
    """End the session and render the scoreboard.

    If the current song is finished and not yet recorded, it is added to the
    history before finalising. An in-progress current song is abandoned — not
    counted toward the scoreboard.
    """
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    if not session.session_finished:
        if session.state.finished and not _current_song_already_recorded(session):
            _record_current_song_result(session)
        session.session_finished = True
    return templates.TemplateResponse(
        request,
        "partials/game_body.html",
        _game_context(game_id, session),
    )


@app.get("/game/{game_id}/preview")
async def game_preview(game_id: str) -> JSONResponse:
    """Return the current song's preview URL on demand.

    The URL is deliberately not rendered into the page HTML; releasing it
    only via this endpoint means a player has to take an explicit action
    before the preview becomes visible in the network tab.
    """
    session = _games.get(game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    if session.session_finished:
        raise HTTPException(status_code=409, detail="Session has ended.")
    preview_url = session.target.preview_url
    if not preview_url:
        raise HTTPException(status_code=503, detail="Target has no preview URL.")
    return JSONResponse({"preview_url": preview_url, "target_id": session.target.spotify_id})


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------


@app.get("/autocomplete")
async def autocomplete(
    settings: Annotated[Settings, Depends(get_settings)],
    q: str = "",
    game_id: str | None = None,
) -> JSONResponse:
    """Return up to 10 autocomplete matches for the user's partial guess.

    Proxies to iTunes search and unions the result with substring matches
    from the active session's correct_pool, so the current target is always
    findable by at least one substring of its title.
    """
    query = q.strip()
    if not query:
        return JSONResponse([])
    if settings.audio_backend != "itunes":
        return JSONResponse([])

    async with httpx.AsyncClient() as client:
        live = await itunes_mod.search_tracks(
            query, limit=10, country=settings.itunes_country, http_client=client
        )

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
    return secrets.token_urlsafe(12)


def _record_current_song_result(session: GameSession) -> None:
    """Append the current (finished) song's outcome to ``session.history``."""
    state = session.state
    session.history.append(
        SongResult(
            target=session.target,
            guesses=state.guesses,
            won=state.won,
            score=game_score(state) if state.finished else 0,
        )
    )


def _current_song_already_recorded(session: GameSession) -> bool:
    """True if the current target's id is the last entry in ``history``.

    We only need to check the tail because songs are recorded exactly once,
    immediately on finish or on /end, in the order they are played.
    """
    if not session.history:
        return False
    return session.history[-1].target.spotify_id == session.target.spotify_id


def _draw_next_song(session: GameSession) -> None:
    """Pop the next target from the remaining pool and reset the per-song state."""
    # ``pop()`` is O(1) from the end; the pool was pre-shuffled so this is
    # equivalent to a uniform random draw without replacement.
    next_track = session.remaining_pool.pop()
    session.state = initial_state(next_track.spotify_id)
    session.target = next_track


def _game_context(game_id: str, session: GameSession) -> dict[str, object]:
    """Build the Jinja context shared by the full-page and partial templates."""
    state = session.state
    guess_lookup: dict[str, dict[str, str]] = {}
    for guess in state.guesses:
        if guess is None or guess in guess_lookup:
            continue
        if guess in session.correct_pool:
            t = session.correct_pool[guess]
            guess_lookup[guess] = {"title": t.title, "artist": t.primary_artist}

    total_songs_in_session = len(session.correct_pool)
    songs_played = len(session.history)
    current_song_number = songs_played + 1 if not session.session_finished else songs_played
    total_score = sum(r.score for r in session.history)
    max_score = songs_played * 6  # 6 points max per song

    return {
        "game_id": game_id,
        "state": state,
        "clip_length_seconds": clip_length_for(state.round_index) if not state.finished else 0,
        "max_clip_seconds": 16,  # the full preview window our d_i progression tops out at
        "target": session.target if state.finished else None,
        "score": game_score(state) if state.finished else None,
        "guess_lookup": guess_lookup,
        # Session-level
        "session_finished": session.session_finished,
        "history": session.history,
        "current_song_number": current_song_number,
        "total_songs": total_songs_in_session,
        "remaining_count": len(session.remaining_pool),
        "total_score": total_score,
        "max_score": max_score,
        "source_type": session.source_type,
        "source_value": session.source_value,
    }
