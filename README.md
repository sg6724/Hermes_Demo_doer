# PathPilot

A teachable, interruptible, voice-first platform walkthrough agent.

PathPilot opens a real dedicated browser session, performs a previously
taught workflow live on a real website (test/dummy account only), narrates
every step by voice and text grounded in what's actually on screen, handles
customer questions mid-walkthrough without losing its place, and resumes
correctly instead of restarting.

## Two implementations in this repo

1. **Baseline / proof-of-concept** — `hubspot-live-walkthrough` skill
   (`D:\hermes\skills\hubspot-live-walkthrough\SKILL.md`), hardcoded to
   HubSpot. Preserved as-is, working reference. Its captured workflow lives
   at `D:\hermes\data\workflows\hubspot-review-inbound-lead.json`.
2. **Generic product** — `platform-walkthrough-agent` skill
   (`D:\hermes\skills\platform-walkthrough-agent\SKILL.md`). Website-agnostic
   engine; all site-specific detail lives in per-site data files, not in the
   skill itself. This is the one to extend for new sites.

Both share the same voice layer:
`D:\hermes\config\voice_config.json` (non-secret config) and
`D:\hermes\scripts\voice_narrator.py` (ElevenLabs streaming TTS + hidden
playback + narration logging).

## File layout (generic product)

```
D:\hermes\data\workflows\<site-id>\<workflow-id>.json   # one captured workflow
D:\hermes\data\workflows\index.json                     # registry of all workflows
D:\hermes\data\site-policies\<site-id>.json              # domains, profile, allowed/blocked actions
D:\hermes\data\runtime\<session-id>.json                 # durable per-session state
D:\hermes\skills\platform-walkthrough-agent\SKILL.md     # generic agent logic
```

Registered today: `hubspot` / `review-inbound-lead` (5 steps), and a
`flytbase` site policy with real-flight actions permanently blocked (no
workflow captured yet).

## Core commands

- `TEACH <site-id> <workflow-name>` — open the site's dedicated test
  browser profile, observe and capture a real 3-6 step workflow with
  semantic locators (never CSS-only), save it under
  `data\workflows\<site-id>\<workflow-id>.json`.
- `RUN <workflow-id>` — validate the active domain against the site
  policy, then perform the workflow live with voice+text narration from
  the actual visible UI, persisting state after every action.
- `QUESTION <customer-question>` — pause, inspect the live page, answer
  from visible facts first (label anything else as general guidance),
  speak + print the answer, then auto-resume from the preserved next
  step unless a route change was requested.
- `CONTINUE` — resume exactly from the saved next_step; never repeats
  completed actions or restarts.
- `PAUSE` — stop before the next browser action, keep all state, wait.
- `SKIP TO <step-id>` — explain what's being skipped, verify the
  destination is safe from the live page, only move if the site policy
  allows it; otherwise explain why not.
- `STOP` — stop immediately, preserve state, confirm no further action
  will occur.

## Adding a new website

See `D:\hermes\skills\platform-walkthrough-agent\references\adding-a-new-site.md`
for the full step-by-step guide: write the site policy first (allowed
domains, dedicated browser profile, allowed/prohibited/permanently-blocked
actions, confirmation rules, risk keywords), then TEACH a workflow, then
test with RUN in read-only mode.

## Voice-first behavior

- ON by default. Config: `D:\hermes\config\voice_config.json` (stock
  ElevenLabs voice "Sarah", `EXAVITQu4vr4xnSDxMaL`, non-secret).
- API key read only from `D:\hermes\.env` / `ELEVENLABS_API_KEY` env var —
  never displayed, logged, or committed. `.env`, browser profiles,
  generated audio, and runtime logs are all git-ignored (`.gitignore`).
- Before every browser action: inspect the live UI, generate one short
  (<35-word) sentence grounded in what's visible, print it, speak it via
  streaming ElevenLabs TTS piped directly into a hidden `ffplay` process
  (no visible window, no full-file wait — true low-latency streaming),
  then perform the action once playback finishes.
- Spoken answers to questions/route changes: under 60 words.
- Time-to-first-audio target: under 3 seconds. Benchmark with
  `python D:\hermes\scripts\voice_narrator.py --benchmark`.
- If voice fails, the walkthrough continues with text-only narration and
  logs `voice_failed: true` in `data\runtime\narration.jsonl` — it never
  halts the walkthrough.

## Safety

- Test/dummy accounts only.
- Default action mode is read-only everywhere.
- Reversible writes require explicit, specific, in-session user
  confirmation naming the exact action.
- Destructive/external/financial/bulk/publish/delete/archive/merge/send/
  import/export/irreversible actions are blocked by default regardless of
  confirmation, unless a site policy explicitly allow-lists a reversible
  version of one.
- A site policy's `permanently_blocked_actions` can never be unlocked by
  in-session confirmation — only by a human editing the policy file
  directly outside a live session (e.g. FlytBase's real-flight controls).
- Refuses to operate outside a site's `allowed_domains`.
- Never bypasses CAPTCHAs, 2FA, or security warnings.
