"""Takes the README screenshots from the demo world: python tools\\screenshots.py (needs Playwright and Edge)."""

import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))

import demo
import serve_dashboard


def main():
    out = CODE / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    root, home = demo.build_world(demo.choose_base())
    server = serve_dashboard.make_server(root, ports=[0], home=home, chat_manager=demo.make_manager(root, home))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.ensure_world()
    server.scan.start(force=True)
    server.scan.thread.join(timeout=60)
    address = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(address)
            page.wait_for_selector("#today-dirty .card")
            time.sleep(0.5)
            page.screenshot(path=str(out / "today.png"))
            page.click("#tab-world")
            page.wait_for_selector("#scan-status:has-text('Nodes')")
            time.sleep(3)
            page.click("#q")
            page.keyboard.type("project atlas")
            page.wait_for_selector("#results li[role=option]")
            page.keyboard.press("Enter")
            page.wait_for_selector("#detail h2")
            time.sleep(1.5)
            page.screenshot(path=str(out / "world.png"))
            page.click("#detail .ask")
            page.fill("#chat-input", "What is unfinished?")
            page.keyboard.press("Control+Enter")
            page.wait_for_selector("#chat-cost:has-text('This message')", timeout=30000)
            page.screenshot(path=str(out / "chat.png"))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Saved today.png, world.png and chat.png in", out)


if __name__ == "__main__":
    main()
