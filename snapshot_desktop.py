"""
Windows desktop apps (.exe / .jar / attach to a running window).

Uses Microsoft UI Automation (via pywinauto) to find buttons, tabs, menu items and links,
presses them breadth-first and captures every window state it has not seen before.
For launched apps each branch restarts the app and replays the press path, like the APK explorer.

Launched programs run in the **background** by default (see snapshot_sandbox.py): inside a job object,
with invisible click-through windows, controls pressed through UI Automation and windows captured with
PrintWindow - nothing appears on screen and your mouse is never used. ``--visible`` restores the old
behaviour (a normal window and real mouse clicks). Attached programs always use the visible path.
"""
import hashlib
import re
import sys
import time
from collections import deque
from pathlib import Path

from snapshot_common import STOP, emit, slugify

try:
    import snapshot_sandbox as sandbox
except Exception:  # pragma: no cover
    sandbox = None

# Always avoided for desktop apps (on top of the user's --avoid list)
DESKTOP_EXTRA = r"exit|quit|close|minimi[sz]e|maximi[sz]e|restore|shut ?down|save|overwrite|send|submit"
CLICK_TYPES = {"Button", "TabItem", "MenuItem", "Hyperlink", "SplitButton"}
TITLEBAR_IDS = {"Close", "Minimize", "Maximize", "Restore", "SystemMenuBar", "TitleBar"}


def run_desktop(target, out: Path, a):
    if sys.platform != "win32":
        sys.exit("Windows app capture is only supported on Windows.")
    try:
        from pywinauto import Application
        from pywinauto.keyboard import send_keys
    except ImportError:
        sys.exit("Missing dependency. Install it with: pip install pywinauto")

    avoid = re.compile("|".join(x for x in (a.avoid, DESKTOP_EXTRA) if x), re.I)
    background = not a.attach and not getattr(a, "visible", False) and sandbox is not None
    state = {"app": None, "launched": False, "bg": None}
    if background:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
        print("Running the program in the background: its windows stay invisible and your mouse is not used.")

    def start():
        if a.attach:
            state["app"] = Application(backend="uia").connect(title_re=a.attach, timeout=15)
        elif background:
            argv = ["java", "-jar", str(target)] if str(target).lower().endswith(".jar") else [str(target)]
            state["bg"] = sandbox.BackgroundProcess(argv, cwd=str(Path(target).parent))
            state["launched"] = True
            if not state["bg"].wait_for_window(25):
                print("  ! The program did not open a window within 25 seconds.")
            time.sleep(a.delay + 2)
            return
        else:
            cmd = f'java -jar "{target}"' if str(target).lower().endswith(".jar") else f'"{target}"'
            state["app"] = Application(backend="uia").start(
                cmd, timeout=20, wait_for_idle=False, work_dir=str(Path(target).parent))
            state["launched"] = True
        time.sleep(a.delay + 3)

    def stop_app():
        if state["bg"] is not None:
            state["bg"].close()          # kills the program and everything it started
            state["bg"] = None
        elif state["launched"] and state["app"] is not None:
            try:
                state["app"].kill()
            except Exception:
                pass

    def press(ctrl, win):
        """Activate a control. In background mode this never touches the real mouse."""
        if not background:
            ctrl.click_input()
            return
        for pattern, method in (("iface_invoke", "Invoke"), ("iface_selection_item", "Select"),
                                ("iface_expand_collapse", "Expand"), ("iface_toggle", "Toggle")):
            try:
                getattr(getattr(ctrl, pattern), method)()
                return
            except Exception:
                continue
        r = ctrl.element_info.rectangle         # nothing supported: send mouse messages to the window
        sandbox.post_click(win.handle, (r.left + r.right) // 2, (r.top + r.bottom) // 2)

    def windows():
        """Visible top-level windows of the app, largest first."""
        try:
            if background:
                if state["bg"] is None:
                    return []
                shown = set(state["bg"].hwnds())
                wins = [UIAWrapper(UIAElementInfo(h)) for h in shown]
            else:
                wins = [w for w in state["app"].windows() if w.is_visible()]
        except Exception:
            return []
        def area(w):
            try:
                r = w.rectangle()
                return r.width() * r.height()
            except Exception:
                return 0
        wins = [w for w in wins if area(w) > 0]
        wins.sort(key=area, reverse=True)
        return wins

    def scan(win):
        """Return (signature, candidates) where candidates = [(key, idx, label, wrapper)]."""
        title = ""
        try:
            title = win.window_text() or ""
        except Exception:
            pass
        items, cands, counts = [], [], {}
        try:
            descs = win.descendants()
        except Exception:
            descs = []
        for c in descs[:1500]:
            try:
                ei = c.element_info
                if not ei.visible:
                    continue
                ctype = ei.control_type
                name = (ei.name or "").strip()
                aid = ei.automation_id or ""
                items.append(re.sub(r"\d+", "#", f"{ctype}|{name[:30]}|{aid}"))
                if ctype not in CLICK_TYPES or not ei.enabled or aid in TITLEBAR_IDS:
                    continue
                if avoid.search(f"{name} {aid}"):
                    continue
                r = ei.rectangle
                if r.width() <= 0 or r.height() <= 0:
                    continue
                key = (title, ctype, name, aid)
                idx = counts.get(key, 0)
                counts[key] = idx + 1
                cands.append((key, idx, name or aid or ctype, c))
            except Exception:
                continue
        sig = hashlib.md5((title + "\n" + "\n".join(sorted(set(items)))).encode("utf-8", "ignore")).hexdigest()
        return sig, cands

    def find(key, idx):
        """(control, window) for a remembered control, or None."""
        for w in windows():
            _, cands = scan(w)
            matches = [c for k, _, _, c in cands if k == key]
            if idx < len(matches):
                return matches[idx], w
        return None

    def shoot(win, name):
        if background:
            time.sleep(0.3)
            state["bg"].snapshot(win.handle).save(str(out / name))
            return
        try:
            win.set_focus()
        except Exception:
            pass
        time.sleep(0.3)
        win.capture_as_image().save(str(out / name))

    try:
        start()
        queue = deque([[]])
        seen, shots = set(), 0
        first = True
        while queue and shots < a.max_screens and not STOP.is_set():
            path = queue.popleft()
            try:
                if not first and path:
                    if state["launched"]:
                        stop_app()
                        start()
                    # attached apps can't be restarted: only single-click paths are tried, in place
                    elif len(path) > 1:
                        continue
                first = False

                ok = True
                for key, idx, _label in path:
                    found = find(key, idx)
                    if found is None:
                        ok = False
                        break
                    press(*found)
                    time.sleep(a.delay)
                if not ok:
                    continue

                label = path[-1][2] if path else "home"
                for win in windows():
                    sig, cands = scan(win)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    shots += 1
                    name = f"{shots:03d}_{slugify(label, 30)}.png"
                    shoot(win, name)
                    trail = " > ".join(p[2] for p in path) or "home"
                    emit(a, out / name, trail, parent=(" > ".join(p[2] for p in path[:-1]) or "home") if path else None,
                         title=label)
                    print(f"[{shots}] {trail}")
                    if len(path) < a.max_depth and (state["launched"] or not path):
                        for key, idx, lb, _c in cands[: a.max_clicks]:
                            queue.append(path + [(key, idx, lb)])
                    if shots >= a.max_screens:
                        break
                if not state["launched"]:
                    send_keys("{ESC}")  # dismiss menus/dialogs opened while attached
                elif background and not state["bg"].alive():
                    print("  ! The program closed itself; restarting for the next branch.")
            except Exception as e:
                print(f"  ! Error while exploring: {e}")

    finally:
        stop_app()  # always kill a program we started, even after an error or Stop
    print(f"\nDone: {shots} window state(s) captured")
