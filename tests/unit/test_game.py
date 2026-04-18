"""Unit tests for :mod:`heardle.game` — pure state transitions and scoring.

No mocks, no IO. These tests encode the mathematical specification of the game
directly:

- $d_i \\in \\{1, 2, 4, 7, 11, 16\\}$ seconds for $i \\in \\{0, \\dots, 5\\}$.
- Score $s = 6 - i$ on a correct guess at round $i$; $s = 0$ on exhaustion.

Test input rationale is stated inline per global testing standards.
"""

from __future__ import annotations

import pytest

from heardle.game import (
    CLIP_LENGTHS_SECONDS,
    MAX_ROUND,
    TOTAL_ROUNDS,
    apply_guess,
    clip_length_for,
    initial_state,
    score,
    skip_round,
)

TARGET = "target_spotify_id"
WRONG = "wrong_spotify_id"


# ---------------------------------------------------------------------------
# clip_length_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "round_index,expected",
    [
        # Rationale: exhaustively cover the entire $d_i$ schedule. These five
        # pairs are the mathematical spec of the game — if any of them is wrong,
        # the game is no longer Heardle.
        (0, 1),
        (1, 2),
        (2, 4),
        (3, 7),
        (4, 11),
        (5, 16),
    ],
)
def test_clip_length_for_valid_rounds(round_index: int, expected: int) -> None:
    assert clip_length_for(round_index) == expected


@pytest.mark.parametrize(
    "out_of_range",
    # Rationale: boundary testing — exactly one below the minimum, exactly one
    # above the maximum, and an obviously invalid negative value.
    [-1, 6, -100, 100],
)
def test_clip_length_for_out_of_range_raises(out_of_range: int) -> None:
    with pytest.raises(IndexError):
        clip_length_for(out_of_range)


def test_clip_length_sequence_is_convex() -> None:
    """Second differences are non-negative — the sequence grows at least linearly.

    Rationale: Heardle's pacing depends on convexity so each additional guess
    reveals meaningfully more of the track. A regression (e.g. someone
    replacing the sequence with {1,2,3,4,5,6}) would break game feel even if
    individual values pass the parametrised test above.
    """
    d = CLIP_LENGTHS_SECONDS
    second_diffs = [d[i + 2] - 2 * d[i + 1] + d[i] for i in range(len(d) - 2)]
    assert all(
        sd >= 0 for sd in second_diffs
    ), f"Second differences {second_diffs} contain a negative value."


# ---------------------------------------------------------------------------
# initial_state
# ---------------------------------------------------------------------------


def test_initial_state() -> None:
    """A fresh game starts at round 0 with no guesses, not finished.

    Rationale: the invariants downstream code depends on. Any transition
    function that accidentally returned a non-fresh state here would break
    every test below.
    """
    s = initial_state(TARGET)
    assert s.target_spotify_id == TARGET
    assert s.round_index == 0
    assert s.guesses == ()
    assert s.finished is False
    assert s.won is False


# ---------------------------------------------------------------------------
# apply_guess + score — win paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "win_at_round,expected_score",
    [
        # Rationale: every possible winning round, with the expected score
        # computed directly from $s = 6 - i$. Brittle on purpose: if the scoring
        # formula regresses, at least one of these breaks.
        (0, 6),
        (1, 5),
        (2, 4),
        (3, 3),
        (4, 2),
        (5, 1),
    ],
)
def test_win_at_each_round_scores_correctly(win_at_round: int, expected_score: int) -> None:
    s = initial_state(TARGET)
    # Burn ``win_at_round`` wrong guesses to reach that round.
    for _ in range(win_at_round):
        s = apply_guess(s, WRONG)
    # Then win.
    s = apply_guess(s, TARGET)
    assert s.finished is True
    assert s.won is True
    assert score(s) == expected_score
    # Guess log has one entry per action — wrongs + the winning guess.
    assert len(s.guesses) == win_at_round + 1


def test_win_freezes_round_index() -> None:
    """A correct guess must not advance ``round_index``.

    Rationale: :func:`score` derives the round-at-win from ``round_index``. If
    the transition advanced on a win, every score would be off by one.
    """
    s = initial_state(TARGET)
    s = apply_guess(s, WRONG)  # now at round 1
    s = apply_guess(s, TARGET)  # win at round 1
    assert s.round_index == 1


# ---------------------------------------------------------------------------
# apply_guess — loss path (exhaustion)
# ---------------------------------------------------------------------------


def test_exhaustion_after_six_wrong_guesses() -> None:
    """Six wrong guesses total = finished, lost, score 0.

    Rationale: $|\\{d_i\\}| = 6$ rounds, so exactly six failures exhausts.
    """
    s = initial_state(TARGET)
    for _ in range(TOTAL_ROUNDS):
        s = apply_guess(s, WRONG)
    assert s.finished is True
    assert s.won is False
    assert s.round_index == MAX_ROUND
    assert len(s.guesses) == TOTAL_ROUNDS
    assert score(s) == 0


def test_apply_guess_on_finished_raises() -> None:
    """Posting a guess after the game ends is a programming error.

    Rationale: fail loudly rather than silently no-op, which would otherwise
    allow the UI to keep accepting inputs after the game has ended.
    """
    s = initial_state(TARGET)
    s = apply_guess(s, TARGET)  # win
    with pytest.raises(ValueError):
        apply_guess(s, WRONG)


# ---------------------------------------------------------------------------
# skip_round
# ---------------------------------------------------------------------------


def test_skip_advances_round_like_wrong_guess() -> None:
    """A skip advances ``round_index`` the same way a wrong guess does.

    Rationale: structurally equivalent transitions keep the game loop simple;
    the only difference is the ``None`` marker in the guess log, which is a UI
    concern not a game-logic one.
    """
    s = initial_state(TARGET)
    s = skip_round(s)
    assert s.round_index == 1
    assert s.guesses == (None,)
    assert s.finished is False


def test_all_skips_exhausts() -> None:
    """Six skips end the game with score 0.

    Rationale: someone who skips every round should lose cleanly, not hit an
    edge case where the game hangs finished=False.
    """
    s = initial_state(TARGET)
    for _ in range(TOTAL_ROUNDS):
        s = skip_round(s)
    assert s.finished is True
    assert s.won is False
    assert score(s) == 0
    assert all(g is None for g in s.guesses)


def test_skip_then_correct_guess_scores_correctly() -> None:
    """Mixed skips and a win at round 2 scores 6 - 2 = 4.

    Rationale: real players mix skips with guesses. Covers the interaction
    between the two transition functions.
    """
    s = initial_state(TARGET)
    s = skip_round(s)  # round 1
    s = skip_round(s)  # round 2
    s = apply_guess(s, TARGET)  # win
    assert s.won is True
    assert score(s) == 4
    assert s.guesses == (None, None, TARGET)


def test_skip_on_finished_raises() -> None:
    """Skipping a finished game is a programming error.

    Rationale: symmetric with the apply_guess guard above.
    """
    s = initial_state(TARGET)
    s = apply_guess(s, TARGET)  # win
    with pytest.raises(ValueError):
        skip_round(s)


# ---------------------------------------------------------------------------
# score — mid-game
# ---------------------------------------------------------------------------


def test_score_mid_game_raises() -> None:
    """Score is undefined while the game is in progress.

    Rationale: prevents the UI from accidentally displaying a "score" before
    the player has finished. Raising forces callers to check ``finished``.
    """
    s = initial_state(TARGET)
    s = apply_guess(s, WRONG)
    with pytest.raises(ValueError):
        score(s)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_apply_guess_does_not_mutate_input() -> None:
    """State is immutable; transitions return new objects.

    Rationale: dataclass frozenness is a runtime guarantee but a regression in
    ``_advance`` could still return the same object. Verifying identity forces
    the intent to stay explicit.
    """
    s0 = initial_state(TARGET)
    s1 = apply_guess(s0, WRONG)
    assert s1 is not s0
    assert s0.round_index == 0  # unchanged
    assert s0.guesses == ()  # unchanged
