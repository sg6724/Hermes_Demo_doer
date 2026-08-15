# PathPilot Chrome Extension

Customer-facing Manifest V3 side-panel frontend for PathPilot. The extension does not replace the local PathPilot controller, Hermes, workflow packs, site policies, durable runtime state, or ElevenLabs integration. It presents a persistent Chrome Side Panel that talks only to the localhost controller.

## Architecture

- Extension: `D:\hermes\extension\pathpilot`
- Local controller: `D:\hermes\pathpilot_panel\app.py`
- Controller URL: `http://127.0.0.1:5057` — bound only to localhost.
- Reasoning/workflow brain: Hermes. The extension never decides or invents answers.
- ElevenLabs: server-side-only speech-to-text and PathPilot TTS. No API key is present in the extension.

## Modes

- Development (default): `PATHPILOT_MODE=development`; standard local controller behavior and detailed event labels.
- Demo: start the controller with `PATHPILOT_MODE=demo`; same security boundary, streamlined judge-facing UI/event presentation.

## Start the controller

From a terminal:

```bash
cd D:/hermes/pathpilot_panel
"C:/Users/Dell/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe" app.py
```

The controller prints its local pairing-token file location on startup. It generates the token once in `D:\hermes\pathpilot_panel\.pairing_token`. This is a local shared secret, distinct from the ElevenLabs API key. Do not commit or share it.

## Load as an unpacked extension into the dedicated HubSpot Chromium profile

1. Launch the dedicated Chromium instance only, using its persistent test profile:

   ```bash
   "C:/Program Files/Google/Chrome/Application/chrome.exe" --user-data-dir="D:\hermes\data\hubspot-demo-profile"
   ```

   If Chrome is installed elsewhere, use that Chrome executable. Do not load PathPilot into a personal Chrome profile.

2. In that Chromium window, open `chrome://extensions`.
3. Turn on **Developer mode** in the top-right.
4. Select **Load unpacked**.
5. Choose exactly: `D:\hermes\extension\pathpilot`
6. Pin PathPilot from the Extensions menu if desired, then click its toolbar icon. This opens its persistent Side Panel.
7. Click **Details** for PathPilot, then **Extension options**.
8. Click **Auto-pair** while the local controller is running. The controller creates/returns a localhost-only pairing token and the extension stores it in `chrome.storage.local`.
9. Open a permitted HubSpot test-portal tab in the same dedicated profile. The side panel should show **Connected**.
10. Choose **HubSpot (test portal)** → **Review an inbound lead and prepare a follow-up** → your conversation mode. Start is disabled until the active tab’s domain passes the selected site policy.

## Reloading after local source changes

Chrome does not hot-reload unpacked extensions. After editing any file under `D:\hermes\extension\pathpilot` (including the side panel HTML/JS/CSS), you must:

1. Open `chrome://extensions` in the dedicated test-profile Chromium window.
2. Click **Reload** on the PathPilot card (or toggle it off/on).
3. Close and reopen the Side Panel (click the toolbar icon again) so it picks up the reloaded scripts.

Editing files alone does nothing until you reload — a stale side panel will keep running the old JS even if the HTML on disk has changed. Bump `version` in `manifest.json` on any user-visible fix so `chrome://extensions` clearly shows a new build is loaded.

If Teach Mode ever shows a UI error like "missing element(s) ...", it means the side panel HTML and JS have drifted out of sync (usually because Reload wasn't clicked after an edit) — reload the extension and reopen the panel before reporting a bug.

## Permissions

The manifest requests only:

- `sidePanel`: persistent PathPilot UI.
- `storage`: retains only local extension settings/pairing token.
- `activeTab`, `tabs`: reads the active dedicated-browser tab URL/title for local domain validation; no page injection.
- Host permissions: `http://127.0.0.1:5057/*` and the explicitly approved HubSpot portal domains.
- Optional host permissions: new sites must request access only when taught/approved. The extension does not use `<all_urls>`.

## Privacy and voice behavior

In Voice ↔ Voice mode, the Side Panel records microphone audio only while the customer holds **Hold to Ask PathPilot**. Browser capture requests echo cancellation, noise suppression, and auto gain control. The audio is posted once to the localhost controller, passed to ElevenLabs STT, and discarded immediately after transcription. It is never written to disk unless `PATHPILOT_DEBUG_KEEP_AUDIO=1` is explicitly set on the local controller.

A hold while PathPilot speaks calls the local `/api/interrupt` endpoint first; it stops local `ffplay` output before capture begins (barge-in). The browser extension does not have access to the ElevenLabs key, HubSpot cookies, passwords, page data, or screenshots.

## Current implementation status

The extension UI, MV3 service worker, local-controller API client, pairing page, domain guard, workflow selector, conversation modes, microphone flow, text fallback, durable event feed, and safety panel are implemented. Before judging, run the browser acceptance test described in the product brief after loading the extension and pairing it.
