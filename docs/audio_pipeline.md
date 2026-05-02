# Audio pipeline

## Current backend: Deezer Public API (default)

The default mode streams 30-second preview MP3s from Deezer's public CDN.
Zero auth, zero configuration, larger global catalogue than iTunes
(~120 M tracks, broad regional coverage), and a more generous rate limit.

```
Browser                                 Server                          Deezer
   │                                      │                               │
   │  user clicks Play                    │                               │
   │  GET /game/{id}/preview              │                               │
   ├─────────────────────────────────────▶│                               │
   │                                      │                               │
   │  {preview_url: "https://..."}        │                               │
   │◀─────────────────────────────────────┤                               │
   │                                      │                               │
   │  audio.src = preview_url             │                               │
   │  audio.play()  (returns promise)     │                               │
   │                                      │                               │
   │  GET preview_url (audio element)     │                               │
   ├──────────────────────────────────────┴──────────────────────────────▶│
   │  MP3 bytes (cdnt-preview.dzcdn.net)                                  │
   │◀─────────────────────────────────────────────────────────────────────┤
   │  play() promise resolves             │                               │
   │  setTimeout(audio.pause, d_i*1000)   │                               │
```

The preview URL itself is fetched from the server lazily (first click of
Play) rather than rendered into the page HTML — a curious player cannot
simply view-source to hear the clip early. The URL is still visible in the
browser's network tab after the first play, which is acceptable for a
personal hobby game; a determined cheater is not the threat model.

### Why Deezer over iTunes for the default

| Property | iTunes | Deezer |
|---|---|---|
| Preview length | 30 s | 30 s |
| Preview format | M4A (AAC) | MP3 |
| Preview start offset | Variable (often ~30 s into track) | Variable (often near the chorus) |
| Auth required | None | None (read endpoints) |
| Documented rate limit | none stated; ~20/min observed | 50 requests / 5 s per IP |
| Catalogue size | Smaller, US-biased | Larger, global |
| CORS | Permissive | Permissive |

For Heardle's gameplay model — many short search calls (autocomplete) plus
one preview fetch per round — Deezer's 50/5s limit is comfortable headroom
where iTunes' 20/min ceiling can be hit during fast typing.

### Endpoint mapping (Deezer)

| Source type | Endpoints called |
|---|---|
| `artist` | `GET /search/artist?q=…&limit=1` → `GET /artist/{id}/top?limit=50` |
| `search` | `GET /search?q=…&limit=50` |
| `year`   | `GET /search?q={year}&limit=300` → `GET /track/{id}` per row (concurrent, semaphore=8) |
| Autocomplete keystroke | `GET /search?q=…&limit=10` |
| Preview-URL release | served from cached `Track.preview_url` (no extra Deezer hit) |

The year-source path is a two-stage hydrate because Deezer's `/search` and
`/artist/{id}/top` envelopes do not include `release_date` — only
`album.id`. The `date:` qualifier in Deezer's search syntax is fuzzy
free-text (a 2020 remix of a 1990 track matches `date:"2020"`), so we cannot
substitute a server-side filter. The bulk-fetch is bounded by an
`asyncio.Semaphore(8)` to stay under the 50/5s rate limit even when the
autocomplete is also active.

### Empty-preview filtering

A non-trivial fraction of Deezer tracks (rights-restricted, regional
licensing, very new releases) return `"preview": ""` — empty string, not
null. Our parser drops these rows at `_try_parse_track` time, so the
correct-answer pool $\mathcal{C}$ exposed to the game is already restricted
to playable tracks. The uniform target sample
$t \sim \mathrm{Uniform}(\mathcal{C})$ therefore can never select an
unwinnable target.

## Why not Spotify?

Full-track offset-addressable playback (i.e. "the first $d_i$ seconds of the
track starting at t = 0") requires the Spotify Web Playback SDK, which in
turn requires:

1. A Spotify Developer app (3-minute dashboard registration).
2. The player's Spotify account to be Premium.
3. An OAuth Authorization Code flow in the browser.

As of the February 2026 Spotify Web API update, new Development-Mode apps
are also capped at 5 authorised users and are restricted to a smaller
endpoint set — `GET /artists/{id}/top-tracks` was removed outright,
`GET /playlists/{id}/tracks` renamed to `/items` with a response-shape
change, and `GET /search` capped at 10 results per page.

The Deezer default trades the "first second of the real intro" feel for
zero-setup universal access. Deezer chooses the 30-second window per track
and tends to favour the chorus or hook over the intro, so clips often start
mid-song. iTunes has the identical issue. Only the deferred Spotify path
gives canonical $T[0, d_i]$ intro reveal.

The `AUDIO_BACKEND` env var keeps the toggle path open: setting it to
`spotify` routes game creation through a branch that currently returns 503
but is architecturally plumbed. Setting it to `itunes` routes through the
fully-wired Apple backend if you prefer M4A previews or US-storefront
ranking.

## Clip-cutoff precision

Two error sources, combined bounding the measured clip length:

1. **Browser-side audio startup latency** — the time between `audio.play()`
   being called and the audio subsystem actually emitting sound. On
   desktop-class browsers with a preloaded `<audio>` element this is
   typically 10–30 ms; on mobile Safari it can reach 100+ ms for the first
   clip of a session.

2. **`setTimeout` jitter** — the browser's event loop may fire the pause
   callback 0 to ~50 ms late, especially under tab throttling.

Our mitigation anchors the pause-schedule on `await audio.play()`, whose
promise resolves *after* the audio subsystem has begun playback. This
collapses source (1) to its residual (the time between resolution and the
first audible sample, usually < 5 ms) and leaves only source (2) as
significant. Net expected error on the 1 s round-zero clip: ±20 ms, well
below the psychoacoustic duration-discrimination threshold.

## Known failure modes

- **Autoplay policy.** Browsers require a user gesture before letting a page
  play audio. The Play button itself is the gesture; subsequent rounds
  inherit the unlocked audio context.
- **Preview-less tracks.** A non-trivial fraction of Deezer entries have an
  empty `preview` field (regional licensing). Our loader drops these at
  parse time; they never reach the target-picker. iTunes has the analogous
  problem with missing `previewUrl`.
- **Region blocks on the CDN.** A preview URL fetched from `cdnt-preview.dzcdn.net`
  may return HTTP 403 for clients in countries where the rights-holder has
  not licensed that track. The empty-preview filter catches the most common
  case (Deezer simply returns `""` for the field), but a small residual of
  tracks may still 403 at playback time. Reload to draw a different target.
- **Tab throttling.** Backgrounded tabs throttle `setTimeout` to once per
  second. An explicit pause still fires when the tab is refocused, but the
  clip length is not enforceable while the tab is hidden.
