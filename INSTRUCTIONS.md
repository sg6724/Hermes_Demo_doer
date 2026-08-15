# PathPilot — Quick Start

Use this guide to run the PathPilot Chrome extension locally.

## 1. Prerequisites

- Windows, Google Chrome, and the Hermes Python environment installed.
- An ElevenLabs API key for voice modes.
- A **test or dummy account only** for any website you teach or run.
- `ffplay` available on your PATH for spoken audio playback. Text mode still works if audio playback is unavailable.

Never use PathPilot with a personal account, production customer data, or a real FlytBase fleet.

## 2. Add your ElevenLabs key

Create `D:\hermes\.env` if it does not already exist:

```text
ELEVENLABS_API_KEY=your_key_here
```

Do not paste this key into the extension, source code, chat, Git, or screenshots.

## 3. Start the local controller

Open PowerShell:

```powershell
cd D:\hermes\pathpilot_panel
& "C:\Users\Dell\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" app.py
```

Keep this terminal running. A successful start shows:

```text
Running on http://127.0.0.1:5057
```

The controller creates a local pairing token automatically. You do not need to copy this token manually.

## 4. Load the Chrome extension

1. Open Chrome using a dedicated PathPilot test profile, not your personal profile.
2. Go to `chrome://extensions`.
3. Turn on **Developer mode**.
4. Click **Load unpacked**.
5. Select this folder:

   ```text
   D:\hermes\extension\pathpilot
   ```

6. Open the PathPilot extension from Chrome’s toolbar. It opens in Chrome’s Side Panel.
7. Click **Connect PathPilot** or open **Extension options** and click **Auto-pair**.
8. Confirm the side panel shows **Ready**.

### Important: reload after source changes

Chrome does not automatically reload unpacked extension files. After any code change:

1. Return to `chrome://extensions`.
2. Click PathPilot’s reload icon.
3. Close and reopen the Side Panel.

## 5. Teach a workflow on a new website

1. Open the desired website in the dedicated test browser.
2. Open PathPilot. It detects the active website automatically.
3. Enter a workflow name, for example `Review drone operations status`.
4. Choose a conversation mode:
   - **Text ↔ Text** — typed conversation only.
   - **Text → Voice** — type questions; PathPilot speaks replies.
   - **Voice ↔ Voice** — hold the microphone button to speak; PathPilot replies aloud.
5. Click **Teach this website**.
6. Accept Chrome’s permission prompt for that exact website.
7. Click **Begin teaching conversation**.
8. Let PathPilot greet you, then describe and demonstrate 3–6 safe, visible steps.
9. Capture each verified read-only step and choose **Review and save workflow**.

PathPilot requests permission per website. It does not request unrestricted access to all websites.

## 6. Run a saved walkthrough

1. Open the same approved website in the dedicated test profile.
2. Open PathPilot.
3. Select a saved website and workflow.
4. Select the conversation mode.
5. Click **Start live walkthrough**.
6. Watch the browser operate live and listen to narration in voice modes.

During the walkthrough, type a question or use **Hold to Ask PathPilot**. PathPilot will show the transcript, answer, and continue from its saved next step.

## 7. Safe HubSpot demo

Use the saved dummy-account workflow:

```text
HubSpot → Review an inbound lead and prepare a follow-up
```

At the Activities step, ask:

> Why is Maya considered a new inbound lead?

PathPilot should answer from the visible Lead lifecycle event and website walkthrough-request note, then continue. It only opens the Task interface; it does not type or create a task.

## 8. FlytBase safety

Use FlytBase only with a test/sandbox workspace. PathPilot is read-only there and permanently blocks launch, takeoff, arm, disarm, land, emergency controls, mission execution, live-flight controls, configuration changes, publishing, deletion, and bulk actions.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Side Panel says Not connected | Ensure the controller terminal is running, then click Connect PathPilot / Auto-pair. |
| New button or UI is missing | Reload PathPilot from `chrome://extensions`, then reopen the Side Panel. |
| No spoken audio | Check system volume, `ffplay`, and `ELEVENLABS_API_KEY`; use Text ↔ Text as a fallback. |
| Microphone does not work | Allow microphone access when Chrome requests it; use the text box as a fallback. |
| Teach button does nothing | Reload the extension and confirm the current website permission was granted. |
| Browser refuses an action | This is normally a safety policy/domain restriction. Use a test account and teach a read-only workflow. |

## 10. Optional Playwright safety tests

Run these from PowerShell:

```powershell
cd D:\hermes\pathpilot_panel
& "C:\Users\Dell\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" -m unittest discover -s tests -v
```

The tests verify HTTPS/domain enforcement and read-only Playwright workflow safety.
