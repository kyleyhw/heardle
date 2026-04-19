# Architecture

## Component overview (iTunes default)

```
Browser (Jinja + htmx + HTML5 <audio>)
        │
        │  HTTPS + htmx swap-in partials
        ▼
FastAPI backend
        │
        │  httpx.AsyncClient
        ▼
iTunes Search / Lookup  (https://itunes.apple.com)
```

Almost the entire state model lives in a single process: a module-level
`_games: dict[str, GameSession]` keyed by a URL-safe random id. Sessions
are ephemeral — restarting the server drops them. This matches the design
target (single-player, local-or-small-host) and removes the need for any
external datastore.

## Module responsibilities

| Module | Responsibility | External calls |
|---|---|---|
| `config.py` | Typed env configuration via a frozen dataclass, all fields default to sensible values | reads `.env` |
| `itunes.py` | Async wrapper for artist / year / search / single-track lookups; parser drops non-song rows and rows without a preview URL | iTunes Search API |
| `spotify.py` | Async wrapper for the (currently-unwired) Spotify backend. Unit-tested, ready for re-enablement; `api.py` does not import it in iTunes mode | Spotify Web API |
| `auth.py` | OAuth Authorization Code flow and state signing for the future Spotify toggle | Spotify OAuth |
| `corpus.py` | Optional pandas-backed offline autocomplete index. Built from a Kaggle snapshot; currently unused by `api.py` | — |
| `game.py` | Pure game logic: `d_i` schedule, scoring, immutable state transitions | — |
| `api.py` | FastAPI routes + htmx partials; orchestrates `itunes` + `game` | — |

The game logic in `game.py` is pure — no IO, no framework imports. This
makes it directly unit-testable without mocks and keeps the `d_i` schedule
/ scoring function easy to verify mathematically.

## Request flow for a single game

1. `GET /` — source-selector form (artist / year / search term).
2. `POST /game/new` with `{source_type, source_value}` — server queries
   iTunes, builds `𝒞`, samples the target uniformly, stores a
   `GameSession`, and 303-redirects to `/game/{id}`.
3. `GET /game/{id}` — renders the full game page. The target's
   `preview_url` is deliberately **not** in the HTML.
4. User clicks Play → `GET /game/{id}/preview` returns the preview URL;
   the browser assigns it to the `<audio>` element, calls `play()`, and
   schedules a pause at `d_i` seconds after the play-start promise
   resolves.
5. `POST /game/{id}/guess` or `/skip` — server applies the transition in
   `heardle.game`, htmx swaps the updated body partial back into the page.
6. During the guess step the user types into an autocomplete input;
   keystrokes fire `GET /autocomplete?q=...&game_id=...` (debounced to
   300 ms). The server merges iTunes' global ranked results with substring
   matches from the game's `correct_pool`, guaranteeing the target is
   always findable by at least one substring of its title.

## Why FastAPI + htmx (not an SPA)

- The game state transitions are discrete and server-authoritative. Every
  guess is a round-trip. htmx's "swap a partial" model maps directly.
- A full SPA framework would duplicate state (client-side round/score
  mirror) without gaining interactivity we need — the only
  continuously-interactive component is the audio player itself.
- Keeps the stack Python-first, consistent with the rest of the
  repository's conventions.

## Backend toggle

`AUDIO_BACKEND` env var selects the audio source:

- `itunes` (default) — fully wired, described above.
- `spotify` — architecturally plumbed, currently returns 503 on
  `/game/new`. The Spotify wrapper (`spotify.py`), OAuth plumbing
  (`auth.py`), templates, and SDK-driven player are preserved in the tree
  for future re-enablement. See
  [audio_pipeline.md](audio_pipeline.md#why-not-spotify) for the policy
  and rework rationale.
