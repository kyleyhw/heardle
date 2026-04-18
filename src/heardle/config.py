"""Environment-driven configuration.

Values are sourced from the process environment (populated by python-dotenv at startup
from ``.env``). See ``.env.example`` for the full list of required variables and their
expected formats.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Typed snapshot of the process environment at import time.

    Kept immutable so that downstream modules can rely on settings not drifting mid-run.
    """

    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    session_secret: str
    game_state_secret: str
    popular_corpus_path: str
    popularity_threshold: int


def load_settings() -> Settings:
    """Load configuration from the environment. Raises ``KeyError`` for any missing key."""
    return Settings(
        spotify_client_id=os.environ["SPOTIFY_CLIENT_ID"],
        spotify_client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        spotify_redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
        session_secret=os.environ["SESSION_SECRET"],
        game_state_secret=os.environ["GAME_STATE_SECRET"],
        popular_corpus_path=os.environ.get("POPULAR_CORPUS_PATH", "data/popular_corpus.parquet"),
        popularity_threshold=int(os.environ.get("POPULARITY_THRESHOLD", "25")),
    )
