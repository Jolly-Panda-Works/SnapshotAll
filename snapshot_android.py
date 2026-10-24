"""Built-in Android runtime for APK capture - no Android Studio, no phone, no other app to open.

On first use SnapshotAll downloads (one time, about 2 GB) into ``%LOCALAPPDATA%\\SnapshotAll\\android``:

* a small Java runtime (Eclipse Temurin, needed by Google's SDK tools),
* Google's Android command-line tools, platform-tools (adb) and emulator,
* one Android system image (``SYSTEM_IMAGE``),

creates a virtual phone and starts it **without a window** (headless). The APK is installed on it,
explored by the normal APK engine and the emulator is shut down at the end.

The emulator needs hardware virtualisation. On Windows enable *Windows Hypervisor Platform*
(Windows features) and virtualisation in the BIOS; without it Android is far too slow to be useful.
"""
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from snapshot_common import STOP

SYSTEM_IMAGE = "system-images;android-34;google_apis;x86_64"
AVD_NAME = "snapshotall"
EMULATOR_PORT = 5584                      # fixed, so it never collides with the user's own emulators
SERIAL = f"emulator-{EMULATOR_PORT}"
LICENSE_URL = "https://developer.android.com/studio/terms"

_CMDLINE = "https://dl.google.com/android/repository/commandlinetools-{plat}-11076708_latest.zip"
_JRE = "https://api.adoptium.net/v3/binary/latest/17/ga/{plat}/x64/jre/hotspot/normal/eclipse"
_PLAT = {"win32": ("win", "windows"), "linux": ("linux", "linux")}


class AndroidRuntimeError(RuntimeError):
    """Raised with a message that is safe to show to the user."""


def platform_names():
    try:
        return _PLAT[sys.platform]
    except KeyError:
        raise AndroidRuntimeError("The built-in Android runtime supports Windows (and Linux for development).")


def runtime_dir() -> Path:
    base = os.environ.get("SNAPSHOTALL_ANDROID_DIR")
    if base:
        return Path(base)
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(root) / "SnapshotAll" / "android"


def _exe(name, kind="bat"):
    """File name of an SDK tool on this OS (sdkmanager.bat / emulator.exe / adb.exe)."""
    if sys.platform == "win32":
        return name + (".bat" if kind == "bat" else ".exe")
    return name


class Paths:
    def __init__(self, root=None):
        self.root = Path(root) if root else runtime_dir()
        self.sdk = self.root / "sdk"
        self.jre = self.root / "jre"
        self.avd_home = self.root / "avd"

    @property
    def sdkmanager(self):
        return self.sdk / "cmdline-tools" / "latest" / "bin" / _exe("sdkmanager")

    @property
    def avdmanager(self):
        return self.sdk / "cmdline-tools" / "latest" / "bin" / _exe("avdmanager")

    @property
    def adb(self):
        return self.sdk / "platform-tools" / _exe("adb", "exe")

    @property
    def emulator(self):
        return self.sdk / "emulator" / _exe("emulator", "exe")

    @property
    def image_dir(self):
        return self.sdk.joinpath(*SYSTEM_IMAGE.split(";"))

    def java_home(self):
        for cand in self.jre.glob("*/bin/java*"):
            return cand.parent.parent
        return None

    def env(self):
        env = dict(os.environ)
        env.update(ANDROID_SDK_ROOT=str(self.sdk), ANDROID_HOME=str(self.sdk),
                   ANDROID_AVD_HOME=str(self.avd_home), ANDROID_USER_HOME=str(self.root / "user"),
                   ADBUTILS_ADB_PATH=str(self.adb))
        jh = self.java_home()
        if jh:
            env["JAVA_HOME"] = str(jh)
            env["PATH"] = str(jh / "bin") + os.pathsep + env.get("PATH", "")
        return env

    def missing(self):
        """Human-readable list of the parts that still have to be downloaded."""
        out = []
        if not self.java_home():
            out.append("Java runtime (about 50 MB)")
        if not self.sdkmanager.exists():
            out.append("Android command-line tools (about 150 MB)")
        if not self.adb.exists() or not self.emulator.exists():
            out.append("Android emulator and platform-tools (about 400 MB)")
        if not self.image_dir.exists():
            out.append("Android 14 system image (about 1.5 GB)")
        return out


def say(msg):
    print(msg, flush=True)


def download(url, dest: Path, label):
    """Download with a progress line every 10 %."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    say(f"Downloading {label}…")
    req = urllib.request.Request(url, headers={"User-Agent": "SnapshotAll"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, -1
        while True:
            if STOP.is_set():
                raise AndroidRuntimeError("Stopped.")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and done * 10 // total != last:
                last = done * 10 // total
                say(f"  {label}: {last * 10}%")


def unzip(zpath: Path, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dest)
        if sys.platform != "win32":                     # keep the executable bit on Linux
            for info in z.infolist():
                mode = info.external_attr >> 16
                if mode:
                    os.chmod(dest / info.filename, mode)


def run_tool(cmd, env, feed=None, timeout=3600):
    """Run an SDK tool, relay its output to the log and fail with a readable message."""
    p = subprocess.run([str(c) for c in cmd], input=feed, env=env, capture_output=True, text=True, timeout=timeout)
    tail = (p.stdout + p.stderr).strip().splitlines()[-6:]
    if p.returncode != 0:
        raise AndroidRuntimeError(f"{Path(str(cmd[0])).name} failed:\n" + "\n".join(tail))
    return p.stdout


def ensure_installed(paths: Paths, accept_licenses):
    """Download whatever is missing. Needs ``accept_licenses`` because the SDK has its own license."""
    missing = paths.missing()
    if missing and not accept_licenses:
        raise AndroidRuntimeError(
            "The Android runtime has to be downloaded once: " + "; ".join(missing) + ".\n"
            f"Downloading it means you accept the Android SDK License ({LICENSE_URL}). "
            "Confirm in the app, or pass --accept-android-licenses on the command line.")
    plat, jre_plat = platform_names()
    paths.root.mkdir(parents=True, exist_ok=True)
    tmp = paths.root / "downloads"
    if not paths.java_home():
        download(_JRE.format(plat=jre_plat), tmp / "jre.zip", "Java runtime")
        unzip(tmp / "jre.zip", paths.jre)
    if not paths.sdkmanager.exists():
        download(_CMDLINE.format(plat=plat), tmp / "cmdline-tools.zip", "Android command-line tools")
        unzip(tmp / "cmdline-tools.zip", paths.sdk / "_cmd")
        target = paths.sdk / "cmdline-tools" / "latest"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(paths.sdk / "_cmd" / "cmdline-tools"), str(target))
        shutil.rmtree(paths.sdk / "_cmd", ignore_errors=True)
    env = paths.env()
    if not paths.adb.exists() or not paths.emulator.exists() or not paths.image_dir.exists():
        say("Accepting the Android SDK licenses and installing the emulator and system image "
            "(this takes several minutes)…")
        run_tool([paths.sdkmanager, f"--sdk_root={paths.sdk}", "--licenses"], env, feed="y\n" * 60)
        run_tool([paths.sdkmanager, f"--sdk_root={paths.sdk}", "platform-tools", "emulator", SYSTEM_IMAGE], env)
    shutil.rmtree(tmp, ignore_errors=True)
    if not paths.adb.exists() or not paths.emulator.exists() or not paths.image_dir.exists():
        raise AndroidRuntimeError("The Android runtime installation did not finish correctly.")


def ensure_avd(paths: Paths):
    if (paths.avd_home / f"{AVD_NAME}.ini").exists():
        return
    paths.avd_home.mkdir(parents=True, exist_ok=True)
    say("Creating the virtual phone…")
    run_tool([paths.avdmanager, "create", "avd", "-n", AVD_NAME, "-k", SYSTEM_IMAGE, "-d", "pixel_5", "--force"],
             paths.env(), feed="no\n")


def check_acceleration(paths: Paths):
    """Fail early, with advice, when this PC can not run an x86 emulator fast enough."""
    p = subprocess.run([str(paths.emulator), "-accel-check"], env=paths.env(), capture_output=True, text=True,
                       timeout=60)
    text = p.stdout + p.stderr
    if p.returncode != 0 or re.search(r"not (installed|usable|available)|disabled", text, re.I):
        raise AndroidRuntimeError(
            "This PC has no hardware virtualisation available for the Android emulator.\n"
            "Turn on virtualisation (Intel VT-x / AMD-V) in the BIOS and enable the Windows feature "
            "'Windows Hypervisor Platform', then restart the PC and try again.\n" + text.strip()[-300:])


class Emulator:
    """A headless emulator that is killed when it is closed (or when SnapshotAll exits)."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self.proc = None
        self.job = None
        self.serial = SERIAL

    def _adb(self, *args, timeout=60):
        return subprocess.run([str(self.paths.adb), "-s", self.serial, *args], env=self.paths.env(),
                              capture_output=True, text=True, timeout=timeout)

    def start(self):
        cmd = [str(self.paths.emulator), "-avd", AVD_NAME, "-port", str(EMULATOR_PORT), "-no-window",
               "-no-audio", "-no-boot-anim", "-no-snapshot", "-gpu", "swiftshader_indirect"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        say("Starting the Android emulator in the background…")
        self.proc = subprocess.Popen(cmd, env=self.paths.env(), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        if sys.platform == "win32":
            self.job = _kill_with_us(self.proc)
        subprocess.run([str(self.paths.adb), "start-server"], env=self.paths.env(), capture_output=True)
        self._wait_boot()
        for key in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
            self._adb("shell", "settings", "put", "global", key, "0")
        self._adb("shell", "settings", "put", "global", "stay_on_while_plugged_in", "7")
        self._adb("shell", "input", "keyevent", "82")      # dismiss the lock screen
        say("Android emulator is ready.")

    def _wait_boot(self, timeout=420):
        end = time.time() + timeout
        last = 0
        while time.time() < end:
            if STOP.is_set():
                raise AndroidRuntimeError("Stopped.")
            if self.proc.poll() is not None:
                raise AndroidRuntimeError("The Android emulator stopped while starting "
                                          f"(exit code {self.proc.returncode}).")
            r = self._adb("shell", "getprop", "sys.boot_completed", timeout=15)
            if r.stdout.strip() == "1":
                return
            if time.time() - last > 30:
                say("  waiting for Android to finish starting…")
                last = time.time()
            time.sleep(3)
        raise AndroidRuntimeError("The Android emulator did not finish starting within 7 minutes.")

    def stop(self):
        if self.proc is None:
            return
        try:
            self._adb("emu", "kill", timeout=15)
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


def _kill_with_us(proc):
    """Windows: put the emulator in a job that is killed when SnapshotAll exits."""
    try:
        import snapshot_sandbox as sb
        import ctypes
        job = sb.k32.CreateJobObjectW(None, None)
        ext = sb._EXT()
        ext.BasicLimitInformation.LimitFlags = sb.LIMIT_KILL_ON_JOB_CLOSE
        sb.k32.SetInformationJobObject(job, sb.JobObjectExtendedLimitInformation, ctypes.byref(ext),
                                       ctypes.sizeof(ext))
        sb.k32.AssignProcessToJobObject(job, int(proc._handle))
        return job
    except Exception:
        return None


def start_builtin_emulator(accept_licenses=False, root=None):
    """Install what is missing, create the virtual phone and boot it. Returns (Emulator, adb_path)."""
    paths = Paths(root)
    ensure_installed(paths, accept_licenses)
    ensure_avd(paths)
    check_acceleration(paths)
    emu = Emulator(paths)
    try:
        emu.start()
    except BaseException:
        emu.stop()
        raise
    return emu, str(paths.adb)
