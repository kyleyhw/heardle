"""Popular-songs corpus loader.

Loads a precomputed snapshot from parquet at server startup and exposes an
autocomplete index over the popularity-filtered rows. See
``docs/corpus_threshold.md`` for the rationale behind the popularity cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass

from heardle.spotify import Track


@dataclass
class Corpus:
    """Popularity-filtered snapshot plus a fuzzy-match autocomplete index."""

    tracks: list[Track]
    # Phase 4 will populate a rapidfuzz-backed autocomplete structure here.


def load_corpus(parquet_path: str, popularity_threshold: int) -> Corpus:
    """Load the parquet snapshot and filter to ``popularity >= popularity_threshold``. Phase 4."""
    raise NotImplementedError("Implemented in Phase 4.")


def autocomplete_pool(corpus: Corpus, correct_pool: list[Track]) -> list[Track]:
    """Return the union ``𝒢 = 𝒫 ∪ 𝒞`` as a deduplicated list, keyed by Spotify id. Phase 4."""
    raise NotImplementedError("Implemented in Phase 4.")
