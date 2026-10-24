"""Drives the console in a real browser (Edge, through Playwright) on the made-up demo world.

Run it from PowerShell:  python tests\browser_check.py <folder for screenshots>
It needs Playwright and Edge (see requirements-dev.txt). It builds the demo world with demo.build_world, so it
never reads your own folders, and the chat is answered by tests\fake_claude.py, so nothing real is called.
The design detector is optional: set OS_DETECTOR to its command to run it, or leave it unset to skip it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat
import demo
import serve_dashboard

REAL = Path(__file__).resolve().parent.parent
DETECTOR = os.environ.get("OS_DETECTOR")
SIZES = ((375, 812), (1440, 900))
failures = []
report = {}


def check(condition, message):
    print(("ok    " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


def wait_until(test, seconds=30, step=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(step)
    return False


def draws(page):
    return int(page.evaluate("document.getElementById('world').dataset.draws || '0'"))


def painted(page):
    return page.evaluate(
        "() => { const c = document.getElementById('world'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
        " let n = 0; for (let i = 0; i < d.length; i += 28) { if (d[i + 3] > 40) { n += 1; } } return n; }"
    )


def new_page(browser, address, width, height, reduced="no-preference", view="world"):
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion=reduced)
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=address)
    page = context.new_page()
    page.errors, page.outside, page.scan_requests = [], [], []
    page.on("request", lambda request: page.scan_requests.append(request.url) if request.url.split("?")[0].endswith("/api/scan") and request.method == "GET" else None)
    page.on("console", lambda message: page.errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.on("request", lambda request: page.outside.append(request.url) if not request.url.startswith(address) else None)
    page.goto(address)
    if view == "world":
        page.click("#tab-world")
        page.wait_for_selector("#scan-status:has-text('Nodes')", timeout=60000)
    else:
        page.wait_for_selector("#today-dirty .card", timeout=60000)
    return context, page


def today_checks(browser, address, out, width, height, temp_inbox):
    label = f"{width}px today"
    context, page = new_page(browser, address, width, height, view="today")
    check(page.get_attribute("#tab-today", "aria-selected") == "true", f"{label}: the page opens on Today")
    check(not page.is_visible("#world"), f"{label}: the map is not what you see first")
    titles = [page.inner_text(f"#{name}") for name in ("today-dirty-title", "today-reports-title", "today-ideas-title")]
    check([t.lower() for t in titles] == ["uncommitted work", "overnight reports", "inbox ideas"], f"{label}: the three sections are there ({titles})")
    for name in ("today-dirty", "today-reports", "today-ideas"):
        check(page.locator(f"#{name} .card").count() >= 1, f"{label}: {name} shows at least a card or a plain empty message")
    check("Latest added first" in page.inner_text("#today-ideas"), f"{label}: the inbox says how it is ordered")
    wait_until(lambda: page.locator("#today-view .card .tool").count() >= 1, 60)
    page.screenshot(path=str(out / f"today-{width}.png"), full_page=True)
    check(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0, f"{label}: no sideways scroll")

    idea = f"today check {width} <b>idea</b>"
    page.click("#tab-cards")
    page.fill("#idea", idea)
    page.click("#add-form button")
    page.wait_for_selector("#add-status:text-is('Added.')")
    page.click("#tab-today")
    check(page.inner_text("#today-ideas li >> nth=0") == idea, f"{label}: a new idea shows first, as plain text")
    check(page.locator("#today-ideas b").count() == 0, f"{label}: no markup is built from an idea")
    check(temp_inbox.read_text(encoding="utf-8").count(f"- {idea}\n") == 1, f"{label}: the idea reached the inbox file once")

    page.focus("#tab-today")
    page.keyboard.press("ArrowLeft")
    check(page.get_attribute("#tab-cards", "aria-selected") == "true" and not page.is_visible("#today-view"), f"{label}: the left arrow wraps from Today to Cards")
    page.keyboard.press("ArrowRight")
    check(page.get_attribute("#tab-today", "aria-selected") == "true" and page.is_visible("#today-view") and not page.is_visible("#cards-view"), f"{label}: the right arrow wraps back and only one view shows")

    button = page.locator("#today-view .card .tool:has-text('Open on map')")
    if button.count():
        button.first.click()
        page.wait_for_selector("#detail h2")
        check(page.get_attribute("#tab-world", "aria-selected") == "true" and page.is_visible("#world"), f"{label}: Open on map goes to the map")
        check(page.evaluate("document.getElementById('world').width") > 0, f"{label}: the map has a size after being hidden at the start")
        check(page.inner_text("#detail h2") != "", f"{label}: the node's details open")
    else:
        check(False, f"{label}: at least one Open on map button exists")
    page.click("#tab-today")
    page.click("#chat-open")
    page.wait_for_selector("#chat:not([hidden])")
    check(page.is_visible("#side") and page.get_attribute("#tab-world", "aria-selected") == "true", f"{label}: the Chat button from Today opens the chat on the map view")
    unexpected = [error for error in page.errors if "429" not in error]
    check(not unexpected, f"{label}: no console errors {unexpected[:2]}")
    check(not page.outside, f"{label}: nothing requested from outside the server {page.outside[:2]}")
    context.close()


def world_checks(browser, address, out, width, height, temp_inbox, first):
    label = f"{width}px"
    context, page = new_page(browser, address, width, height)
    time.sleep(3.5)
    page.screenshot(path=str(out / f"world-idle-{width}.png"))
    check(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0, f"{label}: no sideways scroll")
    check(painted(page) > 300, f"{label}: the canvas is drawn, not blank ({painted(page)} sampled pixels)")
    status = page.inner_text("#scan-status")
    count = int(status.split("Nodes ")[1].split()[0])
    check(count >= 20, f"{label}: the world has {count} nodes")

    before = draws(page)
    time.sleep(2)
    rate = (draws(page) - before) / 2
    report[f"{width}px draws per second, full show"] = rate
    check(rate >= 15, f"{label}: the show runs at {rate:.0f} draws a second (floor 15)")

    wait_until(lambda: "History scan done" in page.inner_text("#scan-status"), 120)
    page.keyboard.press("/")
    check(page.evaluate("document.activeElement.id") == "q", f"{label}: the slash key jumps to the search bar")
    page.keyboard.type("review-change")
    page.wait_for_selector("#results li[role=option]")
    page.screenshot(path=str(out / f"search-{width}.png"))
    first_hit = page.inner_text("#results li >> nth=0")
    check("review-change" in first_hit, f"{label}: search finds the skill first ({first_hit[:40]!r})")
    page.keyboard.press("Enter")
    page.wait_for_selector("#detail h2")
    check(page.inner_text("#detail h2") == "review-change", f"{label}: Enter opens that node's panel")
    wait_until(lambda: "Used" in page.inner_text("#detail"), 20)
    check("Used" in page.inner_text("#detail"), f"{label}: the panel shows its use count once the refreshed data arrives")
    check(page.locator("#detail .link").count() >= 1, f"{label}: the panel lists connections")
    box = page.locator("#detail").bounding_box()
    check(box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width + 1 and box["y"] + box["height"] <= height + 1, f"{label}: the panel fits inside the window")
    time.sleep(1.2)
    page.screenshot(path=str(out / f"world-selected-{width}.png"))

    page.click("#detail >> text=Copy prompt")
    page.wait_for_selector("#detail >> text=Copied")
    clip = page.evaluate("navigator.clipboard.readText()")
    check(clip == "Use the review-change skill on ", f"{label}: Copy prompt puts the exact template on the clipboard ({clip!r})")

    page.locator("#detail .link").first.click()
    time.sleep(0.5)
    check(page.inner_text("#detail h2") != "review-change", f"{label}: clicking a connection moves to that node")
    check(page.evaluate("document.activeElement.tagName") == "H2", f"{label}: keyboard focus moves to the new node's heading, not to the page")

    page.click("#filters summary") if page.locator("#filters[open]").count() == 0 else None
    on = painted(page)
    page.uncheck("#filter-list input[value=skill]")
    time.sleep(0.6)
    off = painted(page)
    check(off < on, f"{label}: hiding Skills removes them from the canvas ({on} to {off})")
    page.check("#filter-list input[value=skill]")

    page.click("#calm")
    check(page.get_attribute("#calm", "aria-pressed") == "true", f"{label}: the Calm switch turns on")
    time.sleep(1.2)
    a = draws(page)
    time.sleep(1.5)
    check(draws(page) == a, f"{label}: in Calm nothing is redrawn while idle ({a} draws)")
    page.screenshot(path=str(out / f"world-calm-{width}.png"))
    page.click("#calm")
    time.sleep(0.5)
    b = draws(page)
    time.sleep(1)
    check(draws(page) > b + 5, f"{label}: turning Calm off brings the motion back")

    reached = set()
    page.focus("#q")
    reached.add("q")
    for _ in range(25):
        page.keyboard.press("Tab")
        reached.add(page.evaluate("document.activeElement.id || document.activeElement.tagName"))
    check({"q", "tab-world", "calm", "fit", "more", "world"} <= reached, f"{label}: Tab reaches the search, tabs, tools and the canvas (reached {sorted(reached)})")

    page.click("#more")
    page.click("#rescan")
    time.sleep(0.8)
    page.click("#more")
    banner = page.inner_text("#banner") if page.locator("#banner:not([hidden])").count() else ""
    state = page.inner_text("#scan-status")
    check("already running" in banner.lower() or "moment ago" in banner or "History scan" in state, f"{label}: Rescan answers politely ({banner[:50]!r})")

    if first:
        done = wait_until(lambda: "History scan done" in page.inner_text("#scan-status"), 120)
        check(done, f"{label}: the history scan finishes ({page.inner_text('#scan-status')[:90]!r})")

    page.click("#tab-cards")
    page.wait_for_selector("#projects .card")
    heads = page.locator("#projects .card-head")
    check(heads.count() >= 1, f"{label}: the Cards tab lists projects ({heads.count()})")
    heads.first.click()
    page.wait_for_selector("#projects .panel:not([hidden]) h4")
    check(heads.first.get_attribute("aria-expanded") == "true", f"{label}: a card opens its drill-down")
    heads.first.focus()
    page.keyboard.press("Enter")
    check(heads.first.get_attribute("aria-expanded") == "false", f"{label}: Enter closes it")
    idea = f"browser check {label} <b>idea</b>"
    page.fill("#idea", idea)
    page.dblclick("#add-form button")
    page.wait_for_selector("#add-status:text-is('Added.')")
    check(idea in page.inner_text("#ideas"), f"{label}: the idea shows as plain text")
    check(temp_inbox.read_text(encoding="utf-8").count(f"- {idea}\n") == 1, f"{label}: a double click adds the idea once")
    page.screenshot(path=str(out / f"cards-{width}.png"), full_page=True)
    check(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0, f"{label}: Cards has no sideways scroll")

    page.click("#tab-world")
    page.wait_for_selector("#detail", state="attached")
    page.click("#fit")
    time.sleep(0.8)
    page.click("#side-close") if page.locator("#side:not([hidden])").count() else None
    check(page.evaluate("document.activeElement.id") == "world", f"{label}: closing the panel returns focus to the map")
    page.evaluate("document.getElementById('world').focus()")
    page.keyboard.press("Escape")
    check(not page.evaluate("document.getElementById('side').hidden === false"), f"{label}: Escape clears the selection")
    unexpected = [error for error in page.errors if "429" not in error]
    check(not unexpected, f"{label}: no console errors beyond the Rescan refusal {unexpected[:2]}")
    check(not page.outside, f"{label}: nothing requested from outside the server {page.outside[:2]}")
    context.close()


def chat_checks(browser, address, out, width, height):
    label = f"{width}px chat"
    os.environ["FAKE_SCENARIO"] = "text"
    context, page = new_page(browser, address, width, height)
    page.click("#chat-open")
    page.wait_for_selector("#chat:not([hidden])")
    check("read only" in page.inner_text("#chat-chip").lower() and "Anthropic" in page.inner_text("#chat-chip"), f"{label}: the context chip says read only and where files go")
    check(page.inner_text(".ro") == "READ ONLY" or page.inner_text(".ro").lower() == "read only", f"{label}: the Read only badge is on screen")
    check(page.evaluate("document.activeElement.id") == "chat-input", f"{label}: opening the chat puts the cursor in the message box")
    check(wait_until(lambda: not page.is_disabled("#chat-send"), 10), f"{label}: Send is ready once the server says chat is available")
    check(page.get_attribute("#chat-log", "aria-live") == "off", f"{label}: the log does not announce every streamed word")

    page.fill("#chat-input", "What is <b>here</b>?")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector("#chat-log .msg.claude")
    check(wait_until(lambda: "This message" in page.inner_text("#chat-cost"), 15), f"{label}: the cost line appears after an answer")
    check(page.inner_text("#chat-log .msg.you .msg-text") == "What is <b>here</b>?", f"{label}: your message shows as plain text")
    check(page.locator("#chat-log b").count() == 0, f"{label}: no markup is built from text")
    check("Hello there." in page.inner_text("#chat-log .msg.claude .msg-text"), f"{label}: the answer streams into the log")
    check(page.get_attribute("#chat-log", "aria-busy") == "false", f"{label}: the log is not busy once the answer ends")
    check(page.evaluate("document.activeElement.id") == "chat-input", f"{label}: the cursor returns to the message box")
    check("Claude has finished" in page.inner_text("#live"), f"{label}: one announcement at the end")
    page.screenshot(path=str(out / f"chat-answer-{width}.png"))

    os.environ["FAKE_SCENARIO"] = "tool"
    page.fill("#chat-input", "Read the readme")
    page.click("#chat-send")
    check(wait_until(lambda: "Reading" in page.inner_text("#chat-log"), 15), f"{label}: a file Claude reads is listed")
    check("rm -rf" not in page.inner_text("#chat-log"), f"{label}: an unknown tool is never shown")
    wait_until(lambda: not page.is_disabled("#chat-send"), 15)

    os.environ["FAKE_SCENARIO"] = "hang"
    page.fill("#chat-input", "Keep going")
    page.click("#chat-send")
    page.wait_for_selector("#chat-stop:not([hidden])")
    check(page.is_disabled("#chat-send"), f"{label}: only one message at a time, Send is off while Claude answers")
    check(page.get_attribute("#chat-log", "aria-busy") == "true", f"{label}: the log is busy while Claude answers")
    page.screenshot(path=str(out / f"chat-running-{width}.png"))
    page.click("#chat-stop")
    check(wait_until(lambda: "Stopped." in page.inner_text("#chat-log"), 15), f"{label}: Stop ends the answer and says so")
    check(wait_until(lambda: not page.is_disabled("#chat-send"), 10), f"{label}: Send is back after Stop")
    check(page.evaluate("document.activeElement.id") == "chat-input", f"{label}: the cursor returns to the message box after Stop")

    os.environ["FAKE_SCENARIO"] = "error"
    page.fill("#chat-input", "Will this work?")
    page.click("#chat-send")
    check(wait_until(lambda: "not signed in" in page.inner_text("#chat-log"), 15), f"{label}: a sign-in problem gets the fixed message")
    check("PLANTED" not in page.inner_text("#chat-log"), f"{label}: raw error text from Claude never reaches the page")
    wait_until(lambda: not page.is_disabled("#chat-send"), 10)
    os.environ["FAKE_SCENARIO"] = "text"

    page.click("#chat-new")
    check(page.locator("#chat-log .msg.you").count() == 0, f"{label}: New chat clears the conversation")

    page.click("#q")
    page.keyboard.type("project atlas")
    page.wait_for_selector("#results li[role=option]")
    page.keyboard.press("Enter")
    page.wait_for_selector("#detail h2")
    check(page.locator("#detail .ask").count() == 1, f"{label}: every node has an Ask Claude button")
    page.click("#detail .ask")
    page.wait_for_selector("#chat:not([hidden])")
    check(page.get_attribute("#tab-chat", "aria-selected") == "true", f"{label}: Ask Claude opens the Chat tab")
    check("atlas" in page.inner_text("#chat-chip").lower(), f"{label}: the chat opens about the node you clicked ({page.inner_text('#chat-chip')[:60]!r})")
    page.fill("#chat-input", "What is unfinished?")
    page.click("#chat-send")
    check(wait_until(lambda: "Hello there." in page.inner_text("#chat-log"), 15), f"{label}: a node chat gets an answer")
    check(wait_until(lambda: "atlas" in page.inner_text("#chat-chip").lower() and "\\" in page.inner_text("#chat-chip"), 5), f"{label}: the chip shows the folder after the first message")
    wait_until(lambda: not page.is_disabled("#chat-send"), 10)

    page.fill("#chat-input", "it's a 'quote' $(calc)")
    page.click("#chat-copy")
    page.wait_for_selector("#chat-cost:has-text('Copied')")
    clip = page.evaluate("navigator.clipboard.readText()")
    check(clip.startswith("Set-Location -LiteralPath '") and clip.endswith("claude 'it''s a ''quote'' $(calc)'"), f"{label}: Copy command is a safe single-quoted PowerShell line ({clip[:90]!r})")

    page.click("#chat-open")
    check("General chat" in page.inner_text("#chat-chip"), f"{label}: the header Chat button gives a general chat")
    check(page.locator("#chat-log .msg.you").count() == 0, f"{label}: the general chat starts clean")
    page.click("#tab-details")
    page.click("#detail .ask") if page.locator("#detail .ask").count() else None
    check("atlas" in page.inner_text("#chat-chip").lower() or page.locator("#detail .ask").count() == 0, f"{label}: going back to a node brings its conversation back")

    box = page.locator("#side").bounding_box()
    check(box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width + 1 and box["y"] >= 0 and box["y"] + box["height"] <= height + 1, f"{label}: the panel fits inside the window")
    check(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0, f"{label}: no sideways scroll")
    page.screenshot(path=str(out / f"chat-node-{width}.png"))
    unexpected = [error for error in page.errors if "429" not in error]
    check(not unexpected, f"{label}: no console errors {unexpected[:2]}")
    check(not page.outside, f"{label}: nothing requested from outside the server {page.outside[:2]}")
    context.close()


def poller_check(browser, address, server):
    real = server.scan.scan_file

    def slow(*arguments, **keywords):
        time.sleep(0.15)
        return real(*arguments, **keywords)

    server.scan.scan_file = slow
    server.scan.stop()
    server.scan.start(force=True)
    context, page = new_page(browser, address, 1440, 900)
    page.wait_for_selector("#scan-status:has-text('percent')", timeout=20000)
    page.click("#more")
    page.wait_for_timeout(300)
    start = len(page.scan_requests)
    for _ in range(5):
        page.click("#rebuild")
        page.wait_for_timeout(200)
    page.wait_for_timeout(6000)
    polls = len(page.scan_requests) - start
    check(2 <= polls <= 6, f"repeated Rebuild during a scan keeps one poller ({polls} status requests in about 7 seconds)")
    check("percent" in page.inner_text("#scan-status"), "the scan was still running while that was measured")
    context.close()
    server.scan.stop()
    server.scan.scan_file = real


def reduced_motion_check(browser, address):
    context, page = new_page(browser, address, 1440, 900, reduced="reduce")
    check(page.get_attribute("#calm", "aria-pressed") == "true", "reduced motion: the world starts in Calm")
    time.sleep(1.5)
    a = draws(page)
    time.sleep(1.5)
    check(draws(page) == a, "reduced motion: nothing is redrawn while idle")
    context.close()


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    base = demo.choose_base()
    temp, home = demo.build_world(base)
    manager = chat.ChatManager(
        temp, home, lambda: serve_dashboard.indexed_projects(temp),
        launcher=[sys.executable, "-I", str(REAL / "tests" / "fake_claude.py")], executable="C:/fake/claude.exe",
        version=lambda: "1.2.3", tested=lambda: {"1.2.3"}, limits={"min_gap_seconds": 0, "heartbeat_seconds": 5},
    )
    server = serve_dashboard.make_server(temp, ports=[0], home=home, chat_manager=manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    server.world_view()
    server.scan.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge")
            for number, (width, height) in enumerate(SIZES):
                world_checks(browser, address, out, width, height, temp / "INBOX.md", number == 0)
            for width, height in SIZES:
                today_checks(browser, address, out, width, height, temp / "INBOX.md")
            for width, height in SIZES:
                chat_checks(browser, address, out, width, height)
            poller_check(browser, address, server)
            reduced_motion_check(browser, address)
            browser.close()
        for viewport in ("390x844", "1440x900") if DETECTOR else ():
            result = subprocess.run(
                [DETECTOR, "detect", "--json", "--viewport", viewport, address + "/"],
                capture_output=True, text=True, timeout=240,
            )
            print(f"detector {viewport}: exit {result.returncode} {result.stdout.strip()[:900]} {result.stderr.strip()[:300]}")
        if not DETECTOR:
            print("detector: skipped (OS_DETECTOR is not set)")
    finally:
        server.shutdown()
        server.server_close()
    for name, value in report.items():
        print(f"figure {name}: {value}")
    print(f"{len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
