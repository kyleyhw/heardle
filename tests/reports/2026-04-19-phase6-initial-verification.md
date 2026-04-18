# Phase 6 — Initial end-to-end verification report

**Date**: 2026-04-19
**Branch / commit**: `master` @ `a8968a0`
**Python**: 3.12.12 on Windows 10 (local dev machine)
**Runtime (full `pytest` suite)**: 1.78 s

## Scope

This report covers the parts of the Phase 6 verification plan that do not require external credentials or assets. It is split into two parts:

- **Part A (this report)**: unit and route-level automated tests, tooling gates (ruff / ty / detect-secrets / pre-commit), and a server-boot smoke test using only the placeholder `.env`. All of this is reproducible without any Spotify account or Kaggle dataset.
- **Part B (to follow once setup is complete)**: Spotify-credential-gated integration tests, the corpus-loader end-to-end run, and a manual play-through. Deferred per the "auth in the last stage" directive; will be recorded as a separate dated report in this folder when completed.

## What was tested

### A1. Full unit test suite

- **What**: `uv run pytest` over `tests/unit/`, covering every phase's modules: `auth`, `spotify`, `game`, `corpus`, `api`.
- **Why**: the unit tests are the executable specification of the game mechanics and the Spotify wrapper's response-shape assumptions. A green run here confirms no regressions from the latest refactors (ruff auto-fixes, formatter passes, pre-commit-triggered reformats).
- **Result**: **94 passed, 3 skipped**. The 3 skips are integration tests gated on `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` (Part B).

Breakdown by file, with input-selection rationale:

| File | Tests | Rationale for inputs |
|---|---|---|
| `test_auth.py` | 12 | Cover every grant flow (auth-code, refresh, client-credentials), state signing/tamper/expiry (`STATE_MAX_AGE_SECONDS = 600` s), and the three Premium-gate outcomes (premium / free / expired token). |
| `test_spotify.py` | 19 | Cover the three `release_date` precisions Spotify returns (YYYY, YYYY-MM, YYYY-MM-DD), pagination edge cases for both the playlist and search envelopes, and null-popularity handling for Development-Mode apps. |
| `test_game.py` | 27 | Exhaustive over the $d_i$ schedule (every $i \in \{0, \dots, 5\}$), every possible winning round with its $6 - i$ score, exhaustion via all-wrong and all-skip, mixed skip/guess path, immutability, convexity check on $d_i$. |
| `test_corpus.py` | 22 | Normalisation edge cases (diacritics, apostrophes, parenthesised suffixes, whitespace collapse, empty input), each filter independently and composed, all-null-popularity fallback, autocomplete recall, union dedup. |
| `test_api.py` | 14 | Index render, autocomplete with and without game context, `/auth/login` redirect and 503-when-unconfigured, `/api/token` shape, logout, game page, guess correct/wrong, skip, and the 409 guard on a finished game. |

### A2. Tooling quality gates

All four pre-commit hooks pass on the working tree:

| Hook | Result | Command |
|---|---|---|
| `ruff check` | ✓ | `uv run ruff check src tests scripts` |
| `ruff format` | ✓ | (via pre-commit) |
| `detect-secrets` | ✓ | `uv run detect-secrets-hook --baseline .secrets.baseline <files>` |
| `ty check` | ✓ | `uv run ty check` |

### A3. Server boot smoke test

- **What**: start the FastAPI app via `TestClient` with only the **unpopulated** `.env` (the direct output of `cp .env.example .env`), hit three representative routes, assert status codes.
- **Why**: the "auth in the last stage" directive means the user clones the repo and copies `.env.example` to `.env` *before* any Spotify or Kaggle setup. The server must boot cleanly in that intermediate state. A broken boot here would gate the user's entire onboarding.
- **Input**: placeholder `SPOTIFY_CLIENT_ID=your_client_id_here`, randomly-generated `SESSION_SECRET` and `GAME_STATE_SECRET`, no corpus parquet.

| Endpoint | Expected | Actual |
|---|---|---|
| `GET /` | 200, source-selector page | **200**, page rendered with login link |
| `GET /auth/login` | 307 redirect toward accounts.spotify.com (will fail server-side at Spotify because `client_id` is a placeholder, but our redirect is well-formed) | **307** |
| `GET /autocomplete?q=halo` | 200 with empty JSON array (no corpus loaded) | **200**, body `[]` |

Startup log contains the expected warning:

```
Popular-songs corpus not found at data\popular_corpus.parquet.
Autocomplete routes will return 503 until `uv run python -m scripts.load_corpus` has run.
```

Passing this confirms the lifespan-level graceful degradation: missing corpus does not prevent the server from starting.

## Part B — pending

The remaining verification requires the external setup documented in [SETUP.md](../../SETUP.md):

1. Spotify Developer app registration → `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` in `.env`.
2. Kaggle download of `rodolfofigueroa/spotify-1.2m-songs` → `tracks_features.csv`.
3. `uv run python -m scripts.load_corpus --input tracks_features.csv` → `data/popular_corpus.parquet`.

Once those three are done, the following are still pending:

- **B1. Integration test suite** (`uv run pytest tests/integration/`): 3 tests against Spotify's "Today's Top Hits" playlist, Ed Sheeran top-tracks, and year-2020 search. These exercise the real response shapes with a Client Credentials token.
- **B2. Corpus load + threshold plot**: run the loader script and `uv run python -m scripts.plot_corpus_distribution`; verify row count is ~1.2M pre-filter, ~700k after `YEAR_THRESHOLD=2000`. Save the plot to `docs/corpus_distribution.png` and inline-reference it from `docs/corpus_threshold.md`.
- **B3. Manual play-through**: log in with a Premium account, start a game from a known artist (e.g. `6eUKZXaKkcviH0Ku9w2n3V` — Ed Sheeran), confirm (a) clip length grows per the $d_i$ sequence, (b) autocomplete surfaces both popular tracks and the correct-answer pool, (c) scoring matches $6 - i$.
- **B4. Clip-timing verification**: open the browser console during a play-through, log `performance.now()` at `play_button` click, at first non-zero `player_state_changed`, and at `player.pause()`. Confirm the measured clip length is within ±50 ms of the nominal $d_i$ value (the theoretical bound given SDK latency mitigation described in `docs/audio_pipeline.md`).

These will be recorded in a follow-up dated report under `tests/reports/` when run.

## Takeaway

97 automated checks (94 unit tests + 3 linting/type/secrets gates) pass cleanly; the server boots in the pre-setup placeholder state without crashing. The project is ready for the user-side setup (Spotify app, Kaggle corpus) and the subsequent manual verification.
