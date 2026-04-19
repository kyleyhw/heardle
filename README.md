# Heardle

A self-hosted, single-player Heardle clone. Pick an **artist**, a **release year**, or any **search term**; a random track from the resulting pool becomes the target. Plays expanding audio clips (1 → 2 → 4 → 7 → 11 → 16 s) with a search-as-you-type guess box.

Runs out of the box with zero configuration — audio is sourced from iTunes' free 30-second previews. A Spotify-backed alternative (full-track playback, intro-from-t=0 fidelity) is architecturally plumbed but currently disabled pending the February 2026 Web API rework.

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
| [SETUP.md](SETUP.md) | End-to-end setup walkthrough (Spotify app, Kaggle corpus, running the server) |
| [docs/architecture.md](docs/architecture.md) | System-level architecture, request flow, module responsibilities |
| [docs/oauth_flow.md](docs/oauth_flow.md) | Spotify Authorization Code flow details, scopes, Premium verification, token refresh |
| [docs/audio_pipeline.md](docs/audio_pipeline.md) | Web Playback SDK integration, clip-cutoff precision, known latency sources |
| [docs/corpus_threshold.md](docs/corpus_threshold.md) | Derivation and empirical justification of the popular-corpus filter |
| [docs/deployment.md](docs/deployment.md) | Optional public-hosted mode (Fly.io / Railway / Render) |

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

### Autocomplete pool

In the default iTunes mode the autocomplete hits iTunes Search on every (debounced) keystroke, returning iTunes' own relevance-ranked matches. The server additionally sweeps the active game's correct-answer pool for substring matches, guaranteeing that the target is always reachable from at least one substring of its title, even if iTunes' global ranking buries it.

An optional offline-corpus mode (pre-indexed Kaggle snapshot with rapidfuzz autocomplete) is preserved in the codebase from an earlier phase; it is currently off the critical path. See [docs/corpus_threshold.md](docs/corpus_threshold.md).

## Setup

```bash
git clone https://github.com/kyleyhw/heardle.git
cd heardle
uv sync
uv run uvicorn heardle.api:app --reload
```

Navigate to **http://127.0.0.1:8000** and play. No accounts, no API keys,
no Premium. See [SETUP.md](SETUP.md) for the few optional knobs (stable
session secret, pre-commit hooks, future Spotify toggle).

For public-hosting so friends can play by visiting a URL, see
[docs/deployment.md](docs/deployment.md).

## Running tests

```bash
uv run pytest tests/unit/          # pure-logic tests, no network
uv run pytest tests/integration/   # hits Spotify with a test account
```

Reports land in `tests/reports/` as dated markdown files; see [PROJECT_PLAN.md](PROJECT_PLAN.md) for phase status.
