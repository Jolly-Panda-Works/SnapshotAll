"""Run a Windows program in the background and capture its windows without showing them.

How it works (pure ctypes, no extra dependencies):

* The program is started inside a **Job Object**. When the job is closed or terminated, the program
  and every process it started are killed - nothing is left running. The job also blocks the program
  from reading / writing the clipboard, changing display settings, creating desktops or logging you off.
* Every top-level window the program creates is made **invisible and click-through**
  (layered, alpha 0, no-activate, tool window). It never appears on screen or in Alt+Tab, it can not take
  keyboard focus and the mouse goes straight through it. Windows UI Automation still sees it as a normal,
  on-screen window, so controls can be found and invoked without moving your mouse.
* Screenshots are taken with ``PrintWindow(PW_RENDERFULLCONTENT)``, which asks Windows to draw the window
  itself instead of copying screen pixels, so they work while the window is invisible.

This is containment, not a security sandbox: the program still runs with your user rights.
"""
import ctypes
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes

IS_WIN = sys.platform == "win32"

# Names that suggest a program installs / removes software or changes the system.
INSTALLER_RE = re.compile(r"(^|[\W_])(setup|install(er)?|unins(tall)?|uninst|update(r)?|patch|msi)([\W_]|$)", re.I)


def looks_like_installer(path) -> bool:
    """True when the file name suggests an installer / updater (running it would change this PC)."""
    from pathlib import Path
    return bool(INSTALLER_RE.search(Path(str(path)).stem))


def image_is_blank(img) -> bool:
    """True when a captured image is one flat colour (the black frame a failed capture returns)."""
    try:
        extrema = img.convert("RGB").resize((32, 32)).getextrema()
        return all(hi - lo < 4 for lo, hi in extrema)
    except Exception:
        return False


if IS_WIN:
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    g32 = ctypes.WinDLL("gdi32", use_last_error=True)

    u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.GetWindowLongW.restype = ctypes.c_long
    u32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    u32.SetWindowLongW.restype = ctypes.c_long
    u32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
    u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.UINT]
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    u32.GetWindowDC.argtypes = [wintypes.HWND]
    u32.GetWindowDC.restype = wintypes.HDC
    u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    u32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
    u32.ChildWindowFromPointEx.restype = wintypes.HWND
    g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g32.CreateCompatibleDC.restype = wintypes.HDC
    g32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g32.SelectObject.restype = wintypes.HGDIOBJ
    g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g32.DeleteDC.argtypes = [wintypes.HDC]
    g32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                              ctypes.c_void_p, wintypes.UINT]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.c_void_p]
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    GWL_STYLE, GWL_EXSTYLE = -16, -20
    WS_CAPTION, WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00C00000, 0x00080000, 0x00000020
    WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_APPWINDOW = 0x00000080, 0x08000000, 0x00040000
    SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE NOMOVE NOZORDER NOACTIVATE FRAMECHANGED
    LWA_ALPHA = 0x2
    SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 4, 7
    PW_RENDERFULLCONTENT = 0x2
    WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
    JobObjectBasicUIRestrictions, JobObjectBasicProcessIdList, JobObjectExtendedLimitInformation = 4, 3, 9
    LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    # clipboard read/write, display settings, desktops, log off / shut down
    UI_LIMITS = 0x02 | 0x04 | 0x10 | 0x40 | 0x80

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IOC(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _PIDLIST(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD), ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * 256)]

    class _BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    _EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_rect(hwnd):
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def set_invisible(hwnd, invisible=True):
    """Make a window fully transparent, click-through, non-activating and hidden from Alt+Tab (or undo it)."""
    ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if invisible:
        ex = (ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    else:
        ex &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
    u32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
    u32.SetLayeredWindowAttributes(hwnd, 0, 0 if invisible else 255, LWA_ALPHA)
    u32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)


def grab_window(hwnd):
    """Draw a window into a PIL image with PrintWindow (works while the window is invisible)."""
    from PIL import Image
    left, top, right, bottom = window_rect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError("window has no size")
    hdc = u32.GetWindowDC(hwnd)
    mem = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    old = g32.SelectObject(mem, bmp)
    try:
        if not u32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            raise RuntimeError("PrintWindow failed")
        bi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
            raise RuntimeError("GetDIBits failed")
        return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        g32.SelectObject(mem, old)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mem)
        u32.ReleaseDC(hwnd, hdc)


def post_click(hwnd, x, y):
    """Last-resort click that sends mouse messages straight to a window (the real mouse never moves)."""
    target = hwnd
    for _ in range(8):  # descend to the deepest child under the point
        pt = wintypes.POINT(x, y)
        u32.ScreenToClient(target, ctypes.byref(pt))
        child = u32.ChildWindowFromPointEx(target, pt, 0x1 | 0x2)  # skip invisible / disabled
        if not child or child == target:
            break
        target = child
    pt = wintypes.POINT(x, y)
    u32.ScreenToClient(target, ctypes.byref(pt))
    lp = (pt.y & 0xFFFF) << 16 | (pt.x & 0xFFFF)
    u32.PostMessageW(target, WM_LBUTTONDOWN, 1, lp)
    u32.PostMessageW(target, WM_LBUTTONUP, 0, lp)


class BackgroundProcess:
    """A program running inside a Job Object, with all of its windows kept invisible."""

    def __init__(self, cmd, cwd=None):
        if not IS_WIN:
            raise RuntimeError("Background capture of Windows programs works on Windows only.")
        self.job = k32.CreateJobObjectW(None, None)
        ext = _EXT()
        ext.BasicLimitInformation.LimitFlags = LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(self.job, JobObjectExtendedLimitInformation, ctypes.byref(ext), ctypes.sizeof(ext))
        ui = wintypes.DWORD(UI_LIMITS)
        k32.SetInformationJobObject(self.job, JobObjectBasicUIRestrictions, ctypes.byref(ui), ctypes.sizeof(ui))

        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWMINNOACTIVE      # start minimised without taking focus
        self.proc = subprocess.Popen(cmd, cwd=cwd, startupinfo=si, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        k32.AssignProcessToJobObject(self.job, int(self.proc._handle))
        self._seen = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    # ── processes of the job ──
    def pids(self):
        info = _PIDLIST()
        if not k32.QueryInformationJobObject(self.job, JobObjectBasicProcessIdList, ctypes.byref(info),
                                             ctypes.sizeof(info), None):
            return {self.proc.pid}
        return {info.ProcessIdList[i] for i in range(info.NumberOfProcessIdsInList)} or {self.proc.pid}

    def alive(self):
        """True while the program (or anything it started) is still running."""
        info = _PIDLIST()
        if not k32.QueryInformationJobObject(self.job, JobObjectBasicProcessIdList, ctypes.byref(info),
                                             ctypes.sizeof(info), None):
            return self.proc.poll() is None
        return info.NumberOfAssignedProcesses > 0

    def hwnds(self):
        """Visible top-level windows that belong to the job."""
        pids, found = self.pids(), []

        def cb(hwnd, _):
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and u32.IsWindowVisible(hwnd):
                found.append(int(hwnd))
            return True
        u32.EnumWindows(_EnumProc(cb), 0)
        return found

    # ── keep windows invisible ──
    def _hide_new(self):
        for hwnd in self.hwnds():
            if hwnd in self._seen:
                continue
            self._seen.add(hwnd)
            try:
                minimized = bool(u32.IsIconic(hwnd))
                set_invisible(hwnd, True)
                if minimized:                      # restore (still invisible) so it can be drawn and automated
                    u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            except Exception:
                pass

    def _watch(self):
        while not self._stop.is_set():
            try:
                self._hide_new()
            except Exception:
                pass
            time.sleep(0.03)

    def wait_for_window(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.hwnds():
                return True
            time.sleep(0.2)
        return False

    def snapshot(self, hwnd):
        """PIL image of one window. If the invisible window draws nothing, show it for an instant and retry."""
        try:
            img = grab_window(hwnd)
            if not image_is_blank(img):
                return img
        except Exception:
            pass
        set_invisible(hwnd, False)
        try:
            time.sleep(0.25)
            return grab_window(hwnd)
        finally:
            set_invisible(hwnd, True)

    def close(self):
        self._stop.set()
        try:
            k32.TerminateJobObject(self.job, 1)   # kills the program and everything it started
        finally:
            k32.CloseHandle(self.job)
