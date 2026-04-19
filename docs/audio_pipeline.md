# Audio pipeline

## Current backend: iTunes Search API

The default mode streams 30-second preview MP4s from Apple. Zero auth, zero
configuration, near-universal coverage of the commercial music catalogue.

```
Browser                                 Server                          iTunes
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
   │  MP4/AAC bytes                                                       │
   │◀─────────────────────────────────────────────────────────────────────┤
   │  play() promise resolves             │                               │
   │  record t_play_start = performance.now()                             │
   │  setTimeout(audio.pause, d_i*1000)   │                               │
```

The preview URL itself is fetched from the server lazily (first click of
Play) rather than rendered into the page HTML — a curious player cannot
simply view-source to hear the clip early. The URL is still visible in the
browser's network tab after the first play, which is acceptable for a
personal hobby game; a determined cheater is not the threat model.

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
change, and `GET /search` capped at 10 results per page. See the
[Spotify blog post](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)
and the [migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).

The iTunes pivot trades the "first second of the real intro" feel for
zero-setup universal access. Apple chooses the 30-second window per track,
so clips sometimes start mid-chorus instead of mid-intro. For most tracks
the difference is small enough that the game still feels like Heardle.

The `AUDIO_BACKEND` env var keeps the toggle path open: setting it to
`spotify` routes game creation through a branch that currently returns 503
but is architecturally plumbed. When Spotify's new API stabilises (or we
rework `heardle.spotify` against the new endpoint set), re-enabling the
Spotify path is a handful of lines of code plus the corresponding SDK-based
player-side rewrite in `player.js`.

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
- **Preview-less tracks.** A handful of iTunes catalogue entries have no
  `previewUrl`. Our loader drops these at parse time; they never reach the
  target-picker.
- **Region blocks.** A track available in one country's iTunes storefront
  may return 404 on preview fetch from another. We expose `ITUNES_COUNTRY`
  in `.env` so the user can pick the storefront the server queries; for
  most English-language catalogue `US` works.
- **Tab throttling.** Backgrounded tabs throttle `setTimeout` to once per
  second. An explicit pause still fires when the tab is refocused, but the
  clip length is not enforceable while the tab is hidden.
