# Popular-songs corpus: filter derivation

## Statement of the problem

The original specification calls for the guess autocomplete pool to include "all Spotify songs with over 10,000 plays". Two external facts make this literally impossible (see [architecture.md](architecture.md) and the top-level plan for details):

1. **Spotify never exposed raw play counts on the public Web API.** The closest documented proxy is the `popularity` field $p \in [0, 100]$.
2. **The February 2026 Web API update removed the `popularity` field from Development-Mode apps.** Live filtering is therefore gated on Extended-Quota approval, which is not granted to hobby projects by default.

We substitute a **precomputed snapshot** (Kaggle / HuggingFace) and apply filters to approximate the requested cutoff.

## Dataset in use

**`rodolfofigueroa/spotify-1.2m-songs`** (Kaggle) — ~1.2M rows. Audio features + metadata + release year. **Lacks a `popularity` column.**

### Why not the smaller `maharshipandya` dataset?

The `maharshipandya/spotify-tracks-dataset` (~114k rows) *does* carry `popularity`, and would let us apply the clean $p \geq \tau$ filter. We chose the larger dataset for the "vast" feel explicitly requested, accepting the need for a substitute filter. The loader script supports both schemas via auto-detection, so swapping datasets is a single CLI arg away.

## Substitute filter: release year

Since the bigger dataset has no `popularity`, we fall back to a **year threshold** as a secondary proxy, on the heuristic that modern releases are overrepresented in "tracks a listener could plausibly recognise" — the catalogue of old remasters in the dataset skews toward obscure editions of otherwise-famous songs, which hurts recall of the *same* songs in their canonical Spotify IDs.

We adopt $y_\tau = 2000$ as the default, giving

$$\mathcal{P} = \{t \in \text{snapshot} : \mathrm{release\_year}(t) \geq y_\tau\}.$$

Empirically on the 1.2M snapshot this reduces $|\mathcal{P}|$ from ~$1.2 \times 10^6$ to ~$7 \times 10^5$ — still comfortably "vast", memory-bounded, and fast enough for the rapidfuzz autocomplete to feel responsive with ~300 ms of client-side debouncing.

The threshold is configurable via `YEAR_THRESHOLD` in `.env`. The popularity filter is also still available via `POPULARITY_THRESHOLD`; when both are set, both apply (logical AND). When the dataset lacks `popularity` entirely (all-null column), the popularity filter silently no-ops and only the year filter applies.

## How to tune

After running the loader, produce the distribution plot:

```bash
uv run python -m scripts.plot_corpus_distribution
```

This writes `docs/corpus_distribution.png` with a log-scale histogram of `release_year` (plus `popularity` if the dataset has it). The interpretation:

- **x-axis**: release year (and popularity, when available).
- **y-axis**: log-scale count of tracks at that year.
- **What to look for**: the year where the pre-Spotify-era long tail flattens into the modern catalogue density. Early-2000s onwards is where major-label releases with first-class Spotify metadata begin. Tracks before that era are largely reissue / licensing imports whose popularity curve does not reflect actual listenership.
- **Takeaway**: $y_\tau$ should sit at or just above this knee. 2000 is the current default because it coincides with both the knee in modern snapshots and the start of the mainstream-download era.

## Snapshot staleness and mitigation

A snapshot is frozen in time. New releases between snapshot date and game time will not appear in $\mathcal{P}$ — but they will still appear in $\mathcal{G} = \mathcal{P} \cup \mathcal{C}$ whenever they are in the current correct-answer pool. The player will therefore *always* be able to type the target; staleness only reduces red-herring variety.

Refresh procedure: re-download the CSV from Kaggle and re-run `uv run python -m scripts.load_corpus --input path/to/fresh.csv`.

## Future enhancement: backfill popularity via Spotify API

If Extended Quota Mode is eventually obtained (or a one-off annotation run is acceptable), `spotify.py` already exposes the `/v1/tracks?ids=...` batch endpoint. A background script could iterate the snapshot in batches of 50 and write the returned popularity values back into the parquet, promoting the year-based filter to the original popularity-based one. At ~100 requests per minute post-Dec-2025 rate-limit tightening, 1.2M / 50 = 24,000 batches ≈ 4 hours of wallclock time — feasible, but not currently needed.

## References

<span id="ref-spotify-popularity">[1]</span> Spotify for Developers. *Web API Reference — Track Object `popularity` field.* [Link](https://developer.spotify.com/documentation/web-api/reference/get-track)
