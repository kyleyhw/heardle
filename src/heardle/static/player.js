/*
 * HTML5 <audio> clip player with skip-extends behaviour.
 *
 * Architecture
 * ------------
 * The <audio> element is rendered in game.html, OUTSIDE the #game-body
 * that htmx swaps. That means:
 *   - On Skip: audio keeps playing, we just reschedule the pause timer
 *     to the new (longer) clip length. The player hears continuous audio
 *     and the reveal extends without restarting from zero.
 *   - On correct/wrong submit (song ends): audio pauses.
 *   - On Next song (round_index resets to 0): audio pauses; the new
 *     song starts fresh when the player clicks Play.
 *   - On End session: audio pauses.
 *
 * We determine which transition just happened by snapshotting the
 * #game-body dataset before/after each htmx swap and comparing.
 *
 * The playback cursor (a thin vertical line overlaid on the progress
 * bar) is driven by requestAnimationFrame while audio.paused is false.
 *
 * Volume persistence
 * ------------------
 * Default volume is 25 % on first load; thereafter the user's choice is
 * kept in localStorage so each htmx swap inherits it rather than
 * resetting.
 */

(function () {
    "use strict";

    const VOLUME_KEY = "heardle:volume";
    const DEFAULT_VOLUME = 25;
    const MAX_CLIP_SECONDS = 16;

    // Cache previewUrl keyed on (game_id, target_id) so a Next-song does not
    // accidentally serve the previous song's preview.
    const PREVIEW_CACHE = new Map();

    let clipPauseHandle = null;
    let playheadRAF = null;
    let preSwapState = null;

    function getAudio() {
        return document.getElementById("heardle-audio");
    }

    function snapshotState() {
        const body = document.getElementById("game-body");
        if (!body) return null;
        return {
            gameId: body.dataset.gameId,
            roundIndex: parseInt(body.dataset.roundIndex, 10),
            finished: body.dataset.finished === "true",
            sessionFinished: body.dataset.sessionFinished === "true",
            clipLength: parseInt(body.dataset.clipLength, 10) || 0,
        };
    }

    // -------------------------------------------------------------------
    // Volume
    // -------------------------------------------------------------------

    function currentVolumePercent() {
        const stored = localStorage.getItem(VOLUME_KEY);
        const n = stored === null ? DEFAULT_VOLUME : parseInt(stored, 10);
        if (Number.isNaN(n) || n < 0 || n > 100) return DEFAULT_VOLUME;
        return n;
    }

    function initVolumeControl() {
        const slider = document.getElementById("volume-slider");
        const readout = document.getElementById("volume-readout");
        const audio = getAudio();
        if (!slider || !audio) return;
        const vol = currentVolumePercent();
        slider.value = String(vol);
        audio.volume = vol / 100;
        if (readout) readout.textContent = `${vol}%`;
        slider.addEventListener("input", () => {
            const v = parseInt(slider.value, 10);
            audio.volume = v / 100;
            localStorage.setItem(VOLUME_KEY, String(v));
            if (readout) readout.textContent = `${v}%`;
        });
    }

    // -------------------------------------------------------------------
    // Playback cursor (requestAnimationFrame)
    // -------------------------------------------------------------------

    function animatePlayhead() {
        const audio = getAudio();
        const playhead = document.querySelector(".clip-playhead");
        if (!audio || !playhead) return;
        if (audio.paused) {
            // Leave the playhead where it is but dim it; the raf loop stops.
            playhead.style.opacity = "0.4";
            playheadRAF = null;
            return;
        }
        const pct = Math.min(100, (audio.currentTime / MAX_CLIP_SECONDS) * 100);
        playhead.style.left = `${pct}%`;
        playhead.style.opacity = "1";
        playheadRAF = requestAnimationFrame(animatePlayhead);
    }

    function startPlayheadAnimation() {
        if (playheadRAF) cancelAnimationFrame(playheadRAF);
        playheadRAF = requestAnimationFrame(animatePlayhead);
    }

    function resetPlayheadAtZero() {
        const playhead = document.querySelector(".clip-playhead");
        if (!playhead) return;
        playhead.style.left = "0%";
        playhead.style.opacity = "0";
    }

    // -------------------------------------------------------------------
    // Pause scheduling
    // -------------------------------------------------------------------

    function schedulePauseAtClipEnd(clipLengthSeconds) {
        const audio = getAudio();
        if (!audio) return;
        if (clipPauseHandle) clearTimeout(clipPauseHandle);
        const remainingSeconds = clipLengthSeconds - audio.currentTime;
        if (remainingSeconds <= 0) {
            audio.pause();
            return;
        }
        clipPauseHandle = setTimeout(() => {
            audio.pause();
        }, remainingSeconds * 1000);
    }

    // -------------------------------------------------------------------
    // Play button
    // -------------------------------------------------------------------

    async function fetchPreviewUrl(gameId) {
        const response = await fetch(`/game/${gameId}/preview`);
        if (!response.ok) {
            console.error("preview request failed:", response.status);
            return null;
        }
        const body = await response.json();
        const key = `${gameId}:${body.target_id}`;
        PREVIEW_CACHE.set(key, body.preview_url);
        return body.preview_url;
    }

    async function playClip(gameId, clipLengthSeconds) {
        const audio = getAudio();
        if (!audio) return;
        // Re-apply volume belt-and-braces in case the audio element was
        // silently created/replaced without going through initVolumeControl.
        audio.volume = currentVolumePercent() / 100;

        const previewUrl = await fetchPreviewUrl(gameId);
        if (!previewUrl) return;

        // Always restart from the start of the preview on a manual Play click.
        // (Skip re-uses the running audio element via the afterSwap hook.)
        if (audio.src !== previewUrl) {
            audio.src = previewUrl;
        }
        audio.currentTime = 0;
        resetPlayheadAtZero();

        try {
            await audio.play();
        } catch (err) {
            console.error("audio.play() rejected:", err);
            return;
        }
        schedulePauseAtClipEnd(clipLengthSeconds);
        startPlayheadAnimation();
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

    // -------------------------------------------------------------------
    // htmx swap handling — decide whether to keep audio playing
    // -------------------------------------------------------------------

    document.body.addEventListener("htmx:beforeSwap", () => {
        preSwapState = snapshotState();
    });

    document.body.addEventListener("htmx:afterSwap", () => {
        // Re-bind DOM-dependent handlers.
        initPlayButton();
        initVolumeControl();

        const audio = getAudio();
        const post = snapshotState();
        if (!audio || !post) return;

        // New song just started (previous was in transition / finished state,
        // now we're at round_index 0 again) — pause any lingering playback.
        const wasTransition = preSwapState?.finished === true && !preSwapState?.sessionFinished;
        const nowFreshSong = post.roundIndex === 0 && !post.finished && !post.sessionFinished;
        if (wasTransition && nowFreshSong) {
            if (!audio.paused) audio.pause();
            if (clipPauseHandle) clearTimeout(clipPauseHandle);
            resetPlayheadAtZero();
            return;
        }

        // Song just finished (win or exhaustion) or session ended.
        if (post.finished || post.sessionFinished) {
            if (!audio.paused) audio.pause();
            if (clipPauseHandle) clearTimeout(clipPauseHandle);
            return;
        }

        // Mid-song swap (most likely a Skip). If audio is still playing,
        // reschedule the pause for the new clip_length instead of cutting
        // it off — this is the "Skip extends the clip" behaviour.
        if (!audio.paused && post.clipLength > 0) {
            schedulePauseAtClipEnd(post.clipLength);
            startPlayheadAnimation();
        }
    });

    // -------------------------------------------------------------------
    // Initial page load
    // -------------------------------------------------------------------

    document.addEventListener("DOMContentLoaded", () => {
        initPlayButton();
        initVolumeControl();
    });

    // Keep the playhead in sync if the user pauses/resumes via external
    // controls (not a typical case in this UI, but defensive).
    document.addEventListener("DOMContentLoaded", () => {
        const audio = getAudio();
        if (!audio) return;
        audio.addEventListener("play", startPlayheadAnimation);
        audio.addEventListener("pause", () => {
            const playhead = document.querySelector(".clip-playhead");
            if (playhead) playhead.style.opacity = "0.4";
        });
    });
})();
