"""Environment-driven configuration.

Values are sourced from the process environment, populated at module import time
from the repository-root ``.env`` by ``python-dotenv``. Missing variables raise
``KeyError`` at settings load, so configuration errors surface immediately at
server start rather than silently mid-request.

``load_settings`` is deliberately uncached — tests may patch ``os.environ`` and
expect to see the change. The FastAPI wiring caches a single instance at app
startup via a module-level global in :mod:`heardle.api`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Side effect intentional: load .env exactly once, on first import of any heardle
# module that touches config. ``load_dotenv`` does not override variables already
# set in the environment, so CI / Docker / systemd-provided env wins, which is
# the behaviour we want in deployment.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Typed snapshot of the process environment at load time.

    Immutable so downstream modules can rely on settings not drifting mid-run.
    """

    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    session_secret: str
    game_state_secret: str
    popular_corpus_path: str
    popularity_threshold: int


def load_settings() -> Settings:
    """Load configuration from the process environment.

    Raises
    ------
    KeyError
        If any required variable is missing. Required: ``SPOTIFY_CLIENT_ID``,
        ``SPOTIFY_CLIENT_SECRET``, ``SPOTIFY_REDIRECT_URI``, ``SESSION_SECRET``,
        ``GAME_STATE_SECRET``. Optional with defaults: ``POPULAR_CORPUS_PATH``
        (``data/popular_corpus.parquet``), ``POPULARITY_THRESHOLD`` (``25``).
    ValueError
        If ``POPULARITY_THRESHOLD`` is set but not an integer.
    """
    return Settings(
        spotify_client_id=os.environ["SPOTIFY_CLIENT_ID"],
        spotify_client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        spotify_redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
        session_secret=os.environ["SESSION_SECRET"],
        game_state_secret=os.environ["GAME_STATE_SECRET"],
        popular_corpus_path=os.environ.get("POPULAR_CORPUS_PATH", "data/popular_corpus.parquet"),
        popularity_threshold=int(os.environ.get("POPULARITY_THRESHOLD", "25")),
    )
