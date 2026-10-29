"""Real browser path: load demo, configure params, generate, upscale, capture states."""
import asyncio, json, pathlib
from playwright.async_api import async_playwright

OUT = pathlib.Path("evidence"); OUT.mkdir(exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 980})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto("http://127.0.0.1:8960/", wait_until="networkidle")
        await page.wait_for_selector(".style-grid button", timeout=20000)
        style_count = await page.locator(".style-grid button").count()
        account_text = (await page.locator("#account").inner_text()).replace("\n", " | ")
        print("styles in grid:", style_count)
        print("account:", account_text)
        await page.screenshot(path=str(OUT / "01_loaded.png"))

        await page.fill("#prompt", "a samurai standing in a bamboo forest, morning mist")
        await page.select_option("#provider", "flux")
        await page.select_option("#ar", "16:9")
        await page.select_option("#n", "2")
        await page.fill("#neg", "blurry, watermark")
        await page.locator(".style-grid button").nth(10).click()
        await page.screenshot(path=str(OUT / "02_configured.png"))

        await page.click("#go")
        await page.wait_for_selector(".card img", timeout=180000)
        cards = await page.locator(".card").count()
        await page.wait_for_function(
            "() => document.querySelectorAll('.card img').length > 0 && [...document.querySelectorAll('.card img')].every(i => i.complete && i.naturalWidth > 0)",
            timeout=120000,
        )
        stats = await page.locator("#stats").inner_text()
        print("cards:", cards, "| stats:", stats.replace("\n", " "))
        await page.screenshot(path=str(OUT / "03_results.png"))

        first = page.locator(".card button").first
        await first.click()
        await page.wait_for_function(
            "() => { const b = document.querySelector('.card button'); return b && b.textContent.includes('已完成'); }",
            timeout=180000,
        )
        print("upscale:", await first.inner_text())
        src = await page.locator(".card img").first.get_attribute("src")
        print("upscaled src:", src)
        await page.screenshot(path=str(OUT / "04_upscaled.png"))

        error_box = await page.locator("#error").is_visible()
        print("console errors:", errors)
        print("error box visible:", error_box)
        await browser.close()
        return 0 if cards == 2 and not errors else 1

raise SystemExit(asyncio.run(main()))
