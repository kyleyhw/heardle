"""Pure game logic — no IO, no Spotify calls, no framework.

The separation matters: the entire $d_i$ schedule, the state-transition
function, and the scoring function are expressed here as plain immutable
Python so that unit tests can verify them against the mathematical
specification in ``README.md`` without mocking anything.

Conventions
-----------
- :class:`GameState` is a frozen dataclass. State transitions return a *new*
  :class:`GameState`; the input is never mutated.
- Guesses are keyed on Spotify track ids (not titles / fuzzy strings). The
  autocomplete UI is responsible for resolving whatever the user types into a
  track id; fuzzy matching lives in :mod:`heardle.corpus`, not here.
- A skipped round is represented by a ``None`` entry in ``guesses``; a real
  guess is the Spotify id string the player picked from autocomplete.

Mathematical reference
----------------------
Clip length $d_i$ in seconds for round $i \\in \\{0, 1, \\dots, 5\\}$:

    d = (1, 2, 4, 7, 11, 16)

Score on a correct guess at round $i$: $s = 6 - i$. Exhausting all rounds
without a correct guess yields $s = 0$.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Clip lengths in seconds, indexed by round i ∈ {0, …, 5}. See README for
# derivation. The sequence is convex (second differences 1, 1, 1, 1, 2) so
# each additional round gives the player meaningfully more information.
CLIP_LENGTHS_SECONDS: tuple[int, ...] = (1, 2, 4, 7, 11, 16)
MAX_ROUND: int = len(CLIP_LENGTHS_SECONDS) - 1  # = 5

# Cap on how many total rounds a game can last. Equal to ``len(d_i)``.
TOTAL_ROUNDS: int = len(CLIP_LENGTHS_SECONDS)


@dataclass(frozen=True)
class GameState:
    """Immutable single-player game state.

    Attributes
    ----------
    target_spotify_id
        The track the player is trying to identify. Never revealed to the
        browser until the game ends (the server holds it).
    round_index
        The round the player is *about to act on*. For a fresh game this is 0.
        Advances only on wrong / skipped guesses; stays put on a correct guess
        (since the game ends there) and on exhaustion.
    guesses
        Tuple of player submissions in order. Each entry is either a Spotify
        track id (a real guess) or ``None`` (a skipped round). Invariant:
        ``len(guesses) == rounds_played``.
    finished
        True once the game has ended (win or exhaustion).
    won
        True iff the final submission matched ``target_spotify_id``. When
        ``finished`` is False this is always False.
    """

    target_spotify_id: str
    round_index: int
    guesses: tuple[str | None, ...]
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
    """Return $d_i$ in seconds for the given round index.

    Raises
    ------
    IndexError
        If ``round_index`` is outside :math:`[0, \\mathrm{MAX\\_ROUND}]`.
    """
    if round_index < 0 or round_index > MAX_ROUND:
        raise IndexError(f"round_index {round_index} out of range [0, {MAX_ROUND}]")
    return CLIP_LENGTHS_SECONDS[round_index]


def score(state: GameState) -> int:
    """Return the final score for a finished game.

    On a win, returns $6 - i$ where $i$ is the round the player won at. On
    exhaustion, returns 0.

    Raises
    ------
    ValueError
        If ``state.finished`` is False — score is undefined mid-game.
    """
    if not state.finished:
        raise ValueError("Score is only defined for a finished game.")
    if not state.won:
        return 0
    # ``round_index`` freezes at the round the correct guess was made (the
    # win short-circuits progression), so this expression remains valid.
    return TOTAL_ROUNDS - state.round_index


def apply_guess(state: GameState, guess_spotify_id: str) -> GameState:
    """Apply a real (non-skip) guess and return the resulting state.

    Raises
    ------
    ValueError
        If the game has already finished.
    """
    if state.finished:
        raise ValueError("Cannot apply a guess to a finished game.")
    new_guesses = (*state.guesses, guess_spotify_id)
    is_correct = guess_spotify_id == state.target_spotify_id
    return _advance(state, new_guesses, is_correct=is_correct)


def skip_round(state: GameState) -> GameState:
    """Advance the game by one round without submitting a guess.

    Uses ``None`` as the guess-log entry so the UI can render "skipped" rows
    distinctly from wrong-track rows. Structurally equivalent to a wrong guess.
    """
    if state.finished:
        raise ValueError("Cannot skip a round on a finished game.")
    new_guesses = (*state.guesses, None)
    return _advance(state, new_guesses, is_correct=False)


def _advance(
    state: GameState,
    new_guesses: tuple[str | None, ...],
    *,
    is_correct: bool,
) -> GameState:
    """Shared transition logic for guesses and skips."""
    if is_correct:
        # Correct answer ends the game immediately; ``round_index`` is frozen
        # at the round the player won at so :func:`score` can reconstruct it.
        return replace(state, guesses=new_guesses, finished=True, won=True)
    # Wrong or skipped at the final round → exhausted.
    if state.round_index >= MAX_ROUND:
        return replace(state, guesses=new_guesses, finished=True, won=False)
    # Otherwise advance to the next round.
    return replace(state, guesses=new_guesses, round_index=state.round_index + 1)
