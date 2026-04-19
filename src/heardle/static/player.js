/*
 * HTML5 <audio> based clip player.
 *
 * Lifecycle
 * ---------
 * 1. Each round's partial renders a single <audio id="heardle-audio"> whose
 *    src starts empty.
 * 2. On the player's first click of the Play button we fetch the target's
 *    preview URL from /game/{id}/preview and assign it to audio.src.
 * 3. audio.play() returns a Promise that resolves when the browser's audio
 *    subsystem has actually begun playback. We use that resolution rather
 *    than the call-site timestamp as the anchor for the pause timer, so
 *    small autoplay / decode latencies don't leak into the measured clip
 *    length.
 * 4. setTimeout schedules audio.pause() at clip_length_seconds * 1000 ms
 *    from the play-start anchor.
 *
 * The DOM is rebuilt on every htmx:afterSwap because the round partial
 * itself is the swapped node; we re-wire the button on each swap.
 */

(function () {
    "use strict";

    const PREVIEW_CACHE = new Map();  // game_id -> preview_url
    let clipPauseHandle = null;
    let currentAudio = null;

    async function fetchPreviewUrl(gameId) {
        if (PREVIEW_CACHE.has(gameId)) return PREVIEW_CACHE.get(gameId);
        const response = await fetch(`/game/${gameId}/preview`);
        if (!response.ok) {
            console.error("preview request failed:", response.status);
            return null;
        }
        const body = await response.json();
        PREVIEW_CACHE.set(gameId, body.preview_url);
        return body.preview_url;
    }

    async function playClip(gameId, clipLengthSeconds) {
        if (clipPauseHandle) clearTimeout(clipPauseHandle);
        const audio = document.getElementById("heardle-audio");
        if (!audio) return;
        currentAudio = audio;

        const previewUrl = await fetchPreviewUrl(gameId);
        if (!previewUrl) return;

        // Always restart from the start of the preview — the clip is the
        // first d_i seconds of Apple's preview window. Using .currentTime = 0
        // before play() also handles the case where the user clicks play
        // twice before the pause timer fires.
        audio.src = previewUrl;
        audio.currentTime = 0;

        try {
            await audio.play();  // resolves after playback has actually started
        } catch (err) {
            console.error("audio.play() rejected:", err);
            return;
        }
        const tPlayStart = performance.now();
        const targetMs = tPlayStart + clipLengthSeconds * 1000;
        clipPauseHandle = setTimeout(() => {
            audio.pause();
        }, Math.max(0, targetMs - performance.now()));
    }

    function initPlayButton() {
        const body = document.getElementById("game-body");
        const button = document.getElementById("play-button");
        if (!body || !button) return;
        const gameId = body.dataset.gameId;
        const clipLength = parseInt(body.dataset.clipLength, 10);
        if (!gameId || !clipLength) return;

        button.addEventListener("click", () => {
            playClip(gameId, clipLength);
        });
    }

    // Pause any still-running clip when the DOM node for the audio element
    // is about to be replaced by an htmx swap. Otherwise the old audio can
    // keep playing in the background after the round has advanced.
    document.body.addEventListener("htmx:beforeSwap", () => {
        if (clipPauseHandle) clearTimeout(clipPauseHandle);
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
        }
    });

    document.addEventListener("DOMContentLoaded", initPlayButton);
    document.body.addEventListener("htmx:afterSwap", initPlayButton);
})();
