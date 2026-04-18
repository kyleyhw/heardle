/*
 * Spotify Web Playback SDK integration.
 *
 * Lifecycle
 * ---------
 * 1. Spotify's SDK script calls window.onSpotifyWebPlaybackSDKReady once it
 *    has loaded. We register our Player instance then.
 * 2. Player is created with a getOAuthToken callback that fetches the current
 *    access token from /api/token (server-side session).
 * 3. On the first `ready` event we have a device_id; we PUT /v1/me/player to
 *    transfer playback to our device (non-autoplay).
 * 4. When the user clicks #play-button, we PUT /v1/me/player/play with the
 *    target uri from the DOM (server-side injected) and position_ms=0. On the
 *    first `player_state_changed` event with position > 0, we record the
 *    actual play-start timestamp from performance.now() and schedule a
 *    player.pause() at t_play_start + clip_length_seconds * 1000.
 *
 * Clip-cutoff precision
 * ---------------------
 * SDK play has a ~50-100 ms latency between the API call returning and audio
 * actually starting. Anchoring the pause-timer to the first non-zero
 * player-state callback collapses that source of drift, leaving only
 * setTimeout jitter (~20 ms on a focused tab). Good enough for sub-second
 * clips on a 1-second Heardle round-zero.
 */

(function () {
    "use strict";

    let player = null;
    let deviceId = null;
    let clipPauseHandle = null;

    window.onSpotifyWebPlaybackSDKReady = async () => {
        const Spotify = window.Spotify;
        if (!Spotify) return;

        player = new Spotify.Player({
            name: "Heardle (local)",
            getOAuthToken: async (callback) => {
                const response = await fetch("/api/token");
                if (!response.ok) {
                    console.error("Could not fetch token; not logged in?");
                    return;
                }
                const body = await response.json();
                callback(body.access_token);
            },
            volume: 0.8,
        });

        player.addListener("ready", async ({ device_id }) => {
            deviceId = device_id;
            await transferPlaybackHere();
        });

        player.addListener("not_ready", ({ device_id }) => {
            console.warn("Spotify device went offline:", device_id);
        });

        player.addListener("initialization_error", ({ message }) => console.error(message));
        player.addListener("authentication_error", ({ message }) => console.error(message));
        player.addListener("account_error", ({ message }) => {
            // Emitted when the account is not Premium. Our server-side
            // assert_premium should prevent this, but log anyway.
            console.error("Spotify account error (Premium required?):", message);
        });
        player.addListener("playback_error", ({ message }) => console.error(message));

        await player.connect();
    };

    async function transferPlaybackHere() {
        const response = await fetch("/api/token");
        if (!response.ok) return;
        const { access_token } = await response.json();
        // play=false so Spotify does not immediately start whatever the user
        // was listening to. We explicitly start playback per-round.
        await fetch("https://api.spotify.com/v1/me/player", {
            method: "PUT",
            headers: {
                "Authorization": `Bearer ${access_token}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ device_ids: [deviceId], play: false }),
        });
    }

    function getGameBody() {
        return document.getElementById("game-body");
    }

    async function playClip(clipLengthSeconds, gameId) {
        if (!player || !deviceId) {
            console.warn("Player not ready yet.");
            return;
        }
        if (clipPauseHandle) clearTimeout(clipPauseHandle);

        // Ask the server to start playback. The target track URI lives only
        // server-side so the player cannot cheat by reading the DOM.
        const form = new FormData();
        form.append("device_id", deviceId);
        const response = await fetch(`/game/${gameId}/play`, {
            method: "POST",
            body: form,
        });
        if (!response.ok) {
            console.error("server refused play request:", response.status);
            return;
        }

        const onStateChanged = (state) => {
            if (!state) return;
            if (state.position > 0 && !state.paused) {
                // Unsubscribe so we only react to the first start-of-playback.
                player.removeListener("player_state_changed", onStateChanged);
                const tPlayStart = performance.now();
                const targetMs = tPlayStart + clipLengthSeconds * 1000;
                clipPauseHandle = setTimeout(() => {
                    player.pause();
                }, Math.max(0, targetMs - performance.now()));
            }
        };
        player.addListener("player_state_changed", onStateChanged);
    }

    function initPlayButton() {
        const button = document.getElementById("play-button");
        const body = getGameBody();
        const section = document.querySelector(".game");
        if (!button || !body || !section) return;
        const clipLength = parseInt(body.dataset.clipLength, 10);
        const gameId = section.dataset.gameId;
        if (!gameId) return;

        button.addEventListener("click", () => {
            playClip(clipLength, gameId);
        });
    }

    document.addEventListener("DOMContentLoaded", initPlayButton);
    document.body.addEventListener("htmx:afterSwap", initPlayButton);
})();
