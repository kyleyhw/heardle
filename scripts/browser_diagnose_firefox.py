"""Quick Firefox-specific probe of the button flow.

If a bug is Chromium-only the main diagnose wouldn't see it; if it's
Firefox-only (which is what the user seems to be hitting) this script
reproduces it.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

from playwright.async_api import ConsoleMessage, async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "diagnose" / "firefox"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8001"


async def main() -> int:
    console_log: list[str] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.firefox.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            def on_console(msg: ConsoleMessage) -> None:
                console_log.append(f"[{msg.type}] {msg.text}")

            page.on("console", on_console)
            page.on("pageerror", lambda e: console_log.append(f"[pageerror] {e}"))

            await page.goto(f"{BASE}/")
            await page.fill('input[name="source_value"]', "Ed Sheeran")
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{BASE}/game/*")
            print(f"Firefox: game created at {page.url}")
            await page.screenshot(path=str(OUT / "01_game_start.png"), full_page=True)

            # Play
            await page.click("#play-button")
            await asyncio.sleep(1.2)
            audio = await page.evaluate("""() => {
                const a = document.getElementById('heardle-audio');
                return {paused: a?.paused, currentTime: a?.currentTime, volume: a?.volume};
            }""")
            print(f"Firefox after Play: {audio}")

            # Type + select
            await page.fill("#guess-input", "perfect")
            await asyncio.sleep(0.8)
            item = page.locator("#autocomplete-list > li").first
            if await item.count() > 0:
                await item.click()
                await asyncio.sleep(0.3)
                submit_state = await page.evaluate("""() => {
                    const btn = document.getElementById('submit-guess');
                    return {disabled: btn?.disabled};
                }""")
                print(f"Firefox after picking suggestion: {submit_state}")
            else:
                print("Firefox: NO autocomplete suggestions")

            # Submit
            try:
                await page.click("#submit-guess", timeout=3000)
                await asyncio.sleep(0.8)
                after_submit = await page.evaluate("""() => {
                    const b = document.getElementById('game-body');
                    return {round_index: b?.dataset?.roundIndex,
                            clip_length: b?.dataset?.clipLength};
                }""")
                print(f"Firefox after Submit: {after_submit}")
            except Exception as e:
                print(f"Firefox Submit click failed: {type(e).__name__}: {str(e)[:120]}")

            # Skip mid-play — verify audio keeps playing
            try:
                await page.click("#play-button", timeout=2000)
                await asyncio.sleep(0.4)
                pre = await page.evaluate("""() => ({
                    paused: document.getElementById('heardle-audio').paused,
                    currentTime: document.getElementById('heardle-audio').currentTime,
                })""")
                print(f"Firefox mid-playback: {pre}")
                await page.click(".skip-button", timeout=2000)
                post = await page.evaluate("""() => ({
                    paused: document.getElementById('heardle-audio').paused,
                    currentTime: document.getElementById('heardle-audio').currentTime,
                })""")
                print(f"Firefox immediately after Skip: {post}")
            except Exception as e:
                print(f"Firefox Skip-extends probe failed: {type(e).__name__}: {str(e)[:120]}")

            # Exhaust + Next song + End session
            for _ in range(5):
                try:
                    await page.click(".skip-button", timeout=1500)
                    await asyncio.sleep(0.4)
                except Exception:
                    break
            exhausted = await page.evaluate("""() => {
                const b = document.getElementById('game-body');
                return {finished: b?.dataset?.finished,
                        has_next: !!document.querySelector('button.primary')};
            }""")
            print(f"Firefox after exhaust: {exhausted}")
            try:
                await page.click("button.primary", timeout=2000)
                await asyncio.sleep(0.8)
                nx = await page.evaluate("""() => ({
                    round: document.getElementById('game-body')?.dataset?.roundIndex,
                    session: document.getElementById('game-body')?.dataset?.sessionFinished,
                })""")
                print(f"Firefox after Next song: {nx}")
            except Exception as e:
                print(f"Firefox Next song failed: {type(e).__name__}")
            try:
                await page.click(".end-session-link", timeout=2000)
                await asyncio.sleep(0.8)
                en = await page.evaluate("""() => ({
                    session: document.getElementById('game-body')?.dataset?.sessionFinished,
                    has_sb: !!document.querySelector('.scoreboard'),
                })""")
                print(f"Firefox after End session: {en}")
            except Exception as e:
                print(f"Firefox End session failed: {type(e).__name__}")

            await page.screenshot(path=str(OUT / "zz_final.png"), full_page=True)
            await browser.close()
    except Exception:
        print(traceback.format_exc())

    print(f"\nFirefox console messages: {len(console_log)}")
    for m in console_log:
        print(f"  {m}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
