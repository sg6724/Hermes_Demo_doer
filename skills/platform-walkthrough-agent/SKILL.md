---
name: platform-walkthrough-agent
description: Use for PathPilot voice-first interruptible walkthroughs.
---

# PathPilot: Generic Platform Walkthrough Agent

Website-agnostic engine for teaching and replaying safe, voice-narrated
browser walkthroughs on any supported site. All site-specific detail
(domains, browser profile, workflow steps, locators, test data, allowed/
prohibited actions, confirmation rules, risk keywords) lives OUTSIDE this
skill in per-site data files. This skill only holds the generic mechanics.
Do not hardcode HubSpot, FlytBase, or any other site's specifics here.

This skill supersedes the site-specific `hubspot-live-walkthrough` skill
for new work, but that skill and its captured workflow are preserved
unmodified as a working baseline/reference — do not delete or edit them
when using this skill.

## File layout (website-agnostic)

- `D:\hermes\data\workflows\<site-id>\<workflow-id>.json` — one captured
  workflow (steps, semantic locators, narration goals, safety levels).
- `D:\hermes\data\workflows\index.json` — registry of all known workflows
  (site_id, workflow_id, title, paths, step_count, status).
- `D:\hermes\data\site-policies\<site-id>.json` — allowed_domains,
  browser_profile path, test_data_identifiers, default_action_mode,
  allowed_actions, reversible_write_actions_requiring_confirmation,
  prohibited_actions, permanently_blocked_actions (if any),
  confirmation_requirements, site_specific_risk_keywords,
  captcha_and_security_policy.
- `D:\hermes\data\runtime\<session-id>.json` — durable per-session state:
  site_id, workflow_id, status, browser pid/window_id, last_completed_step,
  current_step, next_step, action_mode, pending_confirmation,
  interruption_log, voice status. Template: `_TEMPLATE.session.json` in
  the same directory.
- Shared voice layer (reused from the baseline, not duplicated):
  `D:\hermes\config\voice_config.json` and `D:\hermes\scripts\voice_narrator.py`.

Registered workflows today: `hubspot` / `review-inbound-lead` (5 steps,
ported from the `hubspot-live-walkthrough` baseline capture) and the
`flytbase` site policy (no workflow captured yet — policy pre-authored with
permanent hard-blocks on all real-flight actions).

## Session lifecycle

1. Generate a `session_id` (e.g. `<site-id>-<workflow-id>-<timestamp>`).
2. Write a runtime state file at `D:\hermes\data\runtime\<session-id>.json`
   immediately, before any browser action, using the template shape.
3. After every single action (browser action, narration, interruption
   handling, confirmation, pause) — rewrite the runtime file. State must
   never live only in conversation memory; a fresh session/process must be
   able to CONTINUE correctly by reading this file alone.

## Core commands

### TEACH <site-id> <workflow-name>
1. Load or create `D:\hermes\data\site-policies\<site-id>.json`. If it does
   not exist, create a minimal one with the human's input before touching
   any browser (allowed_domains, browser_profile path, default_action_mode
   = read_only) — never teach against an unpoliced site.
2. Launch a dedicated, fresh Chromium process using
   `site_policy.browser_profile.path` as `--user-data-dir`. Never attach to
   the user's personal browser. Get pid + window_id via
   `computer_use(action='list_windows', pid=<pid>)`.
3. Observe the live UI and capture a real 3-6 step workflow. For EVERY
   step record: `goal`, `live_narration_goal` (what the spoken sentence
   should convey, not a fixed script), `expected_page_state`
   (title/url_pattern/verification text), `semantic_ui_evidence` (role,
   visible_text, location, nearby_text — never CSS selectors alone),
   `fallback_visible_text` (list of alternate ways to find the same
   element), `action_safety_level` (read_only /
   reversible_write_requires_confirmation / prohibited), and
   `verification` (how to confirm the step actually succeeded from the
   live page).
4. Never capture credentials, cookies, tokens, or other profile internals
   — only visible UI text/roles/state. Use `{{placeholder}}` templating for
   any test-data identifiers so the workflow file stays reusable.
5. Save the workflow to
   `D:\hermes\data\workflows\<site-id>\<workflow-id>.json` and add/update
   its entry in `D:\hermes\data\workflows\index.json`.

### RUN <workflow-id>
1. Resolve the workflow file via `index.json`; load its site policy.
2. **Validate the active browser's current domain against
   `site_policy.allowed_domains` before any action.** If it doesn't match,
   stop and explain — never proceed on an unlisted domain.
3. Create or resume the runtime session file.
4. For each step, in order, starting at `next_step`:
   a. Inspect the live UI (fresh capture).
   b. Compose one short narration sentence (<35 words) grounded in what's
      actually visible right now — never reuse the workflow file's
      recorded narration verbatim if the live page differs.
   c. Print the sentence in the transcript.
   d. Speak it via `voice_narrator.speak(text, step_id=...)` (see Voice
      section below); on failure continue with text-only narration.
   e. Perform the real browser action, but only if
      `action_safety_level` is `read_only`, or is a
      `reversible_write_requires_confirmation` step AND the user has just
      given explicit, specific confirmation naming that action in this
      session. Never perform a `prohibited` step.
   f. Verify against the step's `verification` criteria using a fresh
      capture.
   g. Update runtime state: `last_completed_step` = this step,
      `current_step` = this step, `next_step` = the following step id (or
      `"complete"` if this was the last step). Persist immediately.
5. After every browser/computer_use tool result, before starting the next
   action, run the Interruption Handling procedure below.

### QUESTION <customer-question>
1. Immediately pause — do not start or continue any browser action.
2. Runtime state must already have `last_completed_step`, `current_step`,
   `next_step` correctly set from the last persisted write; do not
   recompute or guess them.
3. Inspect the current live page (fresh capture) before answering.
4. Answer from visible current-page facts first. Only add general
   knowledge if the page doesn't contain the answer, and clearly label
   that part "general guidance" (not observed on this page).
5. Never guess. If the answer isn't knowable from the page or reliable
   general knowledge, say so plainly.
6. Speak the answer (<60 words) and print it as text.
7. If the question doesn't request a route change (no SKIP TO / STOP
   implied), automatically resume from the preserved `next_step` after
   answering — no separate CONTINUE needed.
8. If another question arrives before the next browser action actually
   runs, answer it too, keeping `next_step` unchanged until a real browser
   action executes.

### CONTINUE
Resume exactly from the persisted `next_step`. Never repeat
`last_completed_step`'s action and never restart the workflow from step 1.

### PAUSE
Stop before the next browser action, keep all runtime state exactly as
is, and wait. Do not advance `next_step`.

### SKIP TO <step-id>
1. Explain in one spoken+printed sentence what is being skipped and why
   the user asked for it.
2. Verify from a fresh live capture that the destination step's
   `expected_page_state`/preconditions are actually already satisfied —
   don't jump blind.
3. Check the site policy: only allow the skip if every step being skipped
   over contains no `reversible_write_requires_confirmation` or
   `prohibited` step that the user hasn't separately addressed, and if the
   destination step itself is not `prohibited`.
4. If safe and allowed, update `current_step`/`next_step` accordingly and
   persist. If not, explain specifically why it cannot be safely skipped
   (e.g. "step 4 logs a required activity check that step 6 depends on")
   and remain at the current step.

### STOP
Stop all browser actions immediately, persist state exactly as it is
(mark session `status: "stopped"`), and state clearly in narration + text
that no further action will occur without a new explicit command.

## Interruption handling (after every tool result)

Before issuing the next browser action, always:
1. Check for an out-of-band user/customer message in the latest tool
   result.
2. If present, treat it as a priority interruption over any queued next
   step.
3. Classify it as one of: QUESTION, PAUSE, CONTINUE, SKIP TO <step-id>,
   STOP, or unclear.
4. If unclear, ask exactly one concise clarifying question, remain
   paused, and do not guess intent.
5. Persist runtime state immediately after classifying/handling, even if
   the classification was "no interruption, proceeding."

## Voice-first behavior

- Voice is ON by default (`config/voice_config.json` `enabled: true`, or
  `PATHPILOT_VOICE` env var, or explicit override — same resolution order
  as the baseline skill).
- Reuse `D:\hermes\scripts\voice_narrator.py` exactly as-is — do not fork
  a second copy. Its `speak(text, step_id=...)` function:
  - reads the ElevenLabs key only from `D:\hermes\.env` /
    `ELEVENLABS_API_KEY` env var, never logs/prints/writes it;
  - streams synthesized audio directly into a hidden `ffplay` process's
    stdin (true streaming — no full-file wait) for low time-to-first-audio;
  - falls back to a temp file under `D:\hermes\data\runtime\audio\`
    (auto-deleted after playback) only if `ffplay` isn't found;
  - logs event metadata only (text, timestamps, voice id, word count,
    time_to_first_audio_seconds, duration, spoken, voice_failed, error) to
    `D:\hermes\data\runtime\narration.jsonl` — never audio bytes or
    secrets;
  - on any failure, sets `voice_failed: true` and returns normally
    (`spoken: False`) instead of raising — callers MUST continue the
    walkthrough with text-only narration in that case, never halt.
- Normal step narration: under 35 words. Spoken answers to questions/route
  changes: under 60 words.
- Never speak: selectors, tool names, JSON, internal reasoning, policy
  text, raw URLs, secrets, or other implementation detail — only the
  natural-language sentence meant for the end user.
- Speak once per meaningful step or interaction, not continuously — no
  rambling monologue. Silence between spoken sentences while performing
  the actual browser action is expected and correct.
- Time-to-first-audio target: under 3 seconds. Benchmark with
  `python D:\hermes\scripts\voice_narrator.py --benchmark` (speaks 3 short
  sentences once, reports TTFA + total time each) — don't run this
  repeatedly; once is enough to confirm health.

## Safety (generic, enforced regardless of site)

- Test/dummy accounts only, ever.
- Default `action_mode` is `read_only` for every site unless a specific
  step's `action_safety_level` is
  `reversible_write_requires_confirmation` AND the user has explicitly
  confirmed that exact action in the current session.
- Always block, regardless of confirmation: destructive, external,
  financial, bulk, publish, delete, archive, merge, send, import, export,
  and any other irreversible action — unless the site policy explicitly
  lists it under `reversible_write_actions_requiring_confirmation` (most
  won't).
- Refuse to act outside `site_policy.allowed_domains`. Re-check the active
  domain at RUN start and again if navigation ever leaves the expected
  domain mid-session.
- Never bypass CAPTCHAs, 2FA, permission prompts, or any security/warning
  dialog. Pause and hand control to the human.
- A site policy's `permanently_blocked_actions` (see `flytbase.json` for
  an example: launch, takeoff, arm, disarm, land, emergency_controls,
  real_flight_controls, mission_execution, delete, publish, bulk_action)
  can NEVER be unlocked by in-session user confirmation — only by a human
  editing that policy file directly, outside of a live walkthrough.

## Adding a new site

See `references/adding-a-new-site.md` for the step-by-step guide (also
duplicated as the project README).
