# Popular-songs corpus: threshold derivation

## Statement of the problem

The user's original specification calls for the guess autocomplete pool to include "all Spotify songs with over 10,000 plays". Two external facts make this literally impossible (see [architecture.md](architecture.md) and the top-level plan for details):

1. **Spotify never exposed raw play counts on the public Web API.** The closest proxy is the `popularity` field $p \in [0, 100]$.
2. **The February 2026 Web API update removed the `popularity` field from Development-Mode apps.** Live filtering by popularity is therefore gated on Extended-Quota approval, which is not guaranteed for a hobby project.

We therefore substitute a **precomputed snapshot** (Kaggle / HuggingFace) whose rows already carry `popularity` at the time of the snapshot, and apply an empirical threshold $\tau$ to approximate the requested cutoff.

## From plays to popularity

Spotify documents `popularity` only qualitatively: "the popularity is calculated by algorithm and is based, in the most part, on the total number of plays the track has had and how recent those plays are". Reverse-engineering experiments published by third parties suggest a relationship of the form

$$p \approx a \log_{10}(\text{plays}_\text{recent}) + b,$$

with recency-weighted plays on a half-life of roughly a year, and constants $(a, b)$ that shift over time. The exact mapping is non-public, so any numeric equivalence between "plays" and "popularity" is approximate.

Empirical anchors from community reverse-engineering:
- $p \approx 0$: tracks with effectively no recent plays.
- $p \approx 20\text{–}25$: catalogue tracks recognisable to genre enthusiasts; roughly consistent with $10^4$ recent plays.
- $p \approx 40\text{–}60$: mainstream tracks most listeners would recognise.
- $p \geq 80$: current chart hits.

## Choosing τ

We adopt $\tau = 25$ as the initial threshold, giving $\mathcal{P} = \{t : p(t) \geq 25\}$. Rationale:

- This sits at the lower anchor of "recognisable to a regular listener", avoiding the long obscure tail.
- On typical Spotify snapshots (~1.2M rows), this filters to $\mathcal{O}(10^5)$ tracks — consistent with "vast" but memory-bounded.
- It is conservative in the direction that matters: we over-include rather than exclude, because missing tracks are guaranteed to still appear via the union $\mathcal{G} = \mathcal{P} \cup \mathcal{C}$.

## How to tune

After running the loader script (Phase 4), the popularity distribution will be plotted here (histogram + cumulative). The interpretation:

- **x-axis**: `popularity` ∈ [0, 100].
- **y-axis**: count of tracks at that popularity (log scale).
- **What to look for**: the "knee" where the long obscure tail ends and the recognisable mass begins. $\tau$ should sit at or just below the knee.
- **Takeaway**: the value of $\tau$ is a threshold choice, not a derived constant; it can be re-tuned without changing any code in `game.py` or the rest of the pipeline.

The plot and its caption will be inserted below once the corpus is loaded.

## Snapshot staleness and mitigation

A snapshot is frozen in time. New releases between the snapshot date and the current game will not appear in $\mathcal{P}$ — but they will still appear in $\mathcal{G}$ whenever they are part of the current $\mathcal{C}$, because we always take the union. The user will therefore *always* be able to type the target track; the only impact of staleness is on red-herring variety.

Refresh procedure (to be filled in when the loader script exists): re-run `scripts/load_corpus.py --source <url> --output data/popular_corpus.parquet`.

## References

<span id="ref-spotify-popularity">[1]</span> Spotify for Developers. *Web API Reference — Track Object `popularity` field.* [Link](https://developer.spotify.com/documentation/web-api/reference/get-track)
