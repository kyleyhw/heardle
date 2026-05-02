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

## Phase 6: End-to-end verification (Spotify path — deferred)
21. [completed] SETUP.md walkthrough and docs/deployment.md (Spotify variant).
22. [completed] Part A: automated checks pass (94 unit tests, 3 skipped integration). Report at `tests/reports/2026-04-19-phase6-initial-verification.md`.
23. [pending] Part B: superseded by Phase 7 iTunes pivot. Spotify live-verification no longer on the critical path — the Spotify backend remains architecturally present but unwired, pending a future toggle.

## Phase 7: iTunes pivot (for zero-setup "clone and play")
24. [completed] `src/heardle/itunes.py` — async wrapper over iTunes Search API (artist / year / search / single-track), with dedup of album reissues and client-side year filtering. 17 respx-mocked unit tests.
25. [completed] `Track.preview_url: str | None` added (iTunes populates it; Spotify leaves `None`).
26. [completed] `api.py` rewritten: removed OAuth routes, added `GameSession` dataclass, new routes `/game/new` (artist/year/search), `/game/{id}/preview`, updated `/autocomplete` to proxy iTunes + union correct-pool substring matches.
27. [completed] `config.py` reworked: all fields optional with defaults; `AUDIO_BACKEND` env var (default `"itunes"`, `"spotify"` reserved for future); session / game secrets fall back to ephemeral random.
28. [completed] Templates updated: base.html dropped Spotify SDK; index.html new source selector; partials/game_body.html embeds `<audio>`; hidden input renamed to `guess_track_id`.
29. [completed] `static/player.js` rewritten as an HTML5 `<audio>` player. Clip cutoff anchored on `audio.play()` promise resolution (±20 ms accuracy).
30. [completed] `docs/` rewritten: SETUP.md shrunk to ~30 lines, docs/audio_pipeline.md rewritten for iTunes, docs/deployment.md simplified (no per-user auth), docs/architecture.md updated.

## Phase 8: Deezer pivot (default backend swap)
31. [completed] `src/heardle/deezer.py` — async wrapper over the Deezer Public API (`/search`, `/search/artist`, `/artist/{id}/top`, `/track/{id}`). Drops empty-preview rows at parse time. Year-source path bulk-hydrates `/track/{id}` (concurrent, semaphore-bounded) to recover `release_date`, since Deezer's bulk envelopes omit it. 24 respx-mocked unit tests.
32. [completed] `Track.release_year: int | None` — relaxed to optional, since Deezer's `/search` and `/artist/{id}/top` envelopes do not include `release_date`. Templates and the autocomplete client gracefully omit the year when `None`.
33. [completed] `config.py` — `AUDIO_BACKEND` default flipped from `"itunes"` to `"deezer"`. Both backends share the same correct-pool / autocomplete contract; `api.py` now dispatches via a single `_build_correct_pool(backend, …)` helper.
34. [completed] `templates/index.html` copy updated to refer to Deezer; `partials/game_body.html` and `static/autocomplete.js` made year-optional.
35. [completed] Docs rewritten: README, SETUP.md, docs/architecture.md, docs/audio_pipeline.md (with backend-comparison table and endpoint mapping). All 144 unit tests pass; ruff and ty clean.
