# Project Development Plan

This document outlines the planned phases and tasks for developing the Heardle clone. The underlying architectural plan lives at `C:\Users\Kyle\.claude\plans\dazzling-launching-crab.md`; this file tracks progress.

## Phase 1: Scaffolding
1. [completed] Initialise `uv` package project (`uv init --package --name heardle`).
2. [completed] Add runtime dependencies (`fastapi`, `uvicorn`, `httpx`, `pydantic`, `python-dotenv`, `jinja2`, `rapidfuzz`, `pandas`, `pyarrow`, `itsdangerous`).
3. [completed] Add dev dependencies (`ruff`, `ty`, `detect-secrets`, `pytest`, `pytest-asyncio`, `respx`, `pre-commit`, `matplotlib`).
4. [completed] Write `.gitignore`, `.env.example`, `.pre-commit-config.yaml`, generate `.secrets.baseline`.
5. [completed] Write README, PROJECT_PLAN, and `/docs/` skeleton.
6. [completed] Create `src/heardle/*` module stubs and `tests/` structure.
7. [completed] `git add`, initial commit (no Claude/Anthropic attribution).

## Phase 2: Spotify OAuth + metadata
8. [completed] Implement `config.py` with `python-dotenv` loading and a frozen `Settings` dataclass.
9. [completed] Implement `auth.py` — Authorization Code + Client Credentials + refresh flows, `itsdangerous`-signed state (10 min lifetime), `assert_premium` with explicit `PremiumRequiredError`.
10. [completed] Implement `spotify.py` — async wrapper: playlist (paginated), artist top-tracks, year-search (paginated), single-track metadata; robust `_try_parse_track` that skips malformed / non-track entries.
11. [completed] Unit tests (31, respx-mocked) + integration tests (3, gated on credentials) against Spotify's "Today's Top Hits", Ed Sheeran top-tracks, year=2020 search.

## Phase 3: Pure game logic
12. [completed] Implement `game.py` — frozen `GameState`, `clip_length_for(i)`, `apply_guess`, `skip_round`, `score`. Immutable transitions via `dataclasses.replace`. Fuzzy matching deferred to `corpus.py` (Phase 4) since it belongs with autocomplete, not game logic.
13. [completed] Unit tests (27): every $i \in \{0, \dots, 5\}$ clip length, every winning round, exhaustion via all-wrong and all-skip, mixed skip/guess path, immutability assertion, convexity check on $d_i$, out-of-range bounds on `clip_length_for`, mid-game `score()` raises, finished-state guards on both transition functions.

## Phase 4: Popular-songs corpus
14. [completed] Selected `rodolfofigueroa/spotify-1.2m-songs` (Kaggle) — user explicitly chose the larger dataset. Loader auto-detects and also supports `maharshipandya/spotify-tracks-dataset` as a fallback.
15. [completed] `scripts/load_corpus.py` — schema-auto-detecting CSV → canonical parquet converter with `--input` / `--output` CLI args.
16. [completed] `scripts/plot_corpus_distribution.py` — matplotlib histogram for empirical threshold tuning (year and, when present, popularity). Rationale documented in `docs/corpus_threshold.md`.
17. [completed] Implemented `corpus.py` — pandas-backed `Corpus`, `load_corpus(path, popularity_threshold, year_threshold)`, `autocomplete` via rapidfuzz `WRatio`, `union_with(extra_tracks)` for $\mathcal{G} = \mathcal{P} \cup \mathcal{C}$, `normalize` handling diacritics / apostrophes / parenthesised suffixes. 22 unit tests against a synthetic DataFrame.

## Phase 5: Frontend
18. [pending] Jinja templates: `base.html`, `index.html`, `game.html`, partials for guess rounds.
19. [pending] `static/player.js` — Web Playback SDK init, device transfer, playback start at offset 0, clip cutoff via `player_state_changed` events.
20. [pending] `static/styles.css` — minimal layout close to original Heardle's visual grammar (stacked guess rows, progress bar).

## Phase 6: End-to-end verification
21. [pending] Run full `pytest` suite.
22. [pending] Manual play-through with a Premium account; verify clip timings via browser console deltas.
23. [pending] Write test report in `tests/reports/YYYY-MM-DD-initial-verification.md` per user global testing standards.
