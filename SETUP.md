# Setup

The default Deezer-backed mode needs **nothing** beyond a Python 3.12+
toolchain. Clone, sync, run.

```bash
git clone https://github.com/kyleyhw/heardle.git
cd heardle
uv sync                  # install dependencies
uv run uvicorn heardle.api:app --reload
```

Navigate to **http://127.0.0.1:8000**, pick a source (artist / year /
search), and play. No accounts, no API keys, no Premium.

## Optional

Each of these is independent. You can ignore all of them for personal local
play.

### Pre-commit hooks

```bash
uv run pre-commit install
```

Runs `ruff`, `ruff-format`, `detect-secrets`, and `ty` on every commit.

### Pin your session secret

Out of the box the session cookie is signed with an ephemeral random token
that changes on every server restart — so in-progress games reset if you
bounce the server. To pin the secret:

```bash
cp .env.example .env
uv run python -c "import secrets; print(secrets.token_urlsafe(64))"
# paste the output into SESSION_SECRET= in .env
```

### Switch to the iTunes backend

Deezer's preview window is selected by their backend (often the chorus).
iTunes is similar but draws from a US-biased catalogue and may pick a
different window for the same track. To compare:

```bash
echo "AUDIO_BACKEND=itunes" >> .env
```

Both backends are zero-auth and share the same UI contract.

### Offline autocomplete corpus (future enhancement)

The current autocomplete hits the active backend live on each keystroke
(debounced client-side to 300 ms). If you want a pre-indexed offline corpus
instead, see [docs/corpus_threshold.md](docs/corpus_threshold.md) — the
Kaggle pipeline from Phase 4 is kept in the tree and can be wired back in
with one config flag when desired.

### Spotify backend (future toggle)

Playing from Spotify's Web Playback SDK (full-track playback, intro-from-t=0
fidelity) is deliberately disabled in the current build — the February
2026 Web API changes removed endpoints we depended on (`/artists/{id}/top-tracks`,
certain playlist fields), and re-enabling requires a rework of
`heardle.spotify`. Setting `AUDIO_BACKEND=spotify` in `.env` currently
surfaces a 503 on game creation so the disabled branch is visible rather
than silently broken. See [docs/oauth_flow.md](docs/oauth_flow.md) for the
OAuth plumbing that's already in place for when the backend returns.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Server starts, `/` 200s, but `/game/new` 400s "zero playable tracks" | The backend returned no preview-bearing rows for that artist / year | Try a less obscure artist; many genuinely obscure tracks lack a playable preview on either backend |
| Autocomplete doesn't surface the target | The backend's global relevance doesn't rank a deep cut in the top 10 | The server falls back to substring match against the game's correct pool, so the target should still appear when you type part of the title — try a different substring |
| Clip sounds like the chorus, not the intro | Deezer (and iTunes) pick the 30 s window; not always the intro | Expected. See [docs/audio_pipeline.md](docs/audio_pipeline.md) for the tradeoff |
| Audio hangs after one round | htmx swap left stale audio | Reload the page; this shouldn't happen after Phase 7c's beforeSwap pause — if it does, file an issue |

## Next steps

- [docs/architecture.md](docs/architecture.md) — request flow and module responsibilities
- [docs/audio_pipeline.md](docs/audio_pipeline.md) — iTunes preview pipeline, clip-cutoff timing
- [docs/deployment.md](docs/deployment.md) — optional public-hosted mode
