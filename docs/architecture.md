# Architecture

## Component overview

```
Browser (Jinja + htmx + Web Playback SDK)
        │
        │  HTTPS / htmx over WSS for SDK signalling
        ▼
FastAPI backend ──▶ Spotify Web API (track metadata, OAuth)
        │
        ├──▶ popular_corpus.parquet  (loaded once at startup into RAM)
        │
        └──▶ Starlette SessionMiddleware (server-signed cookies, itsdangerous)
```

## Module responsibilities

| Module | Responsibility | Key external calls |
|---|---|---|
| `config.py` | Typed env configuration via pydantic-settings | reads `.env` |
| `auth.py` | OAuth Authorization Code flow, token refresh, Premium verification | Spotify `/authorize`, `/api/token`, `/v1/me` |
| `spotify.py` | Async wrapper for track/playlist/artist/year metadata | Spotify `/v1/playlists/*`, `/v1/artists/*/top-tracks`, `/v1/search` |
| `corpus.py` | Load popular-songs parquet, build autocomplete index, expose $\mathcal{G} = \mathcal{P} \cup \mathcal{C}$ | pandas, rapidfuzz |
| `game.py` | Pure game logic: $d_i$ schedule, scoring, fuzzy guess matching, state transitions | rapidfuzz only |
| `api.py` | FastAPI routes + htmx partials; wires auth, spotify, corpus, game | all of the above |

The game logic in `game.py` is intentionally pure — no IO, no Spotify calls. This makes it directly unit-testable without mocks, and keeps the $d_i$ schedule / scoring function easy to verify mathematically.

## Request flow for a single game

1. `GET /` — source selector (artist / year / playlist).
2. `GET /auth/login` — redirect to Spotify `/authorize` with scopes `streaming user-read-email user-read-private playlist-read-private`.
3. `GET /callback?code=...` — exchange code for tokens, verify `product == "premium"`, store token in signed session cookie.
4. `POST /game/new` with a source spec — server builds $\mathcal{C}$, samples target $t$, returns an HMAC-signed `game_id` (target id never leaked plaintext to the browser).
5. Browser loads the SDK, receives the `device_id`, issues `PUT /me/player/play` with `{uris: ["spotify:track:..."], position_ms: 0}` to the Spotify API directly (the browser holds the access token).
6. JS subscribes to `player_state_changed`; on first event with `position > 0 && !paused` records $t_\text{play start}$ via `performance.now()`, then schedules `player.pause()` at $t_\text{play start} + d_i \cdot 1000$ ms.
7. `POST /game/{id}/guess` returns an htmx partial with the next row rendered and the new clip length.

## Why FastAPI + htmx (not an SPA)

- The game state transitions are discrete and server-authoritative. Every guess is one round-trip. htmx's "swap a partial" model maps directly to this.
- A full SPA framework would duplicate state (client-side round/score mirror) without gaining interactivity we need — the only continuously-interactive component is the audio player itself, which runs inside Spotify's SDK iframe regardless of framework.
- Keeps the stack Python-first, consistent with the rest of the user's global preferences.
