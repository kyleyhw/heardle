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

## Phase 5: Frontend + full API routes
18. [completed] Jinja templates: `base.html`, `index.html`, `game.html`, `partials/game_body.html` (htmx-swappable).
19. [completed] `static/player.js` — SDK init with `getOAuthToken` → `/api/token`, device transfer, clip cutoff anchored to `player_state_changed` events for ±20 ms precision.
20. [completed] `static/autocomplete.js` — debounced typeahead (300 ms) hitting `/autocomplete?q=X&game_id=Y`, re-initialises on every htmx swap.
21. [completed] `static/styles.css` — dark Heardle-esque layout with stacked round rows and Spotify-green accent.
22. [completed] `api.py` expanded to full routing: `/`, `/auth/login`, `/callback`, `/auth/logout`, `/api/token`, `/game/new`, `/game/{id}`, `/game/{id}/guess|skip|play`, `/autocomplete`. Target track URI held server-side only (cheat-prevention). 14 unit tests via `TestClient` with dependency overrides.

## Phase 6: End-to-end verification
21. [completed] SETUP.md walkthrough (Spotify app, Kaggle download, corpus loader) and docs/deployment.md (optional Fly.io/Railway/Render hosting).
22. [completed] Part A: automated checks pass (94 unit tests, 3 skipped integration, ruff + ty + detect-secrets green, server boots with only a placeholder `.env`). Report at `tests/reports/2026-04-19-phase6-initial-verification.md`.
23. [pending] Part B: credential-gated integration tests + corpus load + manual play-through + clip-timing verification. Requires user-side Spotify Developer app registration and Kaggle download. Follow-up dated report under `tests/reports/` when complete.
