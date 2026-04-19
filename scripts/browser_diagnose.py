"""Drive the running server with a headless browser to reproduce UX bugs.

Usage:
    # In one terminal:
    uv run uvicorn heardle.api:app --host 127.0.0.1 --port 8001

    # In another:
    uv run python -m scripts.browser_diagnose

Output goes to ``diagnose/`` at repo root (screenshots + logs).
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

from playwright.async_api import ConsoleMessage, async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "diagnose"
OUT.mkdir(exist_ok=True)

BASE = "http://127.0.0.1:8001"


async def main() -> int:
    console_log: list[str] = []
    network_errors: list[str] = []
    crash: str | None = None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            def on_console(msg: ConsoleMessage) -> None:
                console_log.append(f"[{msg.type}] {msg.text}")

            page.on("console", on_console)
            page.on("pageerror", lambda e: console_log.append(f"[pageerror] {e}"))
            page.on(
                "requestfailed",
                lambda req: network_errors.append(
                    f"{req.method} {req.url} - {req.failure or 'unknown'}"
                ),
            )

            await page.goto(f"{BASE}/")
            await page.screenshot(path=str(OUT / "01_index.png"), full_page=True)

            await page.fill('input[name="source_value"]', "Ed Sheeran")
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE}/game/*")
            game_id = page.url.rsplit("/", 1)[-1]
            print(f"Game created: {game_id}")
            await page.screenshot(path=str(OUT / "02_game_start.png"), full_page=True)
            (OUT / "02_game_start.html").write_text(await page.content(), encoding="utf-8")

            # ------- Play button -------
            await page.click("#play-button")
            await asyncio.sleep(1.5)
            audio_state = await page.evaluate("""() => {
                const a = document.getElementById('heardle-audio');
                return {src: a?.src?.slice(0, 60), paused: a?.paused,
                        currentTime: a?.currentTime, volume: a?.volume};
            }""")
            print(f"After Play: {audio_state}")
            await page.screenshot(path=str(OUT / "03_after_play.png"))

            # ------- Autocomplete type + inspect dropdown -------
            await page.fill("#guess-input", "perfect")
            await asyncio.sleep(1.0)
            dropdown_state = await page.evaluate("""() => {
                const ul = document.getElementById('autocomplete-list');
                const input = document.getElementById('guess-input');
                return {
                    input_value: input?.value,
                    hidden: ul?.hidden,
                    hidden_attr: ul?.getAttribute('hidden'),
                    count: ul?.children?.length,
                    innerHTML_len: ul?.innerHTML?.length,
                    innerHTML_snippet: ul?.innerHTML?.slice(0, 200),
                    computed_display: window.getComputedStyle(ul).display,
                };
            }""")
            print(f"Autocomplete state: {dropdown_state}")
            await page.screenshot(path=str(OUT / "04_autocomplete_open.png"))

            # ------- Trigger the fetch manually to see if the JS code works -------
            manual_fetch = await page.evaluate(
                """async (gid) => {
                    const r = await fetch(`/autocomplete?q=perfect&game_id=${gid}`);
                    const j = await r.json();
                    return {status: r.status, count: j.length, first: j[0]};
                }""",
                game_id,
            )
            print(f"Manual fetch from browser: {manual_fetch}")

            # ------- Check whether the input listener is attached -------
            await page.evaluate("""() => {
                const input = document.getElementById('guess-input');
                input.value = 'shape';
                input.dispatchEvent(new Event('input', {bubbles: true}));
            }""")
            await asyncio.sleep(1.0)
            post_dispatch = await page.evaluate("""() => {
                const ul = document.getElementById('autocomplete-list');
                return {
                    hidden: ul?.hidden,
                    count: ul?.children?.length,
                    innerHTML_snippet: ul?.innerHTML?.slice(0, 120),
                };
            }""")
            print(f"After dispatchEvent('input'): {post_dispatch}")

            # ------- Try clicking the first suggestion item if present -------
            first_item = page.locator("#autocomplete-list > li").first
            item_count = await first_item.count()
            print(f"First suggestion item count: {item_count}")
            if item_count > 0:
                await first_item.click()
                await asyncio.sleep(0.4)
                submit_state = await page.evaluate("""() => {
                    const btn = document.getElementById('submit-guess');
                    const h = document.getElementById('guess-track-id');
                    return {submit_disabled: btn?.disabled,
                            hidden_value: h?.value,
                            input_value: document.getElementById('guess-input')?.value};
                }""")
                print(f"After suggestion click: {submit_state}")
                await page.screenshot(path=str(OUT / "05_suggestion_picked.png"))

                # ------- Click Submit -------
                try:
                    await page.click("#submit-guess", timeout=3000)
                    await asyncio.sleep(1.0)
                    after_submit = await page.evaluate("""() => {
                        const body = document.getElementById('game-body');
                        return {round_index: body?.dataset?.roundIndex,
                                finished: body?.dataset?.finished,
                                clip_length: body?.dataset?.clipLength};
                    }""")
                    print(f"After Submit click: {after_submit}")
                    await page.screenshot(path=str(OUT / "06_after_submit.png"))
                except Exception as e:
                    print(f"Submit click failed: {type(e).__name__}: {str(e)[:200]}")
                    await page.screenshot(path=str(OUT / "06_submit_failed.png"))

            # ------- Try Skip -------
            try:
                await page.click(".skip-button", timeout=3000)
                await asyncio.sleep(1.0)
                after_skip = await page.evaluate("""() => {
                    const body = document.getElementById('game-body');
                    return {round_index: body?.dataset?.roundIndex,
                            finished: body?.dataset?.finished,
                            clip_length: body?.dataset?.clipLength};
                }""")
                print(f"After Skip: {after_skip}")
                await page.screenshot(path=str(OUT / "07_after_skip.png"))
            except Exception as e:
                print(f"Skip click failed: {type(e).__name__}: {str(e)[:200]}")

            # ------- Skip-extends: audio should not restart when skipping -------
            # Click Play, wait ~0.3 s, then click Skip; audio should continue
            # playing past 0.3 s (not reset to 0) and the pause timer should
            # re-anchor to the new (longer) clip length.
            try:
                await page.click("#play-button", timeout=2000)
                await asyncio.sleep(0.4)
                mid_state = await page.evaluate("""() => {
                    const a = document.getElementById('heardle-audio');
                    return {paused: a?.paused, currentTime: a?.currentTime};
                }""")
                print(f"Mid-playback (before Skip): {mid_state}")
                await page.click(".skip-button", timeout=2000)
                # Immediately after the swap — audio should still be playing
                # and currentTime should NOT have been reset to 0.
                post_skip_audio = await page.evaluate("""() => {
                    const a = document.getElementById('heardle-audio');
                    return {paused: a?.paused, currentTime: a?.currentTime};
                }""")
                print(f"Immediately after Skip (audio): {post_skip_audio}")
            except Exception as e:
                print(f"Skip-extends probe failed: {type(e).__name__}: {str(e)[:200]}")

            # ------- Exhaust the song with skips -------
            for i in range(6):
                try:
                    btn = page.locator(".skip-button").first
                    if await btn.count() == 0:
                        break
                    await btn.click(timeout=1500)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"  exhaust-skip #{i} failed: {type(e).__name__}: {str(e)[:100]}")
                    break
            exhaust_state = await page.evaluate("""() => {
                const body = document.getElementById('game-body');
                return {finished: body?.dataset?.finished,
                        session_finished: body?.dataset?.sessionFinished,
                        has_next_button: !!document.querySelector('button.primary'),
                        has_end_button: !!document.querySelector(
                            'button.secondary, .end-session-link')};
            }""")
            print(f"Exhausted song: {exhaust_state}")
            await page.screenshot(path=str(OUT / "08_song_exhausted.png"), full_page=True)

            # ------- Click "Next song" (transition state primary button) -------
            try:
                await page.click("button.primary", timeout=3000)
                await asyncio.sleep(1.0)
                after_next = await page.evaluate("""() => {
                    const body = document.getElementById('game-body');
                    return {round_index: body?.dataset?.roundIndex,
                            finished: body?.dataset?.finished,
                            session_finished: body?.dataset?.sessionFinished};
                }""")
                print(f"After Next song: {after_next}")
                await page.screenshot(path=str(OUT / "09_after_next.png"), full_page=True)
            except Exception as e:
                print(f"Next song click failed: {type(e).__name__}: {str(e)[:200]}")
                await page.screenshot(path=str(OUT / "09_next_failed.png"), full_page=True)

            # ------- Click "End session" (mid-game link style) -------
            try:
                await page.click(".end-session-link", timeout=3000)
                await asyncio.sleep(1.0)
                after_end = await page.evaluate("""() => {
                    const body = document.getElementById('game-body');
                    return {session_finished: body?.dataset?.sessionFinished,
                            has_scoreboard: !!document.querySelector('.scoreboard')};
                }""")
                print(f"After End session: {after_end}")
                await page.screenshot(path=str(OUT / "10_after_end.png"), full_page=True)
            except Exception as e:
                print(f"End session click failed: {type(e).__name__}: {str(e)[:200]}")
                await page.screenshot(path=str(OUT / "10_end_failed.png"), full_page=True)

            await browser.close()
    except Exception:
        crash = traceback.format_exc()
        print(f"Diagnose crashed:\n{crash}")

    (OUT / "console.log").write_text("\n".join(console_log), encoding="utf-8")
    (OUT / "network_errors.log").write_text("\n".join(network_errors), encoding="utf-8")
    if crash:
        (OUT / "crash.log").write_text(crash, encoding="utf-8")
    print(f"\nConsole messages: {len(console_log)}")
    for msg in console_log:
        print(f"  {msg}")
    print(f"Network failures: {len(network_errors)}")
    for msg in network_errors:
        print(f"  {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
