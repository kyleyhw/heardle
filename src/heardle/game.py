"""Pure game logic — no IO, no Spotify calls, no framework.

The separation matters: the entire $d_i$ schedule and scoring function are expressed
here as plain Python so that unit tests can verify them against the mathematical
specification in ``README.md`` without mocking anything. Phase 3 implementation target.
"""

from __future__ import annotations

from dataclasses import dataclass

# Clip lengths in seconds, indexed by round i ∈ {0, …, 5}. See README for derivation.
CLIP_LENGTHS_SECONDS: tuple[int, ...] = (1, 2, 4, 7, 11, 16)
MAX_ROUND: int = len(CLIP_LENGTHS_SECONDS) - 1  # = 5


@dataclass
class GameState:
    """Single-player game state. Immutable operations return a new ``GameState``."""

    target_spotify_id: str
    round_index: int
    guesses: tuple[str, ...]
    finished: bool
    won: bool


def initial_state(target_spotify_id: str) -> GameState:
    """Start a new game targeting the given Spotify track id."""
    return GameState(
        target_spotify_id=target_spotify_id,
        round_index=0,
        guesses=(),
        finished=False,
        won=False,
    )


def clip_length_for(round_index: int) -> int:
    """Return $d_i$ in seconds for the given round index."""
    raise NotImplementedError("Implemented in Phase 3.")


def score(state: GameState) -> int:
    """Return $6 - i$ if won at round $i$; 0 if exhausted; raises if mid-game."""
    raise NotImplementedError("Implemented in Phase 3.")


def apply_guess(state: GameState, guess_spotify_id: str) -> GameState:
    """Apply a guess. If correct → finished + won; else advance round or finish."""
    raise NotImplementedError("Implemented in Phase 3.")
