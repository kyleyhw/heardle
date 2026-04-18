/*
 * Debounced typeahead over /autocomplete.
 *
 * The endpoint returns [{id, title, artist, year}, ...] sorted by fuzzy
 * match score. User selection populates a hidden input (guess-spotify-id)
 * and enables the submit button.
 *
 * We re-initialise on every htmx swap because the autocomplete lives inside
 * the swappable #game-body fragment — after a guess, the DOM node is gone
 * and replaced with a fresh one. Binding once at page load would only wire
 * the first round.
 */

(function () {
    "use strict";

    const DEBOUNCE_MS = 300;
    const MIN_QUERY_LENGTH = 2;

    function initAutocomplete() {
        const input = document.getElementById("guess-input");
        const list = document.getElementById("autocomplete-list");
        const hiddenId = document.getElementById("guess-spotify-id");
        const submit = document.getElementById("submit-guess");
        if (!input || !list || !hiddenId || !submit) return;  // game is finished

        const gameId = input.dataset.gameId;
        let debounceHandle = null;

        function clearSuggestions() {
            list.innerHTML = "";
            list.hidden = true;
        }

        function selectSuggestion(suggestion) {
            input.value = `${suggestion.title} — ${suggestion.artist}`;
            hiddenId.value = suggestion.id;
            submit.disabled = false;
            clearSuggestions();
        }

        async function fetchSuggestions(query) {
            const url = new URL("/autocomplete", window.location.origin);
            url.searchParams.set("q", query);
            url.searchParams.set("game_id", gameId);
            const response = await fetch(url);
            if (!response.ok) {
                console.error("autocomplete request failed", response.status);
                return [];
            }
            return response.json();
        }

        function renderSuggestions(suggestions) {
            list.innerHTML = "";
            if (suggestions.length === 0) {
                list.hidden = true;
                return;
            }
            for (const s of suggestions) {
                const item = document.createElement("li");
                item.innerHTML =
                    `<span class="title">${escapeHtml(s.title)}</span>` +
                    ` <span class="meta">${escapeHtml(s.artist)} · ${s.year}</span>`;
                item.addEventListener("mousedown", (ev) => {
                    // mousedown rather than click so the blur from input doesn't
                    // hide the list before the click fires.
                    ev.preventDefault();
                    selectSuggestion(s);
                });
                list.appendChild(item);
            }
            list.hidden = false;
        }

        input.addEventListener("input", () => {
            // Once the user edits, the previous selection is stale.
            hiddenId.value = "";
            submit.disabled = true;

            const query = input.value.trim();
            clearTimeout(debounceHandle);
            if (query.length < MIN_QUERY_LENGTH) {
                clearSuggestions();
                return;
            }
            debounceHandle = setTimeout(async () => {
                const suggestions = await fetchSuggestions(query);
                renderSuggestions(suggestions);
            }, DEBOUNCE_MS);
        });

        input.addEventListener("blur", () => {
            // Hide after the current event loop tick so mousedown on a
            // suggestion can still register.
            setTimeout(clearSuggestions, 150);
        });
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    document.addEventListener("DOMContentLoaded", initAutocomplete);
    document.body.addEventListener("htmx:afterSwap", initAutocomplete);
})();
