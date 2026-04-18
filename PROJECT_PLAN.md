# Project Development Plan

This document outlines the planned phases and tasks for developing the Heardle clone. The underlying architectural plan lives at `C:\Users\Kyle\.claude\plans\dazzling-launching-crab.md`; this file tracks progress.

## Phase 1: Scaffolding
1. [completed] Initialise `uv` package project (`uv init --package --name heardle`).
2. [completed] Add runtime dependencies (`fastapi`, `uvicorn`, `httpx`, `pydantic`, `python-dotenv`, `jinja2`, `rapidfuzz`, `pandas`, `pyarrow`, `itsdangerous`).
3. [completed] Add dev dependencies (`ruff`, `ty`, `detect-secrets`, `pytest`, `pytest-asyncio`, `respx`, `pre-commit`, `matplotlib`).
4. [completed] Write `.gitignore`, `.env.example`, `.pre-commit-config.yaml`, generate `.secrets.baseline`.
5. [in-progress] Write README, PROJECT_PLAN, and `/docs/` skeleton.
6. [pending] Create `src/heardle/*` module stubs and `tests/` structure.
7. [pending] `git add`, initial commit (no Claude/Anthropic attribution).

## Phase 2: Spotify OAuth + metadata
8. [pending] Implement `config.py` (pydantic-settings over `.env`).
9. [pending] Implement `auth.py` — Authorization Code flow, callback handler, refresh on 401, Premium check via `/v1/me`.
10. [pending] Implement `spotify.py` — async wrapper: fetch playlist tracks, artist top-tracks, year-search tracks, single-track metadata.
11. [pending] Integration test against a known public playlist (e.g. Spotify's "Today's Top Hits").

## Phase 3: Pure game logic
12. [pending] Implement `game.py` — `d_i` schedule, round progression, scoring, title/artist normalisation, rapidfuzz-based matching.
13. [pending] Unit tests: every $i \in \{0, \dots, 5\}$ transition, scoring correctness, exhaustion, boundary inputs on matching.

## Phase 4: Popular-songs corpus
14. [pending] Select a Spotify songs snapshot dataset (Kaggle / HuggingFace); document provenance.
15. [pending] Write a one-off loader script that produces `data/popular_corpus.parquet`.
16. [pending] Plot the popularity distribution, choose threshold $\tau$ empirically, document in `docs/corpus_threshold.md`.
17. [pending] Implement `corpus.py` — load at startup, build rapidfuzz autocomplete index, expose `union(C)` helper.

## Phase 5: Frontend
18. [pending] Jinja templates: `base.html`, `index.html`, `game.html`, partials for guess rounds.
19. [pending] `static/player.js` — Web Playback SDK init, device transfer, playback start at offset 0, clip cutoff via `player_state_changed` events.
20. [pending] `static/styles.css` — minimal layout close to original Heardle's visual grammar (stacked guess rows, progress bar).

## Phase 6: End-to-end verification
21. [pending] Run full `pytest` suite.
22. [pending] Manual play-through with a Premium account; verify clip timings via browser console deltas.
23. [pending] Write test report in `tests/reports/YYYY-MM-DD-initial-verification.md` per user global testing standards.
