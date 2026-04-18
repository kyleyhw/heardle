"""Unit tests for :mod:`heardle.corpus`.

Uses small synthetic DataFrames so no real Kaggle parquet is needed. Covers
normalisation edge cases, the two independent filters, autocomplete recall,
and the union operation for 𝒢 = 𝒫 ∪ 𝒞.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from heardle.corpus import (
    load_corpus,
    make_search_text,
    normalize,
)
from heardle.spotify import Track


def _sample_df() -> pd.DataFrame:
    """Return a small, hand-picked corpus DataFrame.

    Rationale for these rows: the set deliberately mixes (a) high- and low-
    popularity tracks, (b) pre-2000 and post-2000 release years, and (c)
    titles with diacritics / parenthesised suffixes, so each downstream test
    can exercise real-world string shapes without needing a large fixture.
    """
    rows = [
        # popular + modern + diacritic in artist name
        ("id_1", "Halo", "Beyoncé", "I Am... Sasha Fierce", 2008, 80),
        # popular + modern + parenthesised suffix in title
        (
            "id_2",
            "Shape of You (Radio Edit)",
            "Ed Sheeran",
            "÷",
            2017,
            90,
        ),
        # obscure + modern
        ("id_3", "Obscure Track", "Nobody In Particular", "Self Released", 2022, 5),
        # popular + pre-2000 (a candidate the year-threshold drops)
        ("id_4", "Bohemian Rhapsody", "Queen", "A Night at the Opera", 1975, 75),
        # popular + missing popularity (null cell)
        (
            "id_5",
            "Blinding Lights",
            "The Weeknd",
            "After Hours",
            2020,
            None,
        ),
    ]
    df = pd.DataFrame(
        rows,
        columns=[
            "spotify_id",
            "title",
            "primary_artist",
            "album_name",
            "release_year",
            "popularity",
        ],
    )
    # Nullable integer dtype — matches what ``scripts/load_corpus.py`` writes.
    df = df.astype({"release_year": "int32", "popularity": "Int32"})
    return df


@pytest.fixture
def sample_parquet(tmp_path: Path) -> Path:
    """Write a small corpus to a temp parquet for ``load_corpus`` tests."""
    path = tmp_path / "corpus.parquet"
    _sample_df().to_parquet(path, index=False)
    return path


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Rationale: lowercase + diacritic-strip is the baseline transformation.
        ("Beyoncé", "beyonce"),
        # Rationale: parenthesised suffixes ("Remastered", "feat.", etc.) appear
        # on a large fraction of Spotify titles; we strip them so fuzzy matches
        # survive.
        ("Shape of You (Radio Edit)", "shape of you"),
        ("Hotel California (Remastered 2013)", "hotel california"),
        ("Super Freaky Girl [feat. Someone]", "super freaky girl"),
        # Rationale: punctuation must not block matches (apostrophes, commas).
        ("Don't Stop Me Now", "dont stop me now"),
        # Rationale: whitespace collapse.
        ("   too   many   spaces   ", "too many spaces"),
        # Rationale: empty input must not blow up.
        ("", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_make_search_text_concatenates() -> None:
    """Rationale: make_search_text is the field rapidfuzz actually searches against;
    regression here silently breaks every autocomplete query."""
    assert make_search_text("Halo", "Beyoncé") == "halo beyonce"


# ---------------------------------------------------------------------------
# load_corpus — filters
# ---------------------------------------------------------------------------


def test_load_corpus_no_filter_returns_all(sample_parquet: Path) -> None:
    """Rationale: baseline — no filter keeps every row."""
    corpus = load_corpus(str(sample_parquet))
    assert len(corpus) == 5


def test_load_corpus_popularity_threshold_drops_low_popularity(
    sample_parquet: Path,
) -> None:
    """Rationale: popularity ≥ 50 should drop the obscure Nobody-In-Particular track
    and the null-popularity Weeknd row; keep the other three."""
    corpus = load_corpus(str(sample_parquet), popularity_threshold=50)
    ids = {corpus.get_track(sid).spotify_id for sid in ("id_1", "id_2", "id_4")}
    assert len(corpus) == 3
    assert ids == {"id_1", "id_2", "id_4"}


def test_load_corpus_year_threshold_drops_pre_cutoff(sample_parquet: Path) -> None:
    """Rationale: year ≥ 2000 drops the 1975 Queen track; keeps the other four."""
    corpus = load_corpus(str(sample_parquet), year_threshold=2000)
    assert len(corpus) == 4
    assert "id_4" not in corpus


def test_load_corpus_both_filters_compose(sample_parquet: Path) -> None:
    """Rationale: both filters apply with logical AND — modern AND popular."""
    corpus = load_corpus(str(sample_parquet), popularity_threshold=50, year_threshold=2000)
    # id_1 (2008, pop=80) and id_2 (2017, pop=90) survive; id_4 drops on year,
    # id_3 on popularity, id_5 on null-popularity (treated as -1 for the
    # purpose of the comparison).
    assert len(corpus) == 2
    assert "id_1" in corpus
    assert "id_2" in corpus


def test_load_corpus_popularity_filter_silently_skipped_when_all_null(
    tmp_path: Path,
) -> None:
    """Rationale: the 1.2M rodolfofigueroa dataset has no popularity, but a user
    might still leave POPULARITY_THRESHOLD set. The filter must silently no-op
    rather than filtering everything out."""
    df = _sample_df()
    df["popularity"] = pd.NA
    path = tmp_path / "no_pop.parquet"
    df.to_parquet(path, index=False)

    corpus = load_corpus(str(path), popularity_threshold=50)
    assert len(corpus) == 5


# ---------------------------------------------------------------------------
# Corpus — basic queries
# ---------------------------------------------------------------------------


def test_corpus_contains_and_get_track(sample_parquet: Path) -> None:
    """Rationale: these two methods are the basis of ``union_with`` dedup and
    of the game-engine's post-win display of the target track details."""
    corpus = load_corpus(str(sample_parquet))
    assert "id_1" in corpus
    assert "not_a_real_id" not in corpus
    track = corpus.get_track("id_1")
    assert isinstance(track, Track)
    assert track.title == "Halo"
    assert track.primary_artist == "Beyoncé"
    assert track.popularity == 80


def test_corpus_get_track_raises_for_missing(sample_parquet: Path) -> None:
    corpus = load_corpus(str(sample_parquet))
    with pytest.raises(KeyError):
        corpus.get_track("not_a_real_id")


# ---------------------------------------------------------------------------
# Corpus.autocomplete
# ---------------------------------------------------------------------------


def test_autocomplete_finds_exact_match(sample_parquet: Path) -> None:
    """Rationale: the minimal behaviour — the autocomplete must surface the
    target when the user types (essentially) the title."""
    corpus = load_corpus(str(sample_parquet))
    matches = corpus.autocomplete("shape of you")
    ids = [t.spotify_id for t in matches]
    assert "id_2" in ids


def test_autocomplete_handles_diacritics(sample_parquet: Path) -> None:
    """Rationale: users type "beyonce" far more often than "Beyoncé"; the
    normalisation must bridge that gap."""
    corpus = load_corpus(str(sample_parquet))
    matches = corpus.autocomplete("beyonce halo")
    assert any(t.spotify_id == "id_1" for t in matches)


def test_autocomplete_empty_query_returns_empty(sample_parquet: Path) -> None:
    """Rationale: empty input should not trigger a rapidfuzz scan over the
    entire corpus (it would return nonsense and waste CPU)."""
    corpus = load_corpus(str(sample_parquet))
    assert corpus.autocomplete("") == []
    assert corpus.autocomplete("   ") == []


def test_autocomplete_respects_limit(sample_parquet: Path) -> None:
    """Rationale: the UI shows a capped dropdown; the wrapper must honour the cap.

    A deliberately broad query is used so the ``limit`` bound, not the match
    count, is what constrains the result size.
    """
    corpus = load_corpus(str(sample_parquet))
    matches = corpus.autocomplete("a", limit=2, score_cutoff=0.0)
    assert len(matches) <= 2


# ---------------------------------------------------------------------------
# Corpus.union_with — 𝒢 = 𝒫 ∪ 𝒞
# ---------------------------------------------------------------------------


def test_union_adds_new_tracks(sample_parquet: Path) -> None:
    """Rationale: a track in 𝒞 but not 𝒫 must appear in 𝒢 so the player can
    type its name in the guess box even if it is obscure."""
    corpus = load_corpus(str(sample_parquet))
    extra = Track(
        spotify_id="extra_id",
        title="Not In Corpus",
        primary_artist="Some Artist",
        album_name="X",
        release_year=2023,
        popularity=3,
    )
    merged = corpus.union_with([extra])
    assert len(merged) == len(corpus) + 1
    assert "extra_id" in merged


def test_union_dedups_by_spotify_id(sample_parquet: Path) -> None:
    """Rationale: ``𝒞 ⊂ 𝒫`` is common (the user-supplied playlist shares tracks
    with the popular corpus). Duplicates would appear twice in autocomplete and
    confuse rank-based scoring."""
    corpus = load_corpus(str(sample_parquet))
    # Reuse an existing id — the union should not add it.
    dup = Track(
        spotify_id="id_1",
        title="Halo",
        primary_artist="Beyoncé",
        album_name="I Am... Sasha Fierce",
        release_year=2008,
        popularity=80,
    )
    merged = corpus.union_with([dup])
    assert len(merged) == len(corpus)


def test_union_with_empty_returns_same_object(sample_parquet: Path) -> None:
    """Rationale: a micro-optimisation — no new tracks means no DataFrame copy.

    The game engine calls ``union_with`` on every new game; avoiding a copy
    when the playlist's tracks are all already in the corpus saves a lot of
    allocations over time.
    """
    corpus = load_corpus(str(sample_parquet))
    merged = corpus.union_with([])
    assert merged is corpus
