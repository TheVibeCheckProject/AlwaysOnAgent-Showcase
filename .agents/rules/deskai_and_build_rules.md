# DeskAI Build, Environment & Update Integrity Rules

## 1. NEVER INSTALL OR DOWNLOAD GRADLE OR ANDROID SDK ON THIS MACHINE
- This host machine (HP EliteDesk 800 G6 Mini, `agent-mini`) is strictly the 24/7 AlwaysOnAgent desktop server daemon.
- It does **NOT** compile, sign, or build Android APKs.
- APK compilation and signing occur exclusively on the owner's primary development workstation, where the private `debug.keystore` and `google-services.json` reside.
- **NEVER** run `scoop install gradle`, `choco install`, download Android SDK command-line tools, or execute Gradle builds on this mini PC.
- AlwaysOnAgent changes to DeskAI are strictly code/model edits committed directly to git.

## 2. NEVER BUMP `web_dist/version.json` AHEAD OF THE COMPILED APK BINARY
- `web_dist/version.json` is the public OTA manifest checked by installed DeskAI companion apps.
- `web_dist/version.json` must **ALWAYS** match the exact `versionCode` present inside the committed `DeskAI.apk` binary.
- Bumping `versionCode` in `version.json` before the matching `.apk` binary has been compiled and checked into the repository causes an **infinite update loop**:
  1. The user's phone detects a newer version manifest.
  2. The phone downloads `DeskAI.apk`, which still contains the old version code.
  3. The phone installs the APK, but its installed version code remains unchanged.
  4. The app relaunches, sees the manifest is still higher, and immediately prompts to update again.
- Only update `web_dist/version.json` in the exact commit where the freshly built `DeskAI.apk` binary is replaced.

## 3. ALWAYS USE CACHE-BUSTING TIMESTAMPS ON GITHUB RAW DOWNLOADS
- GitHub's raw CDN (`raw.githubusercontent.com`) caches binary files for 5–10 minutes.
- All manifest checks and APK download URLs must include a dynamic timestamp parameter (`?t=${System.currentTimeMillis()}`) to prevent devices from receiving stale cached binaries.
