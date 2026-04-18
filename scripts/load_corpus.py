"""Convert a raw Kaggle Spotify-songs CSV into the canonical corpus parquet.

This is a one-off tool, run manually after downloading a dataset from Kaggle.
It auto-detects the two common schemas this project supports and writes the
canonical columns consumed by :mod:`heardle.corpus`:

``spotify_id, title, primary_artist, album_name, release_year, popularity``.

Supported inputs
----------------
1. ``rodolfofigueroa/spotify-1.2m-songs`` — columns ``id, name, artists, album,
   year, ...``. Lacks ``popularity``; the script writes ``popularity`` as all
   nulls so the loader falls back to the year-threshold filter.

2. ``maharshipandya/spotify-tracks-dataset`` — columns ``track_id, track_name,
   artists, album_name, popularity, ...``. Has a real ``popularity`` column
   the loader's popularity-threshold filter can use.

Usage
-----
::

    uv run python -m scripts.load_corpus --input path/to/raw.csv
    uv run python -m scripts.load_corpus \
        --input path/to/raw.csv \
        --output data/popular_corpus.parquet

The output defaults to ``data/popular_corpus.parquet`` (gitignored).
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import pandas as pd

CANONICAL_COLUMNS: list[str] = [
    "spotify_id",
    "title",
    "primary_artist",
    "album_name",
    "release_year",
    "popularity",
]

# Schema-detection fingerprints: a tuple of columns whose simultaneous presence
# uniquely identifies the source dataset. Keeps the detection declarative.
_SCHEMA_FINGERPRINTS: dict[str, tuple[str, ...]] = {
    "rodolfofigueroa": ("id", "name", "artists", "year"),
    "maharshipandya": ("track_id", "track_name", "artists", "popularity"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Convert a raw Kaggle Spotify-songs CSV into the canonical corpus parquet.")
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the raw CSV downloaded from Kaggle.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/popular_corpus.parquet"),
        help="Destination parquet path (default: data/popular_corpus.parquet).",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: input CSV not found: {args.input}", file=sys.stderr)
        return 2

    print(f"Reading {args.input} ...", file=sys.stderr)
    df_raw = pd.read_csv(args.input)
    schema = _detect_schema(df_raw.columns.tolist())
    print(f"Detected schema: {schema}", file=sys.stderr)

    df_canon = _to_canonical(df_raw, schema)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df_canon.to_parquet(args.output, index=False)

    print(f"Wrote {len(df_canon):,} rows to {args.output}", file=sys.stderr)
    _print_summary(df_canon)
    return 0


def _detect_schema(columns: list[str]) -> str:
    """Return the schema name whose fingerprint is fully present in ``columns``."""
    col_set = set(columns)
    for name, fingerprint in _SCHEMA_FINGERPRINTS.items():
        if set(fingerprint).issubset(col_set):
            return name
    raise ValueError(
        f"Could not identify schema from columns {columns}. "
        f"Supported fingerprints: {_SCHEMA_FINGERPRINTS}"
    )


def _to_canonical(df_raw: pd.DataFrame, schema: str) -> pd.DataFrame:
    """Dispatch to the schema-specific converter and return canonical columns."""
    if schema == "rodolfofigueroa":
        df = _from_rodolfofigueroa(df_raw)
    elif schema == "maharshipandya":
        df = _from_maharshipandya(df_raw)
    else:  # defensive — _detect_schema is already exhaustive
        raise ValueError(f"Unknown schema: {schema}")
    # Drop rows with missing essentials; cast to stable dtypes.
    df = df.dropna(subset=["spotify_id", "title", "primary_artist", "release_year"])
    df = df.astype(
        {
            "spotify_id": "string",
            "title": "string",
            "primary_artist": "string",
            "album_name": "string",
            "release_year": "int32",
            "popularity": "Int32",  # nullable
        }
    )
    return df[CANONICAL_COLUMNS].reset_index(drop=True)


def _from_rodolfofigueroa(df: pd.DataFrame) -> pd.DataFrame:
    """Map the 1.2M-songs schema onto canonical columns.

    Notes:
    - ``artists`` arrives as a stringified Python list (``"['A', 'B']"``);
      we ``ast.literal_eval`` each cell and keep the first element.
    - ``year`` is already an integer; ``release_date`` is ignored since year
      suffices for our filter.
    - ``popularity`` is absent, set to pandas ``<NA>``.
    """
    return pd.DataFrame(
        {
            "spotify_id": df["id"],
            "title": df["name"],
            "primary_artist": df["artists"].apply(_first_artist_from_listlike),
            "album_name": df.get("album", pd.Series([""] * len(df))),
            "release_year": df["year"],
            "popularity": pd.NA,
        }
    )


def _from_maharshipandya(df: pd.DataFrame) -> pd.DataFrame:
    """Map the 114k-tracks schema onto canonical columns.

    ``artists`` here is a ``;``-delimited string (``"Artist A;Artist B"``).
    ``release_year`` is extracted from ``album_name`` if a year column is not
    present; some variants of this dataset ship a ``year`` column and some do
    not.
    """
    if "year" in df.columns:
        release_year = df["year"]
    elif "release_date" in df.columns:
        release_year = df["release_date"].astype(str).str.slice(0, 4)
    else:
        raise ValueError("maharshipandya variant lacks both 'year' and 'release_date' columns")
    return pd.DataFrame(
        {
            "spotify_id": df["track_id"],
            "title": df["track_name"],
            "primary_artist": df["artists"].apply(_first_artist_from_semicolon_string),
            "album_name": df.get("album_name", pd.Series([""] * len(df))),
            "release_year": release_year,
            "popularity": df["popularity"],
        }
    )


def _first_artist_from_listlike(raw: object) -> str | None:
    """Parse a stringified list of artists and return the first name.

    Spotify's canonical ``artists`` field is an ordered list whose first
    element is the primary artist; collaborators follow.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    first = parsed[0]
    return str(first) if first is not None else None


def _first_artist_from_semicolon_string(raw: object) -> str | None:
    """Return the substring before the first semicolon in a joined-artists string."""
    if not isinstance(raw, str) or not raw:
        return None
    return raw.split(";", 1)[0].strip() or None


def _print_summary(df: pd.DataFrame) -> None:
    """Emit a short statistical summary of the produced parquet for operator review."""
    print("\nSummary:", file=sys.stderr)
    print(f"  rows:               {len(df):,}", file=sys.stderr)
    print(
        f"  release_year range: {int(df['release_year'].min())} - "
        f"{int(df['release_year'].max())}",
        file=sys.stderr,
    )
    if df["popularity"].notna().any():
        nonnull = df["popularity"].dropna()
        print(
            f"  popularity:         present ({len(nonnull):,} non-null, "
            f"median={int(nonnull.median())}, "
            f"mean={nonnull.mean():.1f})",
            file=sys.stderr,
        )
    else:
        print("  popularity:         absent (dataset lacks the column)", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
