# OAuth flow

## Scopes

| Scope | Purpose |
|---|---|
| `streaming` | Required by the Web Playback SDK to emit audio. |
| `user-read-email` | Identify the logged-in user in logs / UI. |
| `user-read-private` | Expose `product` field from `/v1/me`, used to gate Premium access. |
| `playlist-read-private` | Allow using the user's own private playlists as the source pool. |

## Sequence

```
Browser                          FastAPI                          Spotify
   │                                │                                │
   │ GET /auth/login                │                                │
   ├───────────────────────────────▶│                                │
   │                                │ 302 → /authorize?state=SIGNED  │
   │◀───────────────────────────────┤                                │
   │ GET /authorize (user consent)  │                                │
   ├────────────────────────────────┴───────────────────────────────▶│
   │ 302 → /callback?code=…&state=…                                  │
   │◀────────────────────────────────────────────────────────────────┤
   │ GET /callback?code=…           │                                │
   ├───────────────────────────────▶│ POST /api/token (code → token) │
   │                                ├───────────────────────────────▶│
   │                                │  {access_token, refresh_token} │
   │                                │◀───────────────────────────────┤
   │                                │ GET /v1/me (assert premium)    │
   │                                ├───────────────────────────────▶│
   │                                │  {product: "premium"}          │
   │                                │◀───────────────────────────────┤
   │  set signed session cookie     │                                │
   │◀───────────────────────────────┤                                │
```

## Token refresh

Tokens expire in 3600 s. On a 401 from any Spotify call in `spotify.py`, the handler:

1. Exchanges the stored `refresh_token` at `POST /api/token` with `grant_type=refresh_token`.
2. Updates the session-cookie payload with the new `access_token` (and the new `refresh_token`, if Spotify rotates it — they sometimes do).
3. Retries the original request exactly once.

If the refresh itself 4xx's, the session is invalidated and the user is redirected to `/auth/login` with a one-time flash message.

## Premium verification

Spotify silently rejects `PUT /me/player/play` for free-tier users — the SDK device appears but playback does not start. To give a clean error up-front we check `GET /v1/me` → `product == "premium"` on every login and every new game; users without Premium see a page explaining the limitation and linking to [Spotify's Premium page](https://www.spotify.com/premium).

## Why Authorization Code, not PKCE

We are server-side (Python backend with access to the client secret), so the classic Authorization Code flow is both simpler and strictly more secure than the PKCE variant. PKCE is only needed when the client secret cannot be kept secret (SPAs, mobile apps).

## State parameter

The `state` query parameter is signed with `GAME_STATE_SECRET` (HMAC-SHA256) and includes a random nonce plus a timestamp. On callback we verify the HMAC and reject states older than 10 minutes. This prevents CSRF against the callback endpoint.
