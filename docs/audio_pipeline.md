# Audio pipeline

## Why the Web Playback SDK

See the top-level [architectural plan](../README.md) and `C:\Users\Kyle\.claude\plans\dazzling-launching-crab.md` for the full rationale. In summary:

- Spotify `preview_url` was deprecated in November 2024 and returns `null` for nearly all tracks in newly-registered apps, so we cannot use free 30 s previews from the Web API.
- iTunes / Apple Music previews exist and are auth-free, but Apple picks the 30 s window — often the chorus, not the intro. This breaks Heardle's "guess from the first second of the intro" contract.
- The Web Playback SDK plays the real track starting at any chosen offset, so $T[0, d_i]$ is exactly the first $d_i$ seconds. The cost is the Premium requirement plus OAuth complexity, both of which are acceptable for a self-hosted single-player instance.

## Playback sequence

```
Browser                                Spotify Web API                Spotify SDK (browser)
   │                                        │                              │
   │ load https://sdk.scdn.co/spotify-player.js                             │
   ├────────────────────────────────────────┼──────────────────────────────▶
   │                                        │  Player instance created     │
   │                                        │                              │
   │ player.connect()                       │                              │
   │◀───────────────────────────────────────┼──────────────────────────────┤
   │ ready event → {device_id: DID}         │                              │
   │                                        │                              │
   │ PUT /v1/me/player  {device_ids: [DID], play: false}                   │
   ├───────────────────────────────────────▶│                              │
   │                                        │                              │
   │ User clicks "Start round" (user gesture unlocks audio)                │
   │ PUT /v1/me/player/play?device_id=DID                                  │
   │   body: {uris:["spotify:track:TID"], position_ms: 0}                  │
   ├───────────────────────────────────────▶│                              │
   │                                        │  SDK begins playback         │
   │ player_state_changed event             │                              │
   │◀───────────────────────────────────────┼──────────────────────────────┤
   │ record t_play_start = performance.now()                               │
   │ setTimeout(() => player.pause(), d_i*1000)                            │
```

## Clip-cutoff precision

The naïve implementation `setTimeout(player.pause, d_i * 1000)` scheduled from the moment `play()` is *called* has two error sources:

1. **SDK play-latency** — the ~50–100 ms between `play()` returning and audio actually starting.
2. **`setTimeout` jitter** — the browser's event loop may fire 0 to ~50 ms late, especially under tab-throttling.

Combined, the audible clip could be anywhere from $d_i - 0$ to $d_i + 150$ ms long — acceptable for $d_i = 16$ s, borderline for $d_i = 1$ s (15% relative error).

Mitigation: anchor the pause to the actual audio-start moment. Subscribe to `player.addListener('player_state_changed', cb)`; on the first callback where `state.position > 0 && !state.paused`, record $t_\text{play start}$ from `performance.now()` and schedule

```javascript
const targetMs = tPlayStart + d_i * 1000;
const delay = targetMs - performance.now();
setTimeout(() => player.pause(), delay);
```

This collapses source (1) to ~0 ms residual and keeps source (2) bounded at ~20 ms on a focused tab. The remaining jitter is well inside human perception thresholds for audio duration (~50 ms at short durations).

## Known failure modes

- **Autoplay policy.** Most browsers block programmatic audio until a user gesture. We wrap the first `play()` in a click handler on an explicit "Start" button. Subsequent rounds reuse the already-unlocked context.
- **Device transfer race.** The SDK `ready` event can fire before `PUT /v1/me/player` has been honoured. We wait for `ready` *and* a successful 2xx on the device-transfer call before enabling the "Start" button.
- **Track unavailable in user's market.** Some tracks 404 on `PUT /me/player/play`. We surface this as "skip round" with an explanatory toast.
- **Tab throttling.** Backgrounded tabs throttle `setTimeout` to once per second. We detect `document.visibilityState === 'hidden'` during a round and warn the player.
