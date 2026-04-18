"""Produce a histogram of ``release_year`` (and optionally ``popularity``) for
the canonical corpus parquet, saved to ``docs/corpus_distribution.png``.

Supports the threshold-tuning workflow described in ``docs/corpus_threshold.md``:
inspect the distribution, identify the knee, set the threshold accordingly.

Usage
-----
::

    uv run python -m scripts.plot_corpus_distribution
    uv run python -m scripts.plot_corpus_distribution --input data/popular_corpus.parquet

Output
------
Writes ``docs/corpus_distribution.png`` with one or two subplots depending on
whether the dataset exposes popularity.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plot the release-year (and popularity, if present) distribution of the "
            "canonical corpus parquet."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/popular_corpus.parquet"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/corpus_distribution.png"),
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: input parquet not found: {args.input}", file=sys.stderr)
        return 2

    df = pd.read_parquet(args.input)
    has_popularity = df["popularity"].notna().any()

    fig, axes = plt.subplots(1, 2 if has_popularity else 1, figsize=(12, 4))
    axes_list = axes if has_popularity else [axes]

    _plot_year_histogram(axes_list[0], df)
    if has_popularity:
        _plot_popularity_histogram(axes_list[1], df)

    fig.suptitle(f"Corpus distribution ({len(df):,} rows from {args.input.name})")
    fig.tight_layout()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=120)
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


def _plot_year_histogram(ax: plt.Axes, df: pd.DataFrame) -> None:
    """Log-scale histogram of release_year to expose the long pre-Spotify-era tail."""
    years = df["release_year"].dropna().astype(int)
    ax.hist(years, bins=range(int(years.min()), int(years.max()) + 2), color="#1f77b4")
    ax.set_yscale("log")
    ax.set_xlabel("release year")
    ax.set_ylabel("count (log scale)")
    ax.set_title("Tracks by release year")
    # Reference line at 2000 — our default YEAR_THRESHOLD.
    ax.axvline(2000, color="red", linestyle="--", linewidth=1, label="YEAR_THRESHOLD=2000")
    ax.legend()


def _plot_popularity_histogram(ax: plt.Axes, df: pd.DataFrame) -> None:
    """Histogram of popularity ∈ [0, 100] with the threshold line highlighted."""
    popularity = df["popularity"].dropna().astype(int)
    ax.hist(popularity, bins=range(0, 102), color="#ff7f0e")
    ax.set_xlabel("popularity")
    ax.set_ylabel("count")
    ax.set_title("Tracks by popularity")
    ax.axvline(25, color="red", linestyle="--", linewidth=1, label="POPULARITY_THRESHOLD=25")
    ax.legend()


if __name__ == "__main__":
    raise SystemExit(main())
