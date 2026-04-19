# Deployment

Deploying the iTunes-backed default is easy — no per-user auth, no
per-user Premium, no OAuth redirect URIs to juggle. You host the app,
players visit the URL, everyone plays.

## When to bother

Only if you want friends or strangers to play without cloning the repo.
For personal local play, `uvicorn` on `127.0.0.1:8000` is sufficient.

## What changes vs. local

The same code runs in both modes. Only two things are worth pinning:

1. **`SESSION_SECRET`**: set a stable 64-byte random token in the host's
   environment so sessions survive process restarts.
2. **HTTPS**: any modern PaaS (Fly.io, Railway, Render) terminates TLS for
   you; no code change needed on the server.

That's it. iTunes Search is keyless, so there are no secrets to stash beyond
the session cookie.

## Recommended hosts

| Host | Free tier | Pros | Cons |
|---|---|---|---|
| **Fly.io** | 3 small VMs, persistent volumes | TLS + domain handled; `fly launch` to `fly deploy` in minutes | Requires `flyctl` install |
| **Railway** | $5 credit/month | Dead simple, pretty UI | Credit burns on idle |
| **Render** | Free web service (sleeps at 15 min idle) | Simplest imaginable | Cold-start on wake; ephemeral disk |

For a low-traffic hobby deployment, **Fly.io** is the pick.

## Fly.io recipe (minimal)

```bash
# Install: https://fly.io/docs/hands-on/install-flyctl/
fly auth signup                # or fly auth login

fly launch --no-deploy         # generates fly.toml; pick a region

# Set the one secret that matters
fly secrets set \
    SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"

fly deploy
```

### Example `fly.toml`

```toml
app = "heardle-<your-handle>"
primary_region = "lhr"

[build]
  builder = "paketobuildpacks/builder:base"

[processes]
  app = "uvicorn heardle.api:app --host 0.0.0.0 --port 8080"

[[services]]
  http_checks = []
  internal_port = 8080
  protocol = "tcp"

  [[services.ports]]
    handlers = ["http"]
    port = 80
    force_https = true

  [[services.ports]]
    handlers = ["tls", "http"]
    port = 443
```

## Spotify mode, if / when it returns

If you later flip the backend to Spotify (after the required Phase 2.5
rework), the deployed app will additionally need:

1. A Spotify Developer app with `https://<your-domain>/callback` listed
   under Redirect URIs in the Dashboard.
2. `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in the host's secret
   store.
3. A cap of 5 authorised users (Feb 2026 Development Mode limit). Players
   must each have Premium accounts.

These constraints push Spotify mode toward personal-use-with-a-few-friends
rather than open-to-the-public. The current iTunes default has none of
these issues.

## Observability

For hobby-scale traffic, Fly's built-in log tail (`fly logs`) is plenty.
The codebase uses the stdlib `logging` module, so pointing it at a JSON
formatter plus `fly logs | jq` gives structured queries for free.
