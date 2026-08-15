# Adding a new supported website to PathPilot

This is the step-by-step guide for onboarding a new website to the generic
`platform-walkthrough-agent`. It is the same content as the project README
at `D:\hermes\README.md`.

## 1. Choose a site_id

Pick a short, lowercase, hyphen-free identifier, e.g. `hubspot`, `flytbase`,
`salesforce`. This becomes the folder/file key used everywhere:
`data\workflows\<site-id>\`, `data\site-policies\<site-id>.json`.

## 2. Write the site policy FIRST

Create `D:\hermes\data\site-policies\<site-id>.json` before touching a
browser. Required fields:

- `site_id`, `display_name`
- `allowed_domains` — exact hostnames the agent may ever navigate to for
  this site. Anything else is refused.
- `browser_profile.path` — a NEW, dedicated, persistent Chromium
  `--user-data-dir` folder under `D:\hermes\data\<site-id>-demo-profile`
  (or similar). Never reuse another site's profile or the user's personal
  Chrome profile.
- `test_data_identifiers` — describe what dummy/test accounts or records
  look like so nobody confuses them with real production data.
- `default_action_mode` — almost always `"read_only"`.
- `allowed_actions` — the read-only/navigation verbs the agent may use
  freely (navigate, click_nav_link, search, open_record, view_tab, ...).
- `reversible_write_actions_requiring_confirmation` — actions that ARE
  allowed but only after the user explicitly confirms that specific action
  in that specific session. Leave empty (`[]`) if the site has none yet.
- `prohibited_actions` — always block these by default (delete, archive,
  merge, enroll, publish, import, export, bulk, send, payments, ...).
- `permanently_blocked_actions` — optional, for actions so dangerous no
  in-session confirmation can ever unlock them (see `flytbase.json`'s
  drone flight controls). Editing this list requires a human editing the
  file directly outside a live session.
- `confirmation_requirements` — plain-language description of what counts
  as valid confirmation for reversible writes, and a statement that
  prohibited/permanently-blocked actions are never unlockable.
- `site_specific_risk_keywords` — words/phrases that, if they appear in a
  user's instruction or a UI label, should make the agent extra cautious
  and double-check policy before proceeding.
- `captcha_and_security_policy` — always: never bypass CAPTCHAs, 2FA, or
  security warnings; pause and hand off to the human.

## 3. TEACH a workflow

Run `TEACH <site-id> <workflow-name>` (see `platform-walkthrough-agent`
SKILL.md for the full procedure). This launches the dedicated browser
profile, captures a real 3-6 step workflow with rich semantic locators
(role, visible text, location, nearby text, fallback text — never CSS-only),
and writes it to:

```
D:\hermes\data\workflows\<site-id>\<workflow-id>.json
```

Then register it in `D:\hermes\data\workflows\index.json`.

## 4. Test with RUN in read-only mode

Run `RUN <workflow-id>` and confirm every step narrates correctly from the
live page and stops appropriately before any write action that needs
confirmation.

## 5. Never skip the domain check

`RUN` always validates the active browser's current domain against
`allowed_domains` before acting — if a new site's policy is missing or
wrong, the agent will refuse rather than guess.
