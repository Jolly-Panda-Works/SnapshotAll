"""Logic tests for background capture and the built-in Android runtime (no Windows, no emulator needed)."""
import argparse
import sys

import pytest
from PIL import Image

import snapshot_all as sa
import snapshot_android as sand
import snapshot_sandbox as sbox


@pytest.mark.parametrize("name,expected", [
    ("Setup_v2.exe", True), ("MyApp-Installer.exe", True), ("uninstall.exe", True), ("updater.exe", True),
    ("MyApp.exe", False), ("Setupify.exe", False), ("notepad.exe", False)])
def test_installer_names_are_detected(name, expected):
    assert sbox.looks_like_installer(f"C:/Programs/{name}") is expected


def test_blank_capture_is_detected():
    assert sbox.image_is_blank(Image.new("RGB", (80, 60), (0, 0, 0)))
    noisy = Image.effect_noise((80, 60), 90).convert("RGB")
    assert not sbox.image_is_blank(noisy)


def test_missing_runtime_parts_are_listed_and_licence_is_required(tmp_path):
    paths = sand.Paths(tmp_path)
    assert len(paths.missing()) == 4
    with pytest.raises(sand.AndroidRuntimeError) as e:
        sand.ensure_installed(paths, accept_licenses=False)
    assert "Android SDK License" in str(e.value)       # nothing is downloaded without consent
    assert not (tmp_path / "downloads").exists()


def test_runtime_paths_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPSHOTALL_ANDROID_DIR", str(tmp_path / "rt"))
    assert sand.runtime_dir() == tmp_path / "rt"
    paths = sand.Paths(tmp_path)
    env = paths.env()
    assert env["ANDROID_SDK_ROOT"] == str(paths.sdk) and env["ANDROID_AVD_HOME"] == str(paths.avd_home)
    assert env["ADBUTILS_ADB_PATH"] == str(paths.adb)
    assert paths.image_dir == paths.sdk / "system-images" / "android-34" / "google_apis" / "x86_64"
    jre_bin = paths.jre / "jdk-17" / "bin"
    jre_bin.mkdir(parents=True)
    (jre_bin / ("java.exe" if sys.platform == "win32" else "java")).write_text("")
    assert paths.java_home() == paths.jre / "jdk-17"
    assert paths.env()["JAVA_HOME"] == str(paths.jre / "jdk-17")      # our own Java wins over any installed one


def _apk_args(**kw):
    base = dict(android_runtime="auto", accept_android_licenses=False, serial=None, package=None, avoid="",
                max_screens=1, max_clicks=1, max_depth=1, delay=0, scroll=0, on_item=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_apk_uses_built_in_emulator_only_when_no_device(monkeypatch, tmp_path):
    calls = {}

    class FakeEmu:
        serial = "emulator-5584"

        def stop(self):
            calls["stopped"] = True

    monkeypatch.setattr(sa, "_find_adb", lambda: "adb")
    monkeypatch.setattr(sa, "_run_apk_on_device", lambda apk, out, a, adb: calls.setdefault("serial", a.serial) and "com.x")
    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: argparse.Namespace(stdout="", stderr=""))
    monkeypatch.setattr(sand, "start_builtin_emulator", lambda accept: (FakeEmu(), "sdk-adb"))

    monkeypatch.setattr(sa, "_device_connected", lambda adb, serial=None: True)    # a phone is connected
    sa.run_apk("x.apk", tmp_path, _apk_args())
    assert calls["serial"] is None and "stopped" not in calls

    calls.clear()
    monkeypatch.setattr(sa, "_device_connected", lambda adb, serial=None: False)   # nothing connected
    a = _apk_args()
    sa.run_apk("x.apk", tmp_path, a)
    assert calls["serial"] == "emulator-5584" and calls["stopped"] and a.serial is None

    calls.clear()
    with pytest.raises(SystemExit):                                                # "My device" with nothing connected
        monkeypatch.setattr(sa, "_run_apk_on_device", lambda *x: sys.exit("No device"))
        sa.run_apk("x.apk", tmp_path, _apk_args(android_runtime="device"))
    assert "stopped" not in calls


def test_cli_exposes_the_new_options():
    import subprocess
    out = subprocess.run([sys.executable, "snapshot_all.py", "--help"], capture_output=True, text=True).stdout
    for flag in ("--android-runtime", "--accept-android-licenses", "--visible"):
        assert flag in out
