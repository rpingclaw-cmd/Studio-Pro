# CLAUDE.md — Studio Pro (repo: rpingclaw-cmd/Studio-Pro)

Desktop video editor (Windows first, Mac/Linux too) for the owner's spiritual talks and podcasts (Pranahuti Yoga,
channel "Divine Light Meditation"). It started as a copy of the "Studio" tab of the Daily Quote Cards website
(repo rpingclaw-cmd/PSM, which stays as it is) and grows into a full editor. The owner is **not a programmer**:
explain in plain words, give exact clicks, never leave half-finished state. Quoted words must stay word-for-word.

## Layout
- `desktop/` — Electron app (`main.js` starts the engine on a free 127.0.0.1 port with a random token, waits for
  `/api/hello`, opens `http://127.0.0.1:PORT/#engine=TOKEN`; `preload.js` exposes only `studioApp.pick/showItem/pathOf/version`).
  `STUDIO_SELFTEST=<dir>` makes the app check itself (engine on, editor open, no page errors), save a screenshot and quit (exit 0 = good).
- `engine/` — Studio Engine (Python 3.13, FastAPI/uvicorn, ffmpeg, DeepFilterNet, Silero VAD, YuNet). Same as PSM's engine plus:
  serves `ui/` at `/` (STUDIO_UI_DIR), token from `STUDIO_ENGINE_TOKEN`, tools from `STUDIO_BIN`, `--app` (no browser,
  exits when the app closes its stdin), hardware check redone when the tools change.
- `ui/` — the app's pages, plain JavaScript (no framework, no build step). Script order in `index.html`:
  core.js → studio.js → engine.js → studio2.js → studio3.js → studio4.js → editor.js → app.js.
  `editor.js` = the Resolve-style editor (timeline canvas, Media/Edit/Color/Fairlight/Deliver pages; it moves the guided
  fieldsets into its panels and back; hand cuts `STU.manualCuts`, blade points `STU.splits`, undo/redo wraps `stuSave`).
- `tools/bundle_runtime.py` — puts a private Python (python-build-standalone via uv) + engine libraries, FFmpeg 8.1
  (BtbN GPL) and deep-filter 0.5.6 into `desktop/build/` (not committed). Run it on the OS you build for.
- `.github/workflows/build.yml` — Linux engine tests; on Windows: bundle, engine tests, build the NSIS installer,
  install it silently, run the self-test (screenshot artifact). Tag `vX.Y.Z` → GitHub Release with the installer.

## Develop / test
- Engine tests: `cd engine && STUDIO_BIN=../desktop/build/bin STUDIO_DEEP_FILTER=../desktop/build/bin/deep-filter uv run pytest -q tests`
  (face tests need `STUDIO_TEST_FACE=/path/face.jpg`). Editor logic: `node tools/tests/test_studio2.mjs`, `node tools/tests/test_studio3.mjs`.
- App: `python3 tools/bundle_runtime.py` once, then `cd desktop && npm ci && npm start` (Linux headless:
  `STUDIO_SELFTEST=/tmp/st xvfb-run -a ./node_modules/.bin/electron --no-sandbox .`). Installer: `npm run dist:win|dist:linux`.

## Rules
1. Test before every push (engine tests with real ffmpeg; the app self-test; Playwright for UI flows). CI must stay green.
2. Few, well-tested commits; plain-English messages. Never commit secrets, keys or tokens.
3. Heavy media work runs on the owner's computer. Keep defaults light: owner's laptop is an i7-8550U (4 cores), 16 GB,
   Intel UHD 620 (Quick Sync) + NVIDIA 940MX (no NVENC), Windows 11, Smart App Control may be on (unsigned apps may be blocked).
4. Check documentation for versions, licences and APIs; don't guess. Licence: GPL-3.0 (FFmpeg GPL build, RVM GPL-3.0).

## Roadmap (owner's goals)
Phase 1 installable app ✓ → Phase 2 auto sync by words (transcribe both tracks, word-level match + lip-sync check), fill
picture gaps when audio started first, hear the recorder audio in the editor → Phase 3 background removal/replacement
(RVM, work in PSM branch `wip-background-framing`), own framing box / crop the lower body → Phase 4 pro tools (multi-track,
titles, transitions, keyframes, effects, mixer, colour wheels, multicam, export presets), local transcription (faster-whisper).
