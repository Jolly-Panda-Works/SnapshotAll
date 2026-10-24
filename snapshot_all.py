#!/usr/bin/env python3
"""
SnapshotAll engine + command-line interface.

Modes (auto-detected from the target):
  web      website URL (online or localhost)
  apk      Android app (.apk)                      -> needs adb + emulator/phone
  desktop  Windows app (.exe / .jar / --attach)    -> Windows only

The desktop app (snapshot_gui.py) uses the same engines with a live preview.

Examples:
  python snapshot_all.py https://example.com
  python snapshot_all.py http://localhost:3000 --max-pages 100
  python snapshot_all.py app.apk --max-screens 60 --max-depth 4
  python snapshot_all.py "C:/Program Files/MyApp/MyApp.exe"
  python snapshot_all.py --attach "Notepad"
"""
import argparse
import functools
import hashlib
import http.server
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from snapshot_common import DEFAULT_AVOID, STOP, emit, normalize, page_key, site_key, slugify  # noqa: F401

SKIP_EXT = re.compile(
    r"\.(pdf|zip|rar|7z|gz|tar|png|jpe?g|gif|svg|webp|ico|mp3|mp4|avi|mov|webm|"
    r"css|js|json|xml|woff2?|ttf|eot|apk|exe|dmg|docx?|xlsx?|pptx?)$", re.I)

class BrowserUnavailable(Exception):
    """No usable Chromium/Edge/Chrome could be launched."""


def launch_browser(pw):
    """Try the Playwright Chromium first, then Microsoft Edge, then Google Chrome."""
    last = None
    for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return pw.chromium.launch(**kw)
        except Exception as e:  # missing executable, etc.
            last = e
    msg = (str(last).strip().splitlines() or ["browser launch failed"])[0]
    raise BrowserUnavailable(msg)


def install_chromium():
    """Download Playwright's Chromium using the driver bundled with the playwright package."""
    from playwright._impl._driver import compute_driver_executable, get_driver_env
    drv = compute_driver_executable()
    cmd = [str(x) for x in drv] if isinstance(drv, (tuple, list)) else [str(drv)]
    try:
        env = get_driver_env()
    except Exception:
        env = None
    flags = 0x08000000 if sys.platform == "win32" else 0  # no console window flash
    p = subprocess.Popen(cmd + ["install", "chromium"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, creationflags=flags)
    for line in p.stdout:
        print(line.rstrip())
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("Chromium download failed. Check your internet connection and try again.")


# ───────────────────────────── WEB ─────────────────────────────

def serve_dir(root: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/", srv


# Scrolls the whole page once so lazy-loaded content, images and scroll animations are triggered,
# then returns to the top. Smooth scrolling is switched off (it would leave the page mid-scroll).
SCROLL_JS = """async () => {
    const doc = document.documentElement;
    const st = document.createElement('style');
    st.textContent = 'html, body { scroll-behavior: auto !important; }';
    document.head.appendChild(st);
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const height = () => Math.max(doc.scrollHeight, document.body ? document.body.scrollHeight : 0);
    const step = Math.max(Math.round(window.innerHeight * 0.8), 200);
    let y = 0, last = height(), stable = 0;
    const t0 = Date.now();
    while (Date.now() - t0 < 20000) {              // hard limit for endless-scroll pages
        window.scrollTo({ top: y, behavior: 'instant' });
        await sleep(120);
        const h = height();
        if (window.scrollY + window.innerHeight >= h - 2) {
            stable = (h === last) ? stable + 1 : 0;  // at the bottom: wait until the height stops growing
            if (stable >= 3) break;
        } else {
            stable = 0;
        }
        last = h;
        y = Math.min(y + step, Math.max(h - window.innerHeight, 0));
    }
    window.scrollTo({ top: 0, behavior: 'instant' });
    await sleep(250);
}"""

# Full-page screenshots draw position:fixed / sticky elements relative to the FIRST screen, so a
# sticky footer, a cookie banner or a chat bubble ends up in the middle of a long page.
#   - sticky elements   -> back into normal flow
#   - fixed footers     -> moved to the real bottom of the page
#   - other fixed items that do not start at the top of the screen (cookie banners, chat, "back to top") -> hidden
#   - fixed headers / side bars that start at the top, and full-screen layers -> left alone
TIDY_JS = """() => {
    const doc = document.documentElement;
    const vw = window.innerWidth, vh = window.innerHeight;
    const docH = Math.max(doc.scrollHeight, document.body ? document.body.scrollHeight : 0);
    const imp = (el, k, v) => el.style.setProperty(k, v, 'important');
    const footerLike = el => el.matches('footer, [role="contentinfo"], [id*="footer" i], [class*="footer" i]')
        || !!el.querySelector('footer, [role="contentinfo"]');
    const res = { moved: 0, hidden: 0, unstuck: 0 };
    for (const el of Array.from(document.body.querySelectorAll('*'))) {
        const cs = getComputedStyle(el);
        if (cs.position === 'sticky') {
            imp(el, 'position', 'relative'); imp(el, 'top', 'auto'); imp(el, 'bottom', 'auto');
            res.unstuck++;
            continue;
        }
        if (cs.position !== 'fixed' || cs.display === 'none' || cs.visibility === 'hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        if (r.width >= vw * 0.9 && r.height >= vh * 0.9) continue;   // full-screen layer: leave alone
        if (r.top <= vh * 0.15) continue;                            // starts at the top: header / side bar
        if (footerLike(el)) {
            imp(el, 'position', 'absolute'); imp(el, 'bottom', 'auto'); imp(el, 'top', '0px');
            const base = el.getBoundingClientRect().top + window.scrollY;
            imp(el, 'top', Math.max(0, docH - r.height - base) + 'px');
            res.moved++;
        } else {
            imp(el, 'visibility', 'hidden');
            res.hidden++;
        }
    }
    return res;
}"""


def normalize_target(target):
    """Turn what a person types ('example.com', 'www.example.com', 'localhost:3000') into a full URL."""
    t = target.strip().strip('"')
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", t):
        return t
    host = t.split("/", 1)[0].rsplit("@", 1)[-1]
    hostname = host.split(":")[0].lower()
    local = (hostname == "localhost" or hostname.endswith(".local") or ":" in host
             or re.match(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", hostname))
    return ("http://" if local else "https://") + t


def run_web(target, out: Path, a):
    from playwright.sync_api import sync_playwright

    server = None
    p = Path(target)
    if p.exists():
        root = p if p.is_dir() else p.parent
        start_url, server = serve_dir(root.resolve())
        if p.is_file():
            start_url += p.name
    else:
        start_url = normalize_target(target)
    # No scheme typed (e.g. "example.com")? We start with https and fall back to http if it can't connect.
    auto_scheme = not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target.strip()) and start_url.startswith("https://")

    origin_key = site_key(urlparse(start_url).netloc)

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        kw = {}
        if a.mobile:
            kw.update(pw.devices["iPhone 13"])
        else:
            kw["viewport"] = {"width": a.width, "height": a.height}
        if a.storage_state:  # for pages behind a login
            kw["storage_state"] = a.storage_state
        ctx = browser.new_context(ignore_https_errors=True, **kw)
        page = ctx.new_page()

        queue = deque([(normalize(start_url), 0, None, "")])  # (url, depth, parent url, link text)
        seen = {page_key(start_url)}

        def goto_page(url, first):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                if first and auto_scheme and url.startswith("https://"):
                    print("  https failed - trying http…")
                    page.goto("http://" + url[len("https://"):], wait_until="domcontentloaded", timeout=30000)
                else:
                    raise

        # sitemap.xml (if present)
        try:
            r = ctx.request.get(f"{urlparse(start_url).scheme}://{urlparse(start_url).netloc}/sitemap.xml",
                                timeout=8000)
            if r.ok:
                for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text()):
                    n = normalize(loc)
                    if (site_key(urlparse(n).netloc) == origin_key and page_key(n) not in seen
                            and not SKIP_EXT.search(urlparse(n).path)):
                        seen.add(page_key(n))
                        queue.append((n, 1, normalize(start_url), ""))
        except Exception:
            pass

        count = 0
        while queue and count < a.max_pages and not STOP.is_set():
            url, depth, parent, link_text = queue.popleft()
            try:
                goto_page(url, count == 0)
                if count == 0 and page.url.startswith(("http://", "https://")):
                    # The site may have redirected us (http -> https, example.com -> www.example.com, ...):
                    # from now on "same site" means the host we actually landed on.
                    origin_key = site_key(urlparse(page.url).netloc)
                    seen.add(page_key(page.url))
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(int(a.delay * 1000))
                # scroll through the page to trigger lazy-loaded content, then tidy floating bars
                page.evaluate(SCROLL_JS)
                if not getattr(a, "keep_floating", False):
                    page.evaluate(TIDY_JS)
                    page.wait_for_timeout(150)
                count += 1
                path = urlparse(url)
                name = f"{count:03d}_{slugify((path.path or '/') + ('?' + path.query if path.query else '') + ('#' + path.fragment if path.fragment else ''))}.png"
                page.screenshot(path=str(out / name), full_page=True)
                try:
                    page_title = (page.title() or "").strip()
                except Exception:
                    page_title = ""
                links = page.eval_on_selector_all(
                    "a[href]",
                    """els => els.map(e => [
                        e.href,
                        (e.innerText || e.getAttribute('aria-label') || e.title || '').trim().replace(/\\s+/g, ' '),
                        e.closest('nav, header, footer, aside, [role="navigation"], [role="banner"], '
                                  + '[role="contentinfo"], [role="menubar"]') ? 'nav' : 'content'])""")
                site_links = []
                for h, text, zone in links:
                    if not h.startswith(("http://", "https://")):
                        continue
                    n = normalize(h)
                    if site_key(urlparse(n).netloc) == origin_key and not SKIP_EXT.search(urlparse(n).path):
                        site_links.append((n, text[:60], zone))
                emit(a, out / name, url, parent=parent, title=link_text or page_title, links=site_links)
                print(f"[{count}] {url}")

                if depth < a.max_depth:
                    for n, text, _zone in site_links:
                        if page_key(n) not in seen:
                            seen.add(page_key(n))
                            queue.append((n, depth + 1, url, text))
            except Exception as e:
                print(f"  ! Error on {url}: {e}")

        browser.close()
    if server:
        server.shutdown()
    print(f"\nDone: {count} page(s) captured")


# ───────────────────────────── APK ─────────────────────────────

def _find_adb():
    """adb from PATH, or the copy that ships with adbutils; None when neither exists."""
    found = shutil.which("adb")
    if found:
        return found
    try:
        import adbutils  # installed together with uiautomator2, ships an adb binary
        return adbutils.adb_path()
    except Exception:
        return None


def _device_connected(adb_bin, serial=None):
    try:
        out = subprocess.run([adb_bin, "devices"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return False
    ready = [ln.split("\t")[0] for ln in out.splitlines()[1:] if ln.strip().endswith("\tdevice")]
    return (serial in ready) if serial else bool(ready)


def run_apk(apk, out: Path, a):
    """Capture an APK. By default it runs on a built-in headless emulator that SnapshotAll sets up itself;
    a phone / emulator that is already connected is used when there is one (``--android-runtime``)."""
    runtime = getattr(a, "android_runtime", "auto")        # auto | emulator | device
    adb_bin = _find_adb()
    emu, pkg, old_serial = None, None, a.serial
    use_builtin = runtime == "emulator" or (runtime == "auto" and not (adb_bin and _device_connected(adb_bin, a.serial)))
    if use_builtin:
        import snapshot_android as sand
        try:
            emu, adb_bin = sand.start_builtin_emulator(getattr(a, "accept_android_licenses", False))
        except sand.AndroidRuntimeError as e:
            sys.exit(str(e))
        a.serial = emu.serial
        os.environ["ADBUTILS_ADB_PATH"] = adb_bin
    try:
        pkg = _run_apk_on_device(apk, out, a, adb_bin)
    finally:
        if emu is not None:
            if pkg:                                     # leave the virtual phone clean for the next run
                subprocess.run([adb_bin, "-s", emu.serial, "uninstall", pkg], capture_output=True)
            emu.stop()
            a.serial = old_serial


def _run_apk_on_device(apk, out: Path, a, adb_bin):
    try:
        import uiautomator2 as u2
    except ImportError:
        sys.exit("Missing dependency. Install it with: pip install uiautomator2")

    if not adb_bin:
        sys.exit("adb not found. Install Android Platform-Tools and add it to PATH, "
                 "or use the built-in emulator (--android-runtime emulator).")

    def adb(*args):
        cmd = [adb_bin] + (["-s", a.serial] if a.serial else []) + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    if adb("get-state").stdout.strip() != "device":
        sys.exit("No device/emulator connected (check 'adb devices').")

    before = set(adb("shell", "pm", "list", "packages", "-3").stdout.split())
    r = adb("install", "-r", "-g", apk)
    if "Success" not in r.stdout:
        sys.exit(f"APK installation failed:\n{r.stdout}{r.stderr}")
    after = set(adb("shell", "pm", "list", "packages", "-3").stdout.split())

    pkg = a.package
    if not pkg:
        new = after - before
        if new:
            pkg = new.pop().replace("package:", "")
        else:  # reinstall of an existing app; try aapt
            try:
                o = subprocess.run(["aapt", "dump", "badging", apk], capture_output=True, text=True).stdout
                pkg = re.search(r"package: name='([^']+)'", o).group(1)
            except Exception:
                if len(after) == 1:                     # only one third-party app is installed: that is it
                    pkg = next(iter(after)).replace("package:", "")
                else:
                    sys.exit("Could not detect the package name. Set it manually (Package field / --package).")
    print(f"Package: {pkg}")

    d = u2.connect(a.serial) if a.serial else u2.connect()
    W, H = d.window_size()
    avoid = re.compile(a.avoid, re.I) if a.avoid else None

    def parse():
        root = ET.fromstring(d.dump_hierarchy())
        sig, clicks, seen_b = set(), [], set()
        for n in root.iter("node"):
            at = n.attrib
            if at.get("package") != pkg:
                continue
            txt = re.sub(r"\d+", "#", at.get("text", ""))[:30]
            sig.add(f'{at.get("class")}|{at.get("resource-id")}|{txt}')
            if at.get("clickable") == "true" and at.get("enabled") == "true":
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", at.get("bounds", ""))
                if not m:
                    continue
                x1, y1, x2, y2 = map(int, m.groups())
                if (x2 - x1) * (y2 - y1) <= 0 or (x1, y1, x2, y2) in seen_b:
                    continue
                seen_b.add((x1, y1, x2, y2))
                label = at.get("text") or at.get("content-desc") or at.get("resource-id", "").split("/")[-1] or "item"
                if avoid and avoid.search(label):
                    continue
                clicks.append(((x1 + x2) // 2, (y1 + y2) // 2, label))
        digest = hashlib.md5("\n".join(sorted(sig)).encode()).hexdigest() if sig else ""
        return digest, clicks

    queue = deque([[]])  # each path = list of taps from the app's home screen
    seen, shots = set(), 0
    while queue and shots < a.max_screens and not STOP.is_set():
        path = queue.popleft()
        d.app_start(pkg, stop=True)
        time.sleep(a.delay + 1.5)
        for x, y, _ in path:
            d.click(x, y)
            time.sleep(a.delay)
        sig, clicks = parse()
        if not sig or sig in seen:
            continue
        seen.add(sig)
        shots += 1
        label = path[-1][2] if path else "home"
        name = f"{shots:03d}_{slugify(label, 30)}.png"
        d.screenshot(str(out / name))
        trail = " > ".join(p[2] for p in path) or "home"
        emit(a, out / name, trail, parent=(" > ".join(p[2] for p in path[:-1]) or "home") if path else None,
             title=label)
        print(f"[{shots}] {trail}")

        if a.scroll and d(scrollable=True).exists:
            prev = sig
            for i in range(a.scroll):
                d.swipe(W // 2, int(H * 0.75), W // 2, int(H * 0.25), 0.3)
                time.sleep(0.7)
                cur, _ = parse()
                if cur == prev or not cur:
                    break
                sname = f"{shots:03d}_{slugify(label, 30)}_scroll{i + 1}.png"
                d.screenshot(str(out / sname))
                emit(a, out / sname, f"{trail} (scroll {i + 1})", kind="scroll")
                prev = cur

        if len(path) < a.max_depth:
            for x, y, lb in clicks[: a.max_clicks]:
                queue.append(path + [(x, y, lb)])

    print(f"\nDone: {shots} screen(s) captured")
    return pkg


# ───────────────────────────── CLI ─────────────────────────────

def detect_mode(target):
    t = (target or "").lower()
    if t.endswith(".apk"):
        return "apk"
    if t.endswith((".exe", ".jar")):
        return "desktop"
    p = Path(target)
    if p.exists() and not (p.is_file() and t.endswith((".html", ".htm"))):
        return "unsupported"
    return "web"


def main():
    ap = argparse.ArgumentParser(
        description="Screenshot every page of a website or every screen of an app")
    ap.add_argument("target", nargs="?", default="",
                    help="website URL, or an .apk / .exe / .jar file")
    ap.add_argument("--mode", choices=["auto", "web", "apk", "desktop"], default="auto")
    ap.add_argument("-o", "--out", help="output folder")
    ap.add_argument("--gif", action="store_true",
                    help="also create one GIF per navigation flow (in <out>/flows), captioned with where you are")
    ap.add_argument("--gif-seconds", type=float, default=1.6, help="slideshow GIFs: seconds each step is shown")
    ap.add_argument("--gif-style", choices=["slideshow", "live"], default="slideshow",
                    help="slideshow = one frame per page; live = replay each flow in a real browser and record it "
                         "(websites only)")
    ap.add_argument("--gif-quality", choices=["compact", "standard", "high"], default="standard",
                    help="live GIFs: picture size and frame rate")
    ap.add_argument("--flow-steps", type=int, default=5, help="continue flows along links until they have this many steps")
    ap.add_argument("--max-flows", type=int, default=12, help="maximum number of GIF flows")
    ap.add_argument("--no-watermark", action="store_true", help="do not add the app icon watermark to GIFs")
    ap.add_argument("--max-depth", type=int, default=None, help="crawl depth (web 5, apk 3, desktop 2)")
    ap.add_argument("--delay", type=float, default=1.0, help="wait after each load/tap (seconds)")
    # web
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--mobile", action="store_true", help="emulate a phone (iPhone 13)")
    ap.add_argument("--keep-floating", action="store_true",
                    help="do not move/hide fixed or sticky bars (footers, cookie banners, chat bubbles)")
    ap.add_argument("--storage-state", help="Playwright session file for pages behind login")
    # apps (apk + desktop)
    ap.add_argument("--max-screens", type=int, default=40)
    ap.add_argument("--max-clicks", type=int, default=25, help="max tappable elements tried per screen")
    ap.add_argument("--avoid", default=DEFAULT_AVOID, help="regex of button labels that must never be tapped")
    ap.add_argument("--scroll", type=int, default=3, help="[apk] scroll steps captured per screen (0 = off)")
    ap.add_argument("--android-runtime", choices=["auto", "emulator", "device"], default="auto",
                    help="[apk] auto = a connected phone/emulator if there is one, otherwise SnapshotAll's built-in "
                         "headless emulator (downloaded once, about 2 GB); emulator = always the built-in one; "
                         "device = only an already connected device")
    ap.add_argument("--accept-android-licenses", action="store_true",
                    help="[apk] accept the Android SDK License (https://developer.android.com/studio/terms) "
                         "so the built-in emulator can be downloaded")
    ap.add_argument("--serial", help="[apk] adb device serial")
    ap.add_argument("--package", help="[apk] app package name (optional)")
    ap.add_argument("--attach", help="[desktop] attach to a running window whose title matches this regex")
    ap.add_argument("--visible", action="store_true",
                    help="[desktop] show the program's window and use the real mouse (default: run it in the "
                         "background, invisible, without touching your mouse)")
    a = ap.parse_args()

    mode = a.mode if a.mode != "auto" else ("desktop" if (a.attach and not a.target) else detect_mode(a.target))
    if mode == "unsupported":
        ap.error("unsupported target: give a website URL, or an .apk / .exe / .jar file")
    if not a.target and not (mode == "desktop" and a.attach):
        ap.error("a target is required")
    if a.max_depth is None:
        a.max_depth = {"web": 5, "apk": 3, "desktop": 2}[mode]

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = a.attach if (mode == "desktop" and a.attach and not a.target) else a.target
    stem = Path(label).stem if mode in ("apk", "desktop") and not a.attach else label
    out = Path(a.out or f"screenshots_{slugify(stem, 30)}_{stamp}")
    out.mkdir(parents=True, exist_ok=True)

    collected = []
    if a.gif:
        a.on_item = lambda path, label, parent, title, kind, links=None: collected.append(
            {"path": path, "label": label, "parent": parent, "title": title, "kind": kind, "links": links or []})
    if mode == "web":
        run_web(a.target, out, a)
    elif mode == "apk":
        run_apk(a.target, out, a)
    else:
        from snapshot_desktop import run_desktop
        run_desktop(a.target, out, a)
    if a.gif:
        import snapshot_gif as sgif
        flows = sgif.build_flows(collected, max_flows=a.max_flows, target_steps=a.flow_steps)
        watermark = not a.no_watermark

        def progress(n, total, crumbs):
            print(f"GIF {n}/{total}: {crumbs}")
        if a.gif_style == "live" and mode == "web":
            from snapshot_live import record_flows
            made = record_flows(flows, out / "flows", a, a.gif_quality, watermark, progress)
        else:
            if a.gif_style == "live":
                print("Live recording is available for websites only; creating slideshow GIFs instead.")
            made = sgif.render_flows(flows, out / "flows", a.gif_seconds, watermark, progress)
        print(f"Created {len(made)} GIF flow(s) in {out / 'flows'}")
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
