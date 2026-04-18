"""Popular-songs corpus — load, filter, and fuzzy-match over a precomputed snapshot.

Design
------
The corpus can hold > 10^6 tracks, so the backing store is a pandas DataFrame
rather than a list of Python dataclasses. Track objects are materialised only
at query output. This keeps memory footprint to ~100 MB for a 1.2M-row
snapshot instead of the ~500 MB a list-of-dataclasses equivalent would use.

Filtering
---------
Two independent filters can be applied at load time, both optional:

- ``popularity_threshold`` (integer): keep rows where ``popularity >= τ``.
  Requires the dataset to expose a ``popularity`` column; datasets like
  ``rodolfofigueroa/spotify-1.2m-songs`` do not.
- ``year_threshold`` (integer): keep rows where ``release_year >= y``. Acts
  as a secondary popularity proxy when the true field is unavailable, on the
  heuristic that a modern release is more likely to be recognised than a
  pre-Spotify-era remaster of an obscure catalogue track.

Either, both, or neither filter may be applied. See
``docs/corpus_threshold.md`` for the empirical rationale.

Autocomplete
------------
:meth:`Corpus.autocomplete` uses ``rapidfuzz.process.extract`` with a
``WRatio`` scorer over a precomputed normalised ``search_text`` column. For a
1.2M-row corpus this runs in ~300–500 ms per query on a single core — slow
enough that the frontend should debounce keystrokes (~300 ms), fast enough
that a typical ``year_threshold=2000`` corpus (~700k rows) feels responsive.

Union
-----
:meth:`Corpus.union_with` computes $\\mathcal{G} = \\mathcal{P} \\cup
\\mathcal{C}$ where $\\mathcal{C}$ is the game's correct-answer pool. The
operation deduplicates by Spotify id and returns a new :class:`Corpus`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

import pandas as pd
from rapidfuzz import fuzz, process

from heardle.spotify import Track

# Canonical schema of ``data/popular_corpus.parquet``. The loader script is
# responsible for producing exactly this schema regardless of the source dataset.
_CANONICAL_COLUMNS: tuple[str, ...] = (
    "spotify_id",
    "title",
    "primary_artist",
    "album_name",
    "release_year",
    "popularity",  # nullable Int64
)

# Precompiled regex for stripping parenthesised suffixes during normalisation.
# Matches "(Remastered 2011)", "(feat. X)", "[Live]", etc.
_PAREN_SUFFIX_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*")

# Precompiled regex for collapsing runs of whitespace into single spaces.
_WHITESPACE_RE = re.compile(r"\s+")

# Apostrophes are intra-word (``don't``, ``it's``); deleting them rather than
# spacing keeps "dont" together, which matches user typing. Covers ASCII,
# Unicode-curly (U+2019), and backtick variants seen in real catalogue data.
_APOSTROPHE_RE = re.compile(r"['\u2018\u2019`]")

# Precompiled regex for stripping non-alphanumeric characters (keep spaces).
# Applied *after* apostrophes have been deleted so inter-word punctuation
# (commas, hyphens between tokens) becomes a space without splitting words.
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    """Normalise a title or artist string for fuzzy matching.

    Applies NFKD Unicode normalisation, strips combining diacritics,
    lowercases, removes parenthesised suffixes, strips remaining punctuation,
    and collapses whitespace. The goal is not to produce a canonical form
    humans would recognise — only to remove noise that would otherwise reduce
    fuzzy-match scores below the recall threshold.
    """
    # NFKD decomposes accented characters into base + combining mark, then we
    # drop the combining marks. This maps "Beyoncé" → "beyonce".
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_accents.lower()
    # Order matters: strip parenthesised suffixes first so closing parens do
    # not survive as stray characters; then delete apostrophes so "don't" stays
    # as "dont" rather than splitting into "don t"; then map remaining
    # inter-word punctuation to spaces.
    without_suffixes = _PAREN_SUFFIX_RE.sub(" ", lowered)
    without_apostrophes = _APOSTROPHE_RE.sub("", without_suffixes)
    without_punct = _PUNCTUATION_RE.sub(" ", without_apostrophes)
    collapsed = _WHITESPACE_RE.sub(" ", without_punct).strip()
    return collapsed


def make_search_text(title: str, primary_artist: str) -> str:
    """Combine a title and artist into the normalised search string used by fuzzy match."""
    return f"{normalize(title)} {normalize(primary_artist)}"


class Corpus:
    """Popularity-filtered snapshot with a fuzzy-match autocomplete index.

    Not a dataclass: the ``_df`` backing store is mutable (pandas) and we want
    controlled access via the methods below rather than raw attribute exposure.
    """

    def __init__(self, df: pd.DataFrame) -> None:
        """Wrap a DataFrame that already contains the canonical columns + ``search_text``.

        Callers should prefer :func:`load_corpus` and :meth:`union_with`; this
        constructor is the low-level entry for tests and internal use.
        """
        missing = set(_CANONICAL_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"Corpus DataFrame missing required columns: {missing}")
        if "search_text" not in df.columns:
            df = df.copy()
            df["search_text"] = [
                make_search_text(title, artist)
                for title, artist in zip(df["title"], df["primary_artist"], strict=True)
            ]
        self._df = df.reset_index(drop=True)
        self._search_texts: list[str] = self._df["search_text"].tolist()
        self._id_to_index: dict[str, int] = {
            sid: i for i, sid in enumerate(self._df["spotify_id"].tolist())
        }

    def __len__(self) -> int:
        return len(self._df)

    def __contains__(self, spotify_id: str) -> bool:
        return spotify_id in self._id_to_index

    def get_track(self, spotify_id: str) -> Track:
        """Materialise a :class:`Track` from the backing DataFrame. KeyError if absent."""
        if spotify_id not in self._id_to_index:
            raise KeyError(spotify_id)
        row = self._df.iloc[self._id_to_index[spotify_id]]
        return _row_to_track(row)

    def autocomplete(
        self,
        query: str,
        *,
        limit: int = 10,
        score_cutoff: float = 60.0,
    ) -> list[Track]:
        """Return the top ``limit`` tracks matching ``query`` by fuzzy score.

        Parameters
        ----------
        query
            Raw user-typed string; will be normalised internally.
        limit
            Maximum number of tracks to return.
        score_cutoff
            rapidfuzz score in [0, 100] below which candidates are dropped.
            60 is a balance between recall (not missing plausible matches) and
            precision (not surfacing nonsense for short / ambiguous queries).
        """
        normalised = normalize(query)
        if not normalised:
            return []
        results = process.extract(
            normalised,
            self._search_texts,
            scorer=fuzz.WRatio,
            limit=limit,
            score_cutoff=score_cutoff,
        )
        return [_row_to_track(self._df.iloc[idx]) for _, _, idx in results]

    def union_with(self, extra_tracks: Iterable[Track]) -> Corpus:
        """Return $\\mathcal{P} \\cup \\mathcal{C}$ as a new :class:`Corpus`.

        Deduplicates by ``spotify_id``; if a track in ``extra_tracks`` is
        already present in the corpus, the corpus's copy is retained.
        """
        new_rows: list[dict[str, object]] = []
        for t in extra_tracks:
            if t.spotify_id in self._id_to_index:
                continue
            new_rows.append(
                {
                    "spotify_id": t.spotify_id,
                    "title": t.title,
                    "primary_artist": t.primary_artist,
                    "album_name": t.album_name,
                    "release_year": t.release_year,
                    "popularity": t.popularity,
                    "search_text": make_search_text(t.title, t.primary_artist),
                }
            )
        if not new_rows:
            return self
        extras_df = pd.DataFrame(new_rows, columns=list(self._df.columns))
        combined = pd.concat([self._df, extras_df], ignore_index=True)
        return Corpus(combined)


def load_corpus(
    parquet_path: str,
    *,
    popularity_threshold: int | None = None,
    year_threshold: int | None = None,
) -> Corpus:
    """Load the canonical parquet and apply optional popularity / year filters.

    Parameters
    ----------
    parquet_path
        Path to a parquet file matching :data:`_CANONICAL_COLUMNS`. Produced
        by ``scripts/load_corpus.py``.
    popularity_threshold
        Keep only rows with ``popularity >= τ``. Silently ignored if the
        dataset's ``popularity`` column is entirely null (as with the 1.2M
        rodolfofigueroa dataset).
    year_threshold
        Keep only rows with ``release_year >= y``.
    """
    df = pd.read_parquet(parquet_path)
    df = _filter(df, popularity_threshold=popularity_threshold, year_threshold=year_threshold)
    return Corpus(df)


def _filter(
    df: pd.DataFrame,
    *,
    popularity_threshold: int | None,
    year_threshold: int | None,
) -> pd.DataFrame:
    """Apply optional popularity and year filters to the canonical DataFrame."""
    if popularity_threshold is not None and df["popularity"].notna().any():
        df = df[df["popularity"].fillna(-1) >= popularity_threshold]
    if year_threshold is not None:
        df = df[df["release_year"] >= year_threshold]
    return df.reset_index(drop=True)


def _row_to_track(row: pd.Series[object]) -> Track:
    """Materialise a :class:`Track` from a single DataFrame row."""
    raw_popularity = row["popularity"]
    popularity = (
        int(raw_popularity) if pd.notna(raw_popularity) else None  # type: ignore[arg-type]
    )
    return Track(
        spotify_id=str(row["spotify_id"]),
        title=str(row["title"]),
        primary_artist=str(row["primary_artist"]),
        album_name=str(row["album_name"]),
        release_year=int(row["release_year"]),  # type: ignore[arg-type]
        popularity=popularity,
    )
