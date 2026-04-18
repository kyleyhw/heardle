# Deployment

The local-run setup in [SETUP.md](../SETUP.md) is all you need to play alone. This document covers the optional "public-hosted" mode — deploying to a platform-as-a-service so friends can play via a URL without cloning anything.

## When to bother

Only if you want others to play without each of them doing the Spotify Developer dance. A hosted instance gives you the closest thing to the original `heardle.app` UX: one URL, every player logs in with Spotify and plays. Tradeoffs below.

## What changes vs. local

The same code runs in both modes. Only three things differ:

1. **`SPOTIFY_REDIRECT_URI`** in `.env` (and in the Spotify Developer Dashboard) moves from `http://127.0.0.1:8000/callback` to `https://your-domain.example/callback`.
2. **`SESSION_SECRET`** and **`GAME_STATE_SECRET`** should be regenerated for the deployed env and injected via the host's secret-management UI rather than committed into `.env`.
3. **Corpus parquet** needs to be built and uploaded to the deployed filesystem (or to object storage if the host has ephemeral disk) before server start. Most PaaS providers support this through a persistent-volume mount or a startup hook.

## Constraints to know before you deploy

### Premium is still required per-player

Spotify's Web Playback SDK refuses to emit audio for non-Premium accounts. Every user must have their own Premium subscription. The server-side `assert_premium` check in `heardle.auth` surfaces this as a clean 403 at login time, but the requirement itself is Spotify's, not ours.

### The 25-user cap

Spotify apps in **Development Mode** are capped at 25 distinct users — each new person who logs in consumes a slot, and once full, subsequent users see "app is in development mode". To lift this, apply for **Extended Quota Mode** through the Developer Dashboard: Spotify manually reviews, wants a real privacy policy and app legitimacy evidence, and the turnaround is on the order of weeks. For a small group of friends, 25 is usually enough.

### Your client secret moves with the app

The `SPOTIFY_CLIENT_SECRET` is a per-app secret that must live on the server. Do not commit it. Most hosts provide a secrets UI — use it.

## Recommended hosts

Any Python-app PaaS works. Ranked by friction for this specific app:

| Host | Free tier | Pros | Cons |
|---|---|---|---|
| **Fly.io** | ~3 small VMs, small volumes | Persistent volume for the parquet; TLS + domain handled; simple `fly launch` + `fly deploy` loop | First-time `flyctl` install |
| **Railway** | $5 free credit/month | Dead simple, nice UI | Volume mounts are a little fiddlier than Fly |
| **Render** | Free web service (sleeps after 15 min idle) | Extremely simple | Sleep-on-idle kills active sessions; ephemeral disk so the parquet must rebuild on each boot |

If you expect steady use and want a persistent disk without billing surprises, **Fly.io** is the recommendation.

## Fly.io recipe (minimal)

```bash
brew install flyctl                                 # or https://fly.io/docs/hands-on/install-flyctl/
fly auth signup                                     # or fly auth login
fly launch --no-deploy                              # generates fly.toml; pick a region near you
fly volumes create heardle_data --size 1 --region <region>   # ~1 GB persistent disk for the parquet

# Upload the parquet (one way — there are many)
fly ssh console -C "mkdir -p /data"
fly ssh sftp shell
  > put data/popular_corpus.parquet /data/popular_corpus.parquet
  > exit

# Set secrets
fly secrets set \
    SPOTIFY_CLIENT_ID=<your id> \
    SPOTIFY_CLIENT_SECRET=<your secret> \
    SPOTIFY_REDIRECT_URI=https://<your-app>.fly.dev/callback \
    SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(64))") \
    GAME_STATE_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(64))") \
    POPULAR_CORPUS_PATH=/data/popular_corpus.parquet

fly deploy
```

Then add the deployed redirect URI to the Spotify Developer Dashboard (`https://<your-app>.fly.dev/callback`) alongside the local one.

### Example `fly.toml` additions

Mount the volume so the parquet persists across restarts:

```toml
[[mounts]]
  source = "heardle_data"
  destination = "/data"
```

And make sure the `app` command runs uvicorn:

```toml
[processes]
  app = "uvicorn heardle.api:app --host 0.0.0.0 --port 8080"
```

## Observability

For a hobby deployment, Fly's built-in log tail (`fly logs`) is sufficient. Structured logging would be the next step if you start seeing real usage — the codebase uses the stdlib `logging` module, so pointing it at a JSON formatter plus `fly logs | jq` gets you structured queries for free.
