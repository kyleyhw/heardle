# Setup

One-time steps to bring a fresh clone to a playable state. Roughly 10 minutes of active work (plus the Kaggle download in the background).

---

## 1. Local environment

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/kyleyhw/heardle.git
cd heardle
uv sync                          # install runtime + dev deps from uv.lock
uv run pre-commit install        # activate ruff + detect-secrets + ty hooks
cp .env.example .env             # create your local env file
```

Open `.env` in your editor — you'll fill in four values across the next two sections.

---

## 2. Spotify Developer app (required)

This is the only unavoidable Spotify-side step. The app gives you the `client_id` and `client_secret` needed for OAuth. Premium is required for playback — see [docs/audio_pipeline.md](docs/audio_pipeline.md) for why.

1. Go to **https://developer.spotify.com/dashboard** and log in with your Spotify account (the one you stream music with, which must be Premium).
2. Accept the Developer Terms of Service if prompted.
3. Click **Create app** and fill the form:
   - **App name**: anything, e.g. `heardle-local`.
   - **App description**: e.g. `Personal Heardle clone, local use only`.
   - **Website**: optional, can leave blank.
   - **Redirect URIs**: add exactly **`http://127.0.0.1:8000/callback`** (must match character-for-character; use `127.0.0.1`, not `localhost`).
   - **Which API/SDKs are you planning to use?**: tick both **Web API** and **Web Playback SDK**.
4. Accept the ToS and **Save**.
5. Open the new app. Copy the **Client ID**. Click **View client secret** and copy the **Client Secret**.

Paste them into `.env`:

```
SPOTIFY_CLIENT_ID=<your client id>
SPOTIFY_CLIENT_SECRET=<your client secret>
```

Now generate two random session secrets:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(64))"
uv run python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Paste the two outputs into `SESSION_SECRET=` and `GAME_STATE_SECRET=` respectively.

---

## 3. Popular-songs corpus (required for autocomplete)

Heardle's guess box is backed by a precomputed snapshot. We default to the 1.2M `rodolfofigueroa/spotify-1.2m-songs` dataset on Kaggle.

### 3a. Download the CSV

1. Sign in (or sign up, free) at **https://www.kaggle.com**.
2. Go to **https://www.kaggle.com/datasets/rodolfofigueroa/spotify-1.2m-songs**.
3. Click **Download** — accept the dataset terms if prompted. You'll get a zip file; extract `tracks_features.csv` (~200 MB).

### 3b. Convert to our canonical parquet

```bash
uv run python -m scripts.load_corpus --input path/to/tracks_features.csv
```

This writes `data/popular_corpus.parquet` (~30 MB, gitignored). The script auto-detects the source schema; the summary at the end should report ~1.2M rows and a release-year range spanning roughly 1920–2020.

### 3c. (Optional) Plot the year distribution

```bash
uv run python -m scripts.plot_corpus_distribution
```

Writes `docs/corpus_distribution.png` showing the release-year histogram with the `YEAR_THRESHOLD=2000` reference line. Inspect this if you want to tune the threshold — see [docs/corpus_threshold.md](docs/corpus_threshold.md) for how.

### Alternative dataset

If you prefer a smaller snapshot that retains Spotify's `popularity` field (for a cleaner filter), use `maharshipandya/spotify-tracks-dataset` instead (~114k rows). The loader auto-detects the schema — same command, different CSV.

---

## 4. Run it

```bash
uv run uvicorn heardle.api:app --reload
```

Navigate to **http://127.0.0.1:8000**. Click **Log in with Spotify**, accept the scopes, and you should land back on the index page with your Spotify display name in the header. Pick a source (artist ID, playlist ID, or year) and **Start game**.

### Finding Spotify IDs

The last URL segment is the ID:

```
https://open.spotify.com/artist/6eUKZXaKkcviH0Ku9w2n3V
                                  └── artist id ─────┘

https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
                                  └──── playlist id ────┘
```

For year, just type the year (e.g. `2020`).

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Spotify login page shows "Invalid redirect URI" | Your dashboard redirect URI does not match `.env` exactly | Both must be `http://127.0.0.1:8000/callback` character-for-character. `localhost` won't work. |
| 403 after login with "Premium" in the error | Your account is free or family child | Upgrade to Premium Individual / Family / Duo / Student |
| Autocomplete returns nothing | Corpus parquet not built | Run the loader script (step 3b) |
| Server starts but 500s on `/` | `.env` not copied or fields missing | `cp .env.example .env` and populate all four required fields |
| Clips play longer than expected | Tab throttled (backgrounded) | Keep the browser tab focused; see [docs/audio_pipeline.md](docs/audio_pipeline.md) |
| `scripts.load_corpus` errors "Could not identify schema" | Downloaded a different dataset | Check the CSV columns against the supported schemas in the script's module docstring |

---

## Next steps

- Hosting it so friends can play without cloning: [docs/deployment.md](docs/deployment.md).
- Tuning the popular-corpus filter: [docs/corpus_threshold.md](docs/corpus_threshold.md).
- Understanding the architecture: [docs/architecture.md](docs/architecture.md).
