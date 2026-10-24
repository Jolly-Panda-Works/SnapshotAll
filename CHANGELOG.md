# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Windows `.exe` / `.jar` run in the background.** The program is started inside a Windows Job Object (it and every process it starts are killed when the scan ends or SnapshotAll exits; clipboard, display settings, desktops and log-off are blocked). Its windows are made invisible and click-through, so nothing appears on screen, nothing takes focus and your mouse is never used. Controls are pressed through UI Automation patterns (mouse messages sent straight to the window as a last resort) and windows are captured with `PrintWindow`. Settings: *Show the program's window* (`--visible`) brings back the old visible mode.
- **Android `.apk` runs without any other app.** SnapshotAll downloads its own Java runtime, Android command-line tools, emulator and an Android 14 system image once (about 2 GB, after you accept the Android SDK License), creates a virtual phone, starts it headless, explores the APK on it, uninstalls the APK and shuts the emulator down. A connected phone / emulator is still used when there is one. New: `--android-runtime`, `--accept-android-licenses`, *Automatic / My device* switch.
- Confirmation before a program is run, with an extra warning when the file name looks like an installer or updater.
- Tests for installer detection, blank-capture detection, runtime paths and the emulator / device choice (`tests/test_background.py`).

### Changed
- `PRIVACY.md`: the built-in Android runtime downloads from Google and Adoptium (only when you start it).
- CI: pushes to `develop` and pull requests to `main` now build the app and keep the installer, portable zip and checksums as downloadable artifacts (14 days). Public GitHub Releases are still published only from `main` and from version tags. Release tags follow the app version (`v1.3.N`).

## [1.4.1] - 2026-10-03

### Fixed
- **Flows page scrolled badly** (content jumped and looked "cached"). Every flow card contained its own nested horizontal scroll area, and CustomTkinter hands the mouse wheel to those areas first: the page could not be scrolled with the pointer over the page tiles, and the tiles area moved up and down instead. The nested scroll areas are gone (tiles now wrap onto several rows), the tiles are built from light native widgets (about half as many widgets as before), and editing a flow redraws only that flow's card.
- **The version shown in the app header differed from the version of the installer.** The version number was written by hand in several places (`snapshot_common.py`, `installer.iss`, the release name). It now has a single source: `tools/set_version.py` decides it at build time and injects the same string into the app header and Settings > About, the file properties of `SnapshotAll.exe`, the installer (Add/Remove Programs) and the release name. Builds from `main` are `MAJOR.MINOR.<build number>`, tags `vX.Y.Z` are used exactly, and builds from other branches are `MAJOR.MINOR.0-dev.<build number>`.

### Changed
- `installer.iss` no longer contains a version number; compile it with `ISCC /DMyAppVersion=<version>` (`build_exe.bat` does this when Inno Setup is installed).

## [1.4.0] - 2026-10-03

### Added
- **Flows stage** between Scan and Save: the flow graph, one chain of page tiles per flow. Switch flows on or off, remove them, create new ones, move or remove steps, add pages, regenerate.
- **Longer flows.** Flows are built from the real links between pages (content links before menu links, URL hierarchy before unrelated pages) and continued along links until they reach *Steps per flow* (default 5), so flows are no longer always two steps.
- **Live recording** GIF style (websites): each flow is replayed in a real browser — scrolling, a mouse pointer moving to the link, a click, cross-fades — and recorded moment by moment. Quality: Compact / Standard / High.
- The app icon as a **watermark** on every GIF frame (switch in the Save step, `--no-watermark`).
- Command line: `--gif-style`, `--gif-quality`, `--flow-steps`, `--no-watermark`.
- Web engine reports every same-site link with its zone (menu or content).
- Tests for the flow graph, captions, watermark and live recording.

### Changed
- GIF captions never contain Persian / Arabic / Hebrew text; such titles fall back to the end of the address or to "Page N" / "Screen N".
- *Max flows* moved from the Save step to the Flows stage.

## [1.3.0] - 2026-10-02

### Added
- **GIF flows**: one animated GIF per navigation flow (a path from the start page / home screen to a page with nothing further down). Each frame shows the page with a caption of the whole path, the current step highlighted, the page address and a step counter.
- Save step: choose **Screenshots (PNG)**, **GIF flows** or both, with seconds per step, a maximum number of flows and a preview of the flows that will be created. The Save button now lives in the footer so it is always visible.
- Command line: `--gif`, `--gif-seconds`, `--max-flows`.
- The engines now report how each page was reached (parent page and link text; tap trail for apps).
- Unit tests for flows (`tests/test_flows.py`).

## [1.2.2] - 2026-10-01

### Fixed
- Website screenshots sometimes showed the footer (or a cookie banner, chat bubble, sticky bar, side bar) in the middle of the page. Full-page screenshots draw `position: fixed` / `sticky` elements relative to the first screen; they are now tidied before capture: sticky elements return to normal flow, fixed footers move to the end of the page, floating widgets are hidden and fixed headers stay at the top.
- Pages with smooth scrolling (`scroll-behavior: smooth`) were sometimes captured while still scrolling back to the top. Smooth scrolling is now disabled during capture, and the scroll pass waits until lazy-loaded content stops growing.

### Added
- **Tidy floating bars** switch in the website settings (`--keep-floating` on the command line to turn it off).
- Browser regression tests (`tests/`) that run in CI.

## [1.2.1] - 2026-09-30

### Added
- Developer website (<https://www.useffarahmand.com/>) in **Settings → About** and in the README.
- Windows title bar painted in the app's color (Windows 11; Windows 10 gets the dark title bar).
- `CODE_SIGNING.md`: code-signing policy, SmartScreen explanation and the available options (Microsoft Store, low-cost open-source certificate, SignPath Foundation).
- Releases now include `SHA256SUMS.txt`.

### Fixed
- Addresses typed without `https://` or `www.` (for example `example.com`) found only one page when the site redirected to another host form. The crawler now treats `example.com` and `www.example.com` as the same site, follows redirects (including to a different domain) and no longer captures the same page twice under `http://` and `https://`.
- Addresses typed without a scheme now start with `https://` (and fall back to `http://` if it cannot connect); `localhost`, private IPs and `host:port` addresses use `http://`.
- The application icon now appears in the Windows taskbar and title bar (CustomTkinter was replacing it with its own icon); the Settings window uses it too.

## [1.2.0] - 2026-09-30

### Added
- Wizard-style interface with four steps: **Source**, **Settings**, **Scan** and **Save**.
- Live scan page with a progress bar and a preview card for every page or screen found, with per-item selection, *Select all / none* and a *View* button.
- Toolbar with *New capture* and *Settings*; Settings contains the default save location and an About section with the app version and developer.
- Windows app capture (`.exe` / `.jar` / attach to a running window) via UI Automation.
- Project logo, application icon and installer icon.

### Changed
- Modern dark and orange design (rounded cards, sidebar stepper) built with CustomTkinter.
- Screenshots are kept in a temporary folder during the scan and only the selected ones are copied to the chosen save location.
- The app no longer writes log or index files; errors are shown in a *Details* panel with a *Copy details* button.

### Removed
- Folder / local-file source and its buttons. Local sites are captured through `localhost` URLs.

## [1.0.0] - 2026-09-30

### Added
- Website crawler with full-page screenshots, sitemap and SPA hash-route support.
- Local folder / HTML file support (served automatically).
- Mobile viewport mode and session-file support for pages behind login.
- Android APK explorer that captures every unique screen, with optional scrolling.
- English Windows desktop GUI with live log, Stop button, Copy log button and keyboard-layout-independent copy/paste.
- Automatic browser selection: Chromium → Microsoft Edge → Google Chrome, with on-demand Chromium download.
- GitHub Actions workflow that builds the exe and installer and publishes them as a GitHub Release.
- Open-source project files: license, contributing guide, code of conduct, security policy, issue and PR templates.
