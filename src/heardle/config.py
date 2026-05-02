"""Environment-driven configuration.

Values are sourced from the process environment, populated at module import
time from the repository-root ``.env`` by ``python-dotenv``. All values have
sensible defaults so a bare clone with no ``.env`` boots cleanly in the
default iTunes-backed mode — the "clone and play" experience.

For production use (real sessions, Spotify-backed mode) the user overrides
specific values in ``.env``. See ``.env.example`` for the full catalogue.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from dotenv import load_dotenv

# Side effect intentional: load .env exactly once, on first import of any
# heardle module that touches config. ``load_dotenv`` does not override
# variables already set in the environment, so CI / Docker / systemd-
# provided env wins — the behaviour we want for deployment.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Typed snapshot of the process environment at load time."""

    audio_backend: str
    """One of:

    - ``"deezer"`` — default. Zero-auth, larger global catalogue, generous
      rate limit. Preview window is selected by Deezer (often the chorus).
    - ``"itunes"`` — zero-auth fallback. Smaller catalogue, US-biased.
    - ``"spotify"`` — reserved for a future re-enablement; currently raises 503.
    """

    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str

    session_secret: str
    game_state_secret: str

    popular_corpus_path: str
    popularity_threshold: int | None
    year_threshold: int | None

    itunes_country: str
    """Two-letter ISO country used for iTunes storefront queries."""


def load_settings() -> Settings:
    """Load configuration from the environment with sensible defaults throughout.

    No variable is strictly required — the only one with any security
    implication (``SESSION_SECRET``) falls back to an ephemeral random token
    so the server can boot for local-only use. Override in production.
    """
    return Settings(
        audio_backend=os.environ.get("AUDIO_BACKEND", "deezer").lower(),
        spotify_client_id=os.environ.get("SPOTIFY_CLIENT_ID", ""),
        spotify_client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
        spotify_redirect_uri=os.environ.get(
            "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback"
        ),
        session_secret=os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32),
        game_state_secret=os.environ.get("GAME_STATE_SECRET") or secrets.token_urlsafe(32),
        popular_corpus_path=os.environ.get("POPULAR_CORPUS_PATH", "data/popular_corpus.parquet"),
        popularity_threshold=_int_or_none(os.environ.get("POPULARITY_THRESHOLD", "25")),
        year_threshold=_int_or_none(os.environ.get("YEAR_THRESHOLD", "2000")),
        itunes_country=os.environ.get("ITUNES_COUNTRY", "US"),
    )


def _int_or_none(raw: str) -> int | None:
    """Parse an env-var int, treating the empty string as "filter disabled"."""
    stripped = raw.strip()
    if not stripped:
        return None
    return int(stripped)
