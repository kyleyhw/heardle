# Heardle

A self-hosted, single-player Heardle clone that accepts any **Spotify artist**, **release year**, or **Spotify playlist** as the pool of possible answers. Plays expanding intro snippets (1 → 2 → 4 → 7 → 11 → 16 s) using the Spotify Web Playback SDK, with a search-as-you-type guess box whose autocomplete pool is the union of a precomputed popular-songs corpus and the current answer pool.

## Directory structure

```
heardle/
├── src/heardle/
│   ├── __init__.py
│   ├── auth.py              OAuth Authorization Code flow, token refresh, Premium check
│   ├── spotify.py           Async wrapper over the Spotify Web API
│   ├── corpus.py            Loads the popular-songs parquet, exposes autocomplete index
│   ├── game.py              Pure game logic: d_i schedule, scoring, guess matching
│   ├── config.py            Env-driven settings (pydantic-settings)
│   ├── api.py               FastAPI routes + htmx partials
│   ├── templates/           Jinja templates (base, index, game, partials/*)
│   └── static/              player.js (SDK + clip cutoff), styles.css
├── data/
│   └── popular_corpus.parquet   Kaggle snapshot, popularity-filtered at load time
├── docs/
│   ├── architecture.md
│   ├── oauth_flow.md
│   ├── audio_pipeline.md
│   └── corpus_threshold.md
├── tests/
│   ├── unit/
│   ├── integration/
│   └── reports/             Markdown test reports (runtime, method, inputs, rationale)
├── .env.example
├── .pre-commit-config.yaml
├── pyproject.toml
├── PROJECT_PLAN.md
└── README.md
```

## Documentation index

| File | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System-level architecture, request flow, module responsibilities |
| [docs/oauth_flow.md](docs/oauth_flow.md) | Spotify Authorization Code flow details, scopes, Premium verification, token refresh |
| [docs/audio_pipeline.md](docs/audio_pipeline.md) | Web Playback SDK integration, clip-cutoff precision, known latency sources |
| [docs/corpus_threshold.md](docs/corpus_threshold.md) | Derivation and empirical justification of the popularity threshold τ |

## Mathematical overview

### Game mechanic

Let the full track audio be $T$ with duration $|T|$ seconds. The game reveals the prefix $T[0, d_i]$ at round $i \in \{0, 1, \dots, 5\}$, where the round-indexed clip lengths are

$$d_i \in \{1,\ 2,\ 4,\ 7,\ 11,\ 16\}\ \text{seconds}.$$

On wrong or skipped guess the round index advances: $i \to i + 1$. On a correct guess at round $i$, the score is

$$s = 6 - i,$$

yielding $s_\text{max} = 6$ (first-round win) and $s = 1$ if the player succeeds at the final round. Exhaustion of round 5 yields $s = 0$.

The sequence $d_i$ is convex and grows faster than linearly, giving the player dramatic extra information per additional guess — a design choice inherited from the original Heardle.

### Answer pools

Let $\mathcal{C}$ denote the correct-answer pool (tracks reachable from the user-supplied artist, release year, or playlist), and $\mathcal{P}$ the popular-songs corpus (described below). The autocomplete pool shown to the player is

$$\mathcal{G} = \mathcal{P} \cup \mathcal{C}.$$

This guarantees that every element of $\mathcal{C}$ is typeable no matter how obscure, while $\mathcal{P}$ supplies a broad pool of plausible red herrings. The daily target is sampled uniformly, $t \sim \mathrm{Uniform}(\mathcal{C})$, with popularity-weighted sampling as a future extension.

### Popularity proxy

Spotify's Web API does not expose raw play counts. The closest proxy, the `popularity` field $p \in [0, 100]$, is documented as roughly logarithmic in recent streams with a recency-weighted decay. Its exact relationship to raw play counts is non-public. As a concrete substitute for the requested "≥ 10,000 Spotify plays" threshold, we apply an empirical popularity threshold $\tau$ to a precomputed dataset snapshot:

$$\mathcal{P} = \{t \in \text{snapshot} : p(t) \geq \tau\}.$$

The rationale for the specific value of $\tau$ is derived from the snapshot's popularity distribution and documented in [docs/corpus_threshold.md](docs/corpus_threshold.md). The interpretation of the histogram is: $\tau$ is chosen at the knee point separating the long obscure tail from the recognisable mainstream mass, tuned so $|\mathcal{P}|$ lands in the $10^4$–$10^5$ range — large enough to feel "vast", small enough to keep memory + autocomplete latency bounded.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync` — install runtime and dev dependencies from `uv.lock`.
3. `cp .env.example .env` and fill in Spotify credentials from the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard). Register the redirect URI exactly as in `.env`.
4. `uv run pre-commit install` — activate the lint + secrets-scan + type-check hooks.
5. Download the popular-songs snapshot per [docs/corpus_threshold.md](docs/corpus_threshold.md), place at `data/popular_corpus.parquet`.
6. `uv run uvicorn heardle.api:app --reload` — launch the server at http://127.0.0.1:8000.

## Running tests

```bash
uv run pytest tests/unit/          # pure-logic tests, no network
uv run pytest tests/integration/   # hits Spotify with a test account
```

Reports land in `tests/reports/` as dated markdown files; see [PROJECT_PLAN.md](PROJECT_PLAN.md) for phase status.
