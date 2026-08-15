---
name: hubspot-live-walkthrough
description: Use for PathPilot live HubSpot walkthrough capture/replay.
---

# HubSpot Live Walkthrough (PathPilot)

Use this skill when the user asks you to teach, capture, rehearse, or replay a browser
walkthrough workflow against a real/live HubSpot test/demo portal for the PathPilot
project — a teachable, interruptible platform walkthrough agent.

## When to use
- User says "teach a workflow", "capture a HubSpot flow", "rehearse the lead
  review flow", or references an existing workflow JSON under
  `D:\hermes\data\workflows\*.json`.
- Any task that drives HubSpot's UI live via computer_use for demo/teaching
  purposes rather than performing real production CRM actions.

## Core principles

1. **Dedicated, isolated browser only.** Always use the persistent Chromium
   profile at `D:\hermes\data\hubspot-demo-profile` (or the project-specified
   path). Launch a brand-new Chrome process pointed at that
   `--user-data-dir`, never attach to the user's personal/default Chrome.
   Launch example (PowerShell via terminal tool, since this host's shell is bash/MSYS):
   ```
   powershell -NoProfile -Command "Start-Process -FilePath 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' -ArgumentList '--user-data-dir=D:\\hermes\\data\\hubspot-demo-profile','--no-first-run','--no-default-browser-check','--new-window','https://app.hubspot.com/login' -PassThru | Select-Object -ExpandProperty Id"
   ```
   Capture the returned PID, then use `computer_use(action='list_windows', pid=<pid>)`
   to get the window_id for that Chrome window. Use pid+window_id together on every
   subsequent computer_use call so actions target only this dedicated window.

2. **Never touch credentials or profile data.** Do not inspect, export, copy,
   or modify cookies, saved passwords, tokens, or any browser profile
   internals. Let the human log in manually when credentials are needed —
   stop and wait for them.

3. **Narrate before each action, from the live page — voice-first, ON by
   default.** For every real browser action:
   a. Inspect the current live HubSpot UI (fresh capture).
   b. Generate one short customer-facing sentence, grounded in what's
      actually visible — never a canned/pre-written script, under 35 words.
   c. Print that exact sentence in the Hermes transcript.
   d. Speak it aloud via `scripts/voice_narrator.py` (`speak()` /
      `--say`) — ElevenLabs streaming TTS through Windows default
      speakers, no visible player window — and wait for playback to finish.
   e. Only after speech finishes, perform the browser action.
   Interruption answers to the user follow the same speak-then-wait pattern
   before resuming at the preserved next step. Never speak selectors, tool
   names, JSON, internal reasoning, secret values, or policy text — only the
   one narration sentence.
   To disable voice for debugging: pass `--no-voice` to voice_narrator.py,
   set env var `PATHPILOT_VOICE=0`, or set `enabled: false` in
   `D:\hermes\config\voice_config.json`. Voice defaults to ON.

4. **Verify after each action.** Re-capture (`computer_use(action='capture',
   mode='som', pid=..., window_id=...)`) and confirm the resulting page
   state (title, URL, visible text) actually matches what the step intended
   before moving to the next step. Wait ~2s after navigations before
   re-capturing if the page is still loading.

5. **Capture rich, semantic locators — not just CSS.** For every step record:
   - visible text of the target element
   - role (button/link/tab/searchbox/etc.)
   - nearby/context text (what's next to it, its section heading)
   - expected resulting page title / URL pattern
   - a fallback locator description in case the primary text differs next time
   If the live UI's label differs from what you expected, use the real label
   you see and note the discrepancy in the captured JSON — don't force the
   expected wording.

6. **Hard safety stop before any write action.** Never actually create,
   save, send, delete, archive, merge, enroll, publish, import, export, use
   payments, or run bulk actions unless the user explicitly instructs that
   specific action in that specific message. When a task's purpose is to
   "explain what X would contain" — open the interface, describe the fields
   present, and STOP. Do not type into the primary content field and do not
   click Create/Save/Send.

## computer_use mechanics learned on this platform (Windows, Chrome)

- Background (default) delivery works for **clicks** on Chrome
  (`Chrome_WidgetWin_1`) reliably, but **not** for keystrokes or text typing —
  those return `background_unavailable` with `escalation.recommended:
  "foreground"`. Escalate exactly as the ladder instructs: retry the same
  `type`/`key` action with `delivery_mode='foreground'`. This briefly raises
  the window and restores focus afterward; it's expected and fine for a
  demo/teaching browser.
- Mouse **scroll** also requires `delivery_mode='foreground'` in this
  environment (background scroll is rejected the same way). Only escalate if
  you actually need to scroll to verify something not already visible —
  don't force it if the target content is already on screen.
- The typed "page" rung (`cua_browser_state` / `cua_browser_type`) needs
  `existing_profile` consent that isn't available by default in standard
  permission mode (`browser_consent_required`). Don't fight it — fall
  through to `delivery_mode='foreground'` for native input instead.
- After a click that navigates, the very next capture can still show the
  previous page mid-transition. Add a short `wait` (1-3s) then re-capture
  before asserting the step failed.
- Element indices from SOM captures shift between calls as the DOM changes;
  always capture fresh before clicking rather than reusing indices from an
  earlier capture.

## Voice narration (ElevenLabs)

- Config (non-secret): `D:\hermes\config\voice_config.json` — provider,
  stock `voice_id`/`voice_name`, model, output format, enabled flag,
  max words per utterance (35).
- Helper script: `D:\hermes\scripts\voice_narrator.py` — `speak(text,
  step_id=...)` synthesizes via ElevenLabs streaming TTS, plays through
  Windows default speakers with no visible player window (ffplay
  `-nodisp -autoexit`, hidden/no-window process), deletes the temp mp3
  immediately after playback, and appends one JSON line of metadata (text,
  timestamp, voice id, word count, spoken bool, duration — never audio
  bytes or the API key) to `D:\hermes\data\runtime\narration.jsonl`.
- Temp audio lives only under `D:\hermes\data\runtime\audio\` and is
  cleaned up automatically after each utterance.
- **API key handling:** read only from `D:\hermes\.env`
  (`ELEVENLABS_API_KEY=...`) or the process environment — never displayed,
  logged, echoed, or written to any file/report/error by this skill's
  tooling. `.env`, the HubSpot browser profile, generated audio, and
  runtime logs are all listed in `D:\hermes\.gitignore`.
- Voice test command (run manually, not automatically, when verifying
  setup): `python D:\hermes\scripts\voice_narrator.py --test`
- To run a walkthrough with voice off (debugging): add `--no-voice`, or set
  `PATHPILOT_VOICE=0` in the environment, or `enabled: false` in
  voice_config.json.

## Output format for captured workflows

Save captured workflows as JSON to `D:\hermes\data\workflows\<workflow-id>.json`
with this shape: `workflow_id`, `workflow_title`, `target_contact`,
`environment` (portal, browser session/profile path, capture date),
`safety_constraints` (list), and `steps[]` — each step has `step`, `name`,
`narration`, `action`, `target` (role/visible_text/location/nearby_text),
`expected_result` (page_title/url_pattern/verification text), and
`fallback_locators`. For a step that stops before a write action, add a
`stop_condition` field stating exactly what was NOT done.

## Pitfalls

- Don't assume a nav item is where a prior HubSpot version put it — always
  locate it live from the current SOM capture (labels/positions do shift
  between HubSpot portal configurations).
- A tooltip appearing (e.g. "Create a task") on hover/first click is not
  the same as the modal opening — some icon buttons need a second click if
  the first only surfaces a tooltip. Verify the actual modal/panel is
  visible before recording a step as complete.
- "Activities" or similar detail tabs may already be the default-selected
  tab on a record page — don't force an unnecessary click if the target
  content is already visible; instead record the step as `verify_visible`
  rather than `click`.
- Never fill in a task/note/email body text field even to "test" it, when
  the instruction is to stop before typing — the safety boundary is on the
  actual content field, not just the Create/Save button.
