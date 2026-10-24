<div align="center">

<img src="assets/logo.png" alt="SnapshotAll logo" width="130">

# SnapshotAll

**Find every page of a website or every screen of an app, preview them all, and save the ones you want.**

[![Build](https://github.com/Usef-Farahmand/SnapshotAll/actions/workflows/build.yml/badge.svg)](https://github.com/Usef-Farahmand/SnapshotAll/actions/workflows/build.yml)
[![Latest release](https://img.shields.io/github/v/release/Usef-Farahmand/SnapshotAll?display_name=tag&color=F97316)](https://github.com/Usef-Farahmand/SnapshotAll/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Usef-Farahmand/SnapshotAll/total?color=F97316)](https://github.com/Usef-Farahmand/SnapshotAll/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

[Download](https://github.com/Usef-Farahmand/SnapshotAll/releases/latest) ·
[Report a bug](https://github.com/Usef-Farahmand/SnapshotAll/issues/new?template=bug_report.yml) ·
[Request a feature](https://github.com/Usef-Farahmand/SnapshotAll/issues/new?template=feature_request.yml)

<img src="docs/screenshot-3-scan.png" alt="SnapshotAll - scan results with live previews" width="720">

<sub>Screenshots in this README use demo data.</sub>

</div>

---

## Table of contents

- [Why SnapshotAll?](#why-snapshotall)
- [How it works: four simple steps](#how-it-works-four-simple-steps)
- [Supported sources](#supported-sources)
- [GIF flows](#gif-flows)
- [Download and install (Windows)](#download-and-install-windows)
- [Command-line usage](#command-line-usage)
- [Code signing](#code-signing)
- [Run from source](#run-from-source)
- [Build the installer yourself](#build-the-installer-yourself)
- [Project structure](#project-structure)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing) · [Security](#security) · [Responsible use](#responsible-use) · [License](#license)

## Why SnapshotAll?

Designers, QA engineers, developers and writers often need a visual record of *every* page or screen of a product — for design reviews, regression checks, hand-offs or archiving. Doing that by hand is slow. SnapshotAll scans the source for you, shows a live preview of everything it finds, lets you pick what to keep, and saves it where you want.

## How it works: five simple steps

| | Step | What happens |
|---|---|---|
| 1 | **Source** | Choose **Website** or **App** and enter its address (a URL, or an `.apk` / `.exe` / `.jar` file). |
| 2 | **Settings** | Tune the options for that source: depth, limits, mobile mode, login session, and more. |
| 3 | **Scan** | A progress bar runs while SnapshotAll explores the whole source. Every page or screen it finds appears right away as a **preview card** you can select or deselect. |
| 4 | **Flows** | SnapshotAll draws the paths through your pages as a **flow graph**: one chain of pages per flow. Switch flows on or off, reorder or remove steps, add pages, or build your own. |
| 5 | **Save** | Choose what to save — **screenshots (PNG)**, **GIF flows**, or both — and where. |

<table>
<tr>
<td><img src="docs/screenshot-1-source.png" alt="Step 1 - Source"></td>
<td><img src="docs/screenshot-2-settings.png" alt="Step 2 - Settings"></td>
</tr>
<tr>
<td><img src="docs/screenshot-3-scan.png" alt="Step 3 - Scan"></td>
<td><img src="docs/screenshot-4-flows.png" alt="Step 4 - Flows"></td>
</tr>
<tr>
<td colspan="2"><img src="docs/screenshot-5-save.png" alt="Step 5 - Save"></td>
</tr>
</table>

The app has a modern dark-and-orange interface with a toolbar (**New capture**, **Settings**). **Settings** holds the default save location and an **About** section with the app version, the developer's name and website.

<div align="center"><img src="docs/screenshot-6-about.png" alt="Settings and About" width="300"></div>
 Screenshots are kept in a temporary folder during the scan and removed when you finish — nothing else is written to disk, and no log files are created.

## Supported sources

### Website
- Any online site or local development server (`example.com`, `https://www.example.com`, `localhost:3000`) — `https://` and `www.` are optional; redirects are followed automatically.
- Crawls same-site links, reads `sitemap.xml`, follows SPA hash routes (`#/about`).
- Auto-scrolls so lazy-loaded content appears; captures full-page images.
- Tidies floating elements so they don't land in the middle of a long screenshot: sticky and fixed footers go to the end of the page, cookie banners / chat bubbles / "back to top" buttons are hidden, fixed headers stay at the top (can be turned off).
- Desktop or **mobile** (iPhone 13) viewport.
- Pages **behind a login** via a Playwright session file.
- Uses **Microsoft Edge / Google Chrome** if installed (no download); otherwise downloads Chromium once.

### App
- **Android `.apk`** — runs the app on a **built-in virtual phone that SnapshotAll starts in the background** (no window, no Android Studio, no phone), taps through the UI and captures every unique screen (with optional scrolling). A phone or emulator that is already connected is used instead when there is one. The first run downloads the Android runtime once (about 2 GB).
- **Windows `.exe` / `.jar`** — runs the program **in the background**: its windows are invisible and click-through, your mouse and keyboard are never used, and the program is killed (with everything it started) when the scan ends. Buttons, tabs, menu items and links are pressed through Windows UI Automation and every new window state and dialog is captured with `PrintWindow`. You can still attach to a running window by title, or turn the window on in Settings (**Show the program's window**).
- Risky buttons (`delete`, `logout`, `pay`, `exit`, `save`, …) are skipped by default — configurable in Settings.

## GIF flows

Turn the scan into short animated walkthroughs. A **flow** is a path through the site (or app): an ordered list of pages, **two or more steps long**. Every flow becomes **one GIF**, and a caption underneath always tells you where you are.

<div align="center"><img src="docs/sample-flow.gif" alt="Slideshow GIF flow" width="560"> <img src="docs/sample-flow-live.gif" alt="Live-recorded GIF flow" width="560"><br><sub>Left: slideshow. Right: live recording.</sub></div>

**How flows are built.** Pages are linked into a graph from the real links on every page. A link inside the page content beats a menu link, and a page "above" another in the address (`/services` → `/services/design`) beats an unrelated one. Those paths are the base flows. Flows that are shorter than **Steps per flow** (default 5) are continued along real links, content links first and pages used by few flows first, so you get journeys such as `Home › Services › Design › About › Contact` instead of only "start page + one page".

**The Flows stage.** Before saving you review the flow graph — each flow is a chain of page tiles connected by arrows:

- switch a flow on or off, **remove** it, or create a **+ New flow**;
- **◀ ✕ ▶** under every tile moves a step, or removes it from that flow;
- **+ Add page** appends any selected page to a flow;
- **Regenerate** rebuilds the flows with a different *Steps per flow* / *Max flows*.

Flows are built from the pages you kept on the Scan step; if you deselect a page on the way, its flow simply carries on from the next one.

**Two styles** (chosen on the Save step):

| Style | What it is | Good for |
|---|---|---|
| **Slideshow** | One frame per page (the top of the page). Quick to create, small files. | Quick overviews, any source. |
| **Live recording** | SnapshotAll replays each flow in a real browser — scrolling through the page, moving a mouse pointer to the link, clicking it, cross-fading to the next page — and records it moment by moment. Quality: Compact / Standard / High. | Demos and documentation. **Websites only.** Larger files and slower. |

**Every frame** shows the page, the app icon as a **watermark** (can be turned off), and a caption with the whole path with the current step highlighted (`Home › Services › Design › About`), the page address (or tap trail for apps), a step counter and progress dots. Captions never contain Persian / Arabic text: such titles are replaced by the last part of the address, or by "Page 3" / "Screen 3".

GIFs are written to a `flows` folder inside your save location. On the command line use `--gif` (see the options below).

## Download and install (Windows)

1. Open the [**Releases**](https://github.com/Usef-Farahmand/SnapshotAll/releases/latest) page.
2. Download one of:
   - `SnapshotAll_Setup.exe` — installer (Start menu + desktop shortcut)
   - `SnapshotAll_Portable.zip` — no installation; unzip and run `SnapshotAll.exe`
3. Run it.

> **Windows SmartScreen warning?** The app is not code-signed yet. Click **More info → Run anyway**. Every release ships with `SHA256SUMS.txt` so you can verify your download — see [CODE_SIGNING.md](CODE_SIGNING.md) for details and how the warning will be removed.
> You can always review the source code and build the app yourself (see below).

## Command-line usage

The desktop app is built on the same engines as this CLI, which saves screenshots straight into a folder:

```bash
python snapshot_all.py <target> [options]
```

The mode is detected from the target: URL → website, `.apk` → Android, `.exe` / `.jar` → Windows app. Override it with `--mode {web,apk,desktop}`.

```bash
python snapshot_all.py https://example.com
python snapshot_all.py http://localhost:3000 --max-pages 100 --mobile
python snapshot_all.py app.apk --max-screens 60 --max-depth 4
python snapshot_all.py "C:\Program Files\MyApp\MyApp.exe"
python snapshot_all.py --attach "Untitled - Notepad"
```

| Option | Applies to | Default | Description |
|---|---|---|---|
| `--mode` | all | `auto` | Force `web`, `apk` or `desktop` |
| `-o, --out` | all | auto | Output folder |
| `--max-depth` | all | web 5, apk 3, desktop 2 | How deep to crawl |
| `--delay` | all | `1.0` | Seconds to wait after each load / tap |
| `--max-pages` | web | `50` | Maximum pages to capture |
| `--width`, `--height` | web | `1440`, `900` | Viewport size |
| `--mobile` | web | off | Emulate a phone (iPhone 13) |
| `--keep-floating` | web | off | Don't move or hide fixed / sticky bars (footers, cookie banners, chat bubbles) |
| `--gif` | all | off | Also create one GIF per flow in `<out>/flows` |
| `--gif-style` | all | `slideshow` | `slideshow`, or `live` (replay each flow in a real browser; websites only) |
| `--gif-quality` | web | `standard` | Live GIFs: `compact`, `standard` or `high` |
| `--gif-seconds` | all | `1.6` | Slideshow: seconds each step is shown |
| `--flow-steps` | all | `5` | Continue flows along links until they have this many steps |
| `--max-flows` | all | `12` | Maximum number of GIF flows |
| `--no-watermark` | all | off | Don't add the app icon to GIFs |
| `--storage-state` | web | – | Playwright session file for logged-in pages |
| `--max-screens` | apps | `40` | Maximum screens / window states |
| `--max-clicks` | apps | `25` | Max tappable elements tried per screen |
| `--avoid` | apps | see source | Regex of button labels that must never be tapped |
| `--scroll` | apk | `3` | Scroll steps captured per screen (`0` = off) |
| `--android-runtime` | apk | `auto` | `auto` = connected device if any, otherwise the built-in headless emulator; `emulator` = always the built-in one; `device` = only a connected device |
| `--accept-android-licenses` | apk | off | Accept the [Android SDK License](https://developer.android.com/studio/terms) so the built-in emulator can be downloaded |
| `--serial`, `--package` | apk | auto | `adb` device serial / app package name |
| `--attach` | desktop | – | Attach to a running window whose title matches this regex |
| `--visible` | desktop | off | Show the program's window and use the real mouse instead of running it in the background |

### Pages behind a login (website)

```bash
playwright codegen --save-storage=auth.json https://your-site.example/login
# log in in the window that opens, then close it
```

Then choose `auth.json` as the **session file** in step 2, or pass `--storage-state auth.json` on the command line.

## Code signing

Windows builds are not signed yet, so SmartScreen may warn on first launch. [CODE_SIGNING.md](CODE_SIGNING.md) explains why, how to verify a download, and the options for removing the warning (a low-cost open-source certificate, or Microsoft Store distribution).

## Run from source

Requirements: **Python 3.10+**.

```bash
git clone https://github.com/Usef-Farahmand/SnapshotAll.git
cd SnapshotAll
pip install -r requirements.txt
python snapshot_gui.py
```

On Windows you can also double-click `run_gui.bat` — it creates a virtual environment and installs everything on first run.

### APK requirements

Nothing to install. On the first APK scan SnapshotAll asks once, then downloads into `%LOCALAPPDATA%\SnapshotAll\android` a small Java runtime (Eclipse Temurin), Google's Android command-line tools, platform-tools, the emulator and one Android 14 system image (about 2 GB), and creates a virtual phone.

- The emulator needs **hardware virtualisation**: turn on Intel VT-x / AMD-V in the BIOS and enable the Windows feature **Windows Hypervisor Platform**.
- Prefer your own phone or emulator? Choose **My device** in Settings (or `--android-runtime device`); then `adb` must be in your `PATH` (or the copy bundled with `adbutils`) and the device must be visible in `adb devices`.

### Running programs safely

Capturing a `.exe` / `.jar` **runs it on your PC**. Background mode keeps it invisible, stops it from using the clipboard, changing display settings or logging you off, and kills it with all its child processes at the end - but it is containment, **not a security sandbox**: the program still runs with your user rights. Only capture programs you trust, and never an installer or updater (SnapshotAll warns when a file name looks like one). For untrusted files use a virtual machine or Windows Sandbox.

## Build the installer yourself

**GitHub Actions (recommended).** Push to `main` (or run *Build Windows EXE and Release* manually). The workflow builds the app with PyInstaller, creates the installer with Inno Setup and publishes both to a GitHub Release.

**Locally on Windows.** Double-click `build_exe.bat` → `dist\SnapshotAll\SnapshotAll.exe`. If [Inno Setup](https://jrsoftware.org/isinfo.php) is installed, it also creates `Output\SnapshotAll_Setup.exe`.

**Version numbers.** The default version is `_DEFAULT_VERSION` in `snapshot_common.py` — change it there to start a new release line (e.g. `1.5.0`). At build time `tools/set_version.py` decides the final version and injects the *same* string into the app header and Settings → About, the file properties of the exe, the installer and the release name: tags `vX.Y.Z` are used exactly, builds from `main` are `MAJOR.MINOR.<build number>`, other branches `MAJOR.MINOR.0-dev.<build number>`.

## Project structure

```
SnapshotAll/
├── snapshot_gui.py        # desktop app: wizard UI (customtkinter)
├── snapshot_all.py        # CLI + website and Android engines
├── snapshot_desktop.py    # Windows app engine (UI Automation)
├── snapshot_gif.py        # flows (link graph) and captioned slideshow GIFs
├── snapshot_live.py       # live recording: replays a flow in a browser and records it
├── snapshot_common.py     # shared helpers, app name / default version / author / website
├── tools/set_version.py   # decides the build version and injects it everywhere
├── assets/                # logo and icons
├── docs/                  # README images
├── installer.iss          # Inno Setup installer script
├── run_gui.bat            # run the GUI from source on Windows
├── build_exe.bat          # build the exe locally on Windows
├── requirements.txt
├── CODE_SIGNING.md        # code-signing policy and SmartScreen guide
└── .github/               # CI, release workflow, issue & PR templates
```

## Limitations

- **Websites:** pages reachable only through button clicks or form submissions are not discovered; CAPTCHAs and bot protection may block the crawler.
- **Android:** screens that need login or specific input can't be passed automatically — log in manually first, then run with the app's package name. Flutter apps, games and WebViews expose limited UI structure.
- **GIF flows:** a flow follows links between pages, which is not always the path a visitor would take. Live recording needs a website source and a browser, and its files are larger (a few hundred KB to several MB per flow, depending on the pages and the quality).
- **Windows apps:** relies on UI Automation, so custom-drawn interfaces (games, some Qt/Electron/canvas UIs) expose few controls. Launchers that start a second process may need **attach** mode instead. Windows only.
- No code signing yet, so Windows SmartScreen may warn on first launch.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No usable browser found" | SnapshotAll downloads Chromium automatically (internet required). Or install Microsoft Edge / Google Chrome. |
| "no hardware virtualisation" (APK) | Turn on VT-x / AMD-V in the BIOS and enable *Windows Hypervisor Platform*, then restart. |
| `adb not found` / no device (**My device**) | Start an emulator or connect a phone with USB debugging; check `adb devices`. |
| Windows app: the window is blank in the screenshot | Some GPU-drawn programs can't be painted while invisible. Turn on **Show the program's window** in Settings. |
| Windows app: nothing captured | Use **Attach**: start the app yourself, then enter part of its window title. |
| "Windows protected your PC" | Click **More info → Run anyway**. See [CODE_SIGNING.md](CODE_SIGNING.md). |
| Ctrl+V doesn't paste | Right-click the field → Paste, or use the **Paste** button. |
| The scan hit a problem | On the Scan page click **Details**, then **Copy details**, and include it in your bug report. |

## Contributing

Contributions are very welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) and follow the [Code of Conduct](CODE_OF_CONDUCT.md). Good first steps: try the app and [open an issue](https://github.com/Usef-Farahmand/SnapshotAll/issues/new/choose).

## Security

Found a vulnerability? Please do **not** open a public issue — see [SECURITY.md](SECURITY.md).

## Responsible use

Only capture websites and apps that you own or have explicit permission to test. Respect terms of service, `robots.txt` policies and applicable laws. Screenshots can contain sensitive data — store and share them carefully. The authors are not responsible for misuse.

## License

Released under the [MIT License](LICENSE) © 2026 [Usef Farahmand](https://github.com/Usef-Farahmand) · [useffarahmand.com](https://www.useffarahmand.com/).

## Acknowledgments

[Playwright for Python](https://playwright.dev/python/) · [uiautomator2](https://github.com/openatx/uiautomator2) · [pywinauto](https://pywinauto.readthedocs.io/) · [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) · [Pillow](https://python-pillow.org/) · [PyInstaller](https://pyinstaller.org/) · [Inno Setup](https://jrsoftware.org/isinfo.php)
