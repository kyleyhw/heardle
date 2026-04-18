"""FastAPI routes — index, OAuth, game lifecycle, autocomplete, token proxy.

Architecture
------------
- Session state is a signed cookie managed by Starlette's ``SessionMiddleware``.
  It holds the user's Spotify tokens and the id of their active game.
- Game state lives in a module-level dict keyed by a random game id. This is
  single-process and ephemeral, which is fine for a single-player local app.
  Restarting the server drops in-progress games.
- Settings and corpus are resolved via FastAPI dependencies (``get_settings``,
  ``get_corpus``). Tests override these dependencies to inject test doubles
  without touching the environment.
- The :func:`lifespan` context tries to load the corpus at startup. If the
  parquet is missing the app still starts, with routes that need it returning
  503 — the user can run the loader script while the server is up in
  developer mode.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired
from starlette.middleware.sessions import SessionMiddleware

from heardle import auth as auth_mod
from heardle import spotify as spotify_mod
from heardle.config import Settings, load_settings
from heardle.corpus import Corpus, load_corpus
from heardle.game import GameState, apply_guess, clip_length_for, initial_state
from heardle.game import score as game_score
from heardle.game import skip_round as game_skip

logger = logging.getLogger("heardle")

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

# In-memory game store. Single-process, single-user is the design target.
_games: dict[str, GameState] = {}

# Corpus is resolved once by ``get_corpus`` via ``_corpus_cache`` below; kept
# in a module-level slot so the lifespan can populate it before the first
# request arrives.
_corpus_cache: list[Corpus | None] = [None]

# Session-cookie key names. Declared as constants so routes cannot diverge.
_SESSION_TOKEN = "spotify_token"
_SESSION_USER = "spotify_user"
_SESSION_GAME_ID = "active_game_id"
_SESSION_OAUTH_STATE = "oauth_state"


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@lru_cache
def get_settings() -> Settings:
    """Return the loaded :class:`Settings`. Cached — tests override this dependency."""
    return load_settings()


def get_corpus() -> Corpus:
    """Return the loaded corpus or raise 503 if it has not been built yet."""
    corpus = _corpus_cache[0]
    if corpus is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Popular-songs corpus is not loaded. Run "
                "`uv run python -m scripts.load_corpus --input <csv>` first."
            ),
        )
    return corpus


async def require_session_token(request: Request) -> auth_mod.TokenBundle:
    """Return the session's Spotify token or 401.

    Does not refresh the token here — the refresh loop lives in
    :func:`_spotify_call` so it can be applied uniformly to any Spotify API
    call and keep the session cookie in sync.
    """
    raw = request.session.get(_SESSION_TOKEN)
    if not raw:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return auth_mod.TokenBundle(**raw)


# ---------------------------------------------------------------------------
# Lifespan — corpus bootstrap
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Populate module-level caches at server start; clear them on shutdown."""
    try:
        settings = get_settings()
    except KeyError as e:
        logger.warning(
            "Missing required environment variable %s. Copy .env.example → .env.",
            e,
        )
        yield
        return

    corpus_path = Path(settings.popular_corpus_path)
    if not corpus_path.exists():
        logger.warning(
            "Popular-songs corpus not found at %s. Autocomplete routes will "
            "return 503 until `uv run python -m scripts.load_corpus` has run.",
            corpus_path,
        )
    else:
        _corpus_cache[0] = load_corpus(
            str(corpus_path),
            popularity_threshold=settings.popularity_threshold,
            year_threshold=settings.year_threshold,
        )
        logger.info("Loaded popular-songs corpus (%d tracks).", len(_corpus_cache[0]))

    yield

    _games.clear()
    _corpus_cache[0] = None


# ---------------------------------------------------------------------------
# Application construction
# ---------------------------------------------------------------------------

app = FastAPI(title="Heardle", version="0.1.0", lifespan=lifespan)

# The SessionMiddleware secret must be resolvable at app-construction time,
# before the lifespan runs. Fall back to a random ephemeral secret if the env
# var is missing — this lets the server come up in dev mode without a
# populated .env, at the cost of invalidating sessions on restart.
_session_secret_fallback = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret_fallback,
    max_age=60 * 60 * 24 * 7,  # 1 week — matches typical Spotify refresh lifecycle
    same_site="lax",
)

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Index and auth
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    """Render the source-selector form. Shows login state in the header."""
    user = request.session.get(_SESSION_USER)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "has_credentials": bool(settings.spotify_client_id),
        },
    )


@app.get("/auth/login")
async def auth_login(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    """Kick off the Spotify Authorization Code flow."""
    if not settings.spotify_client_id:
        raise HTTPException(
            status_code=503,
            detail="SPOTIFY_CLIENT_ID is not configured. Set it in .env.",
        )
    state = auth_mod.sign_state(settings.game_state_secret)
    request.session[_SESSION_OAUTH_STATE] = state
    url = auth_mod.build_authorize_url(
        client_id=settings.spotify_client_id,
        redirect_uri=settings.spotify_redirect_uri,
        state=state,
    )
    return RedirectResponse(url=url)


@app.get("/callback")
async def auth_callback(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Spotify's redirect: verify state, exchange code, check Premium."""
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify returned error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state.")

    expected_state = request.session.get(_SESSION_OAUTH_STATE)
    if state != expected_state:
        raise HTTPException(status_code=400, detail="OAuth state mismatch.")
    try:
        auth_mod.verify_state(settings.game_state_secret, state)
    except (BadSignature, SignatureExpired) as e:
        raise HTTPException(status_code=400, detail=f"Invalid state: {e}") from e

    bundle = await auth_mod.exchange_code_for_tokens(
        code=code,
        redirect_uri=settings.spotify_redirect_uri,
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
    )
    try:
        user_info = await auth_mod.assert_premium(bundle.access_token)
    except auth_mod.PremiumRequiredError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    # Serialise both dataclasses to plain dicts for session storage.
    request.session[_SESSION_TOKEN] = {
        "access_token": bundle.access_token,
        "refresh_token": bundle.refresh_token,
        "expires_at_epoch": bundle.expires_at_epoch,
        "scope": bundle.scope,
    }
    request.session[_SESSION_USER] = {
        "user_id": user_info.user_id,
        "display_name": user_info.display_name,
        "email": user_info.email,
        "product": user_info.product,
        "country": user_info.country,
    }
    request.session.pop(_SESSION_OAUTH_STATE, None)
    return RedirectResponse(url="/", status_code=303)


@app.get("/auth/logout")
async def auth_logout(request: Request) -> RedirectResponse:
    """Clear the session. Does not revoke the Spotify token (Spotify has no revoke endpoint)."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/token")
async def api_token(
    token: Annotated[auth_mod.TokenBundle, Depends(require_session_token)],
) -> JSONResponse:
    """Expose the current user's access token to the Web Playback SDK.

    Same-origin only (the session cookie requirement enforces this). The
    browser uses this endpoint as the ``getOAuthToken`` callback for the SDK.
    """
    return JSONResponse({"access_token": token.access_token})


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------


@app.post("/game/new", response_class=HTMLResponse)
async def game_new(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    corpus: Annotated[Corpus, Depends(get_corpus)],
    token: Annotated[auth_mod.TokenBundle, Depends(require_session_token)],
    source_type: Annotated[str, Form()],
    source_id: Annotated[str, Form()],
) -> RedirectResponse:
    """Build $\\mathcal{C}$ from the requested source, pick a target, redirect to /game/{id}."""
    user_country = (request.session.get(_SESSION_USER) or {}).get("country", "US")
    correct_pool = await _build_correct_pool(
        source_type=source_type,
        source_id=source_id,
        access_token=token.access_token,
        market=user_country,
    )
    if not correct_pool:
        raise HTTPException(
            status_code=400,
            detail="The selected source resolved to zero playable tracks.",
        )

    # numpy import is local to this call so the module does not depend on it
    # at import time; keeps the lifespan path leaner for CI.
    import numpy as np

    rng = np.random.default_rng()
    target = correct_pool[int(rng.integers(0, len(correct_pool)))]

    game_id = secrets.token_urlsafe(16)
    state = initial_state(target.spotify_id)
    _games[game_id] = state

    # Cache the extended autocomplete pool on the state dict so the
    # /autocomplete route can reuse it without rebuilding the union per query.
    _autocomplete_pools[game_id] = corpus.union_with(correct_pool)
    _correct_pool_meta[game_id] = {t.spotify_id: t for t in correct_pool}
    _target_track_meta[game_id] = target

    request.session[_SESSION_GAME_ID] = game_id
    return RedirectResponse(url=f"/game/{game_id}", status_code=303)


_autocomplete_pools: dict[str, Corpus] = {}
_correct_pool_meta: dict[str, dict[str, spotify_mod.Track]] = {}
_target_track_meta: dict[str, spotify_mod.Track] = {}


@app.get("/game/{game_id}", response_class=HTMLResponse)
async def game_page(
    request: Request,
    game_id: str,
    _token: Annotated[auth_mod.TokenBundle, Depends(require_session_token)],
) -> HTMLResponse:
    """Render the full game page for the given id."""
    state = _games.get(game_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    target = _target_track_meta.get(game_id)
    return templates.TemplateResponse(
        request,
        "game.html",
        {
            "game_id": game_id,
            "state": state,
            "clip_length_seconds": clip_length_for(state.round_index) if not state.finished else 0,
            "target": target if state.finished else None,
            "score": game_score(state) if state.finished else None,
            "guess_lookup": _guess_display_for(game_id, state),
        },
    )


@app.post("/game/{game_id}/guess", response_class=HTMLResponse)
async def game_guess(
    request: Request,
    game_id: str,
    guess_spotify_id: Annotated[str, Form()],
    _token: Annotated[auth_mod.TokenBundle, Depends(require_session_token)],
) -> HTMLResponse:
    """Apply a guess and return the updated game body partial."""
    state = _games.get(game_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    try:
        new_state = apply_guess(state, guess_spotify_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    _games[game_id] = new_state
    return _render_game_body(request, game_id, new_state)


@app.post("/game/{game_id}/play")
async def game_play(
    game_id: str,
    device_id: Annotated[str, Form()],
    token: Annotated[auth_mod.TokenBundle, Depends(require_session_token)],
) -> JSONResponse:
    """Start playback of the game's target on the given device, from position 0.

    The target track uri is never sent to the client; only the server-held
    :class:`GameState` knows it. The client supplies only the Spotify Web
    Playback SDK device id of the browser session.
    """
    state = _games.get(game_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    if state.finished:
        raise HTTPException(status_code=409, detail="Game is finished.")
    target_uri = f"spotify:track:{state.target_spotify_id}"
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"https://api.spotify.com/v1/me/player/play?device_id={device_id}",
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "Content-Type": "application/json",
            },
            json={"uris": [target_uri], "position_ms": 0},
            timeout=10.0,
        )
    if response.status_code not in (200, 202, 204):
        raise HTTPException(
            status_code=502,
            detail=f"Spotify play failed ({response.status_code}): {response.text}",
        )
    return JSONResponse({"status": "started"})


@app.post("/game/{game_id}/skip", response_class=HTMLResponse)
async def game_skip_route(
    request: Request,
    game_id: str,
    _token: Annotated[auth_mod.TokenBundle, Depends(require_session_token)],
) -> HTMLResponse:
    """Skip the current round and return the updated game body partial."""
    state = _games.get(game_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown game id.")
    try:
        new_state = game_skip(state)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    _games[game_id] = new_state
    return _render_game_body(request, game_id, new_state)


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------


@app.get("/autocomplete")
async def autocomplete(
    request: Request,
    q: str = "",
    game_id: str | None = None,
) -> JSONResponse:
    """Return up to 10 track matches as JSON: ``[{id, title, artist, year}, ...]``.

    When ``game_id`` is provided and refers to an active game, the search pool
    is $\\mathcal{G} = \\mathcal{P} \\cup \\mathcal{C}_\\text{game}$. Otherwise
    the pool is just $\\mathcal{P}$.
    """
    if game_id and game_id in _autocomplete_pools:
        pool = _autocomplete_pools[game_id]
    else:
        pool_candidate = _corpus_cache[0]
        if pool_candidate is None:
            return JSONResponse([])
        pool = pool_candidate
    tracks = pool.autocomplete(q, limit=10)
    return JSONResponse(
        [
            {
                "id": t.spotify_id,
                "title": t.title,
                "artist": t.primary_artist,
                "year": t.release_year,
            }
            for t in tracks
        ]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _build_correct_pool(
    *,
    source_type: str,
    source_id: str,
    access_token: str,
    market: str,
) -> list[spotify_mod.Track]:
    """Resolve a source-selector submission into $\\mathcal{C}$."""
    async with httpx.AsyncClient() as client:
        if source_type == "playlist":
            return await spotify_mod.fetch_playlist_tracks(
                source_id, access_token, http_client=client
            )
        if source_type == "artist":
            return await spotify_mod.fetch_artist_top_tracks(
                source_id, access_token, market=market, http_client=client
            )
        if source_type == "year":
            year = int(source_id)
            return await spotify_mod.search_tracks_by_year(
                year, access_token, market=market, http_client=client, cap=500
            )
    raise HTTPException(status_code=400, detail=f"Unknown source_type: {source_type}")


def _render_game_body(request: Request, game_id: str, state: GameState) -> HTMLResponse:
    """Render the partial that htmx swaps in after guess / skip."""
    target = _target_track_meta.get(game_id)
    return templates.TemplateResponse(
        request,
        "partials/game_body.html",
        {
            "game_id": game_id,
            "state": state,
            "clip_length_seconds": clip_length_for(state.round_index) if not state.finished else 0,
            "target": target if state.finished else None,
            "score": game_score(state) if state.finished else None,
            "guess_lookup": _guess_display_for(game_id, state),
        },
    )


def _guess_display_for(game_id: str, state: GameState) -> dict[str, Any]:
    """Return a ``{spotify_id: {title, artist}}`` lookup for the current guesses.

    Templates use this to render human-readable rows from the bare ids stored
    in :class:`GameState`. A skipped round (``None``) is rendered by the
    template itself; we only populate entries for real guesses.
    """
    pool = _autocomplete_pools.get(game_id)
    out: dict[str, Any] = {}
    if pool is None:
        return out
    for guess in state.guesses:
        if guess is None or guess in out:
            continue
        if guess in pool:
            t = pool.get_track(guess)
            out[guess] = {"title": t.title, "artist": t.primary_artist}
    return out
