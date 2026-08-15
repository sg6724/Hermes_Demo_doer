# PathPilot — Teachable, Voice-First Platform Walkthrough Agent

## What I built

PathPilot is a Chrome Side Panel extension and local AI controller that can be **taught a short workflow on a real website**, then perform that workflow live in a visible browser while explaining each step. It is designed for solutions engineers who repeatedly demo the same product flows to different customers.

Unlike a fixed screen recording or a hard-coded browser script, PathPilot saves a reusable workflow with its goal, visible UI evidence, expected page state, safety level, and verification condition. This means the agent can replay the workflow later without being taught again.

## What is new

- **General website support:** PathPilot is not limited to HubSpot or FlytBase. When a user opens a new website, they can click **Teach this website**. Chrome shows a permission popup for that exact domain, then PathPilot captures and saves a safe 3–6 step workflow. Saved workflows appear later in the Run dropdown.
- **Real browser walkthroughs:** The agent drives a dedicated, visible Chromium session rather than pretending to click through a static script. Each website uses its own test-browser profile and domain policy.
- **Playwright integration:** I added a controller-owned Playwright executor for safe read-only workflow capture and replay. It allows only HTTPS navigation to exact policy-approved domains, uses a separate Chromium profile, captures visible same-origin links, and rejects cross-origin or unsafe navigation before the browser launches.
- **Three customer interaction modes:**
  - **Text ↔ Text:** the customer types and receives a written answer.
  - **Text → Voice:** the customer types while PathPilot narrates and answers aloud.
  - **Voice ↔ Voice:** the customer holds a talk button, asks naturally, sees the transcript, and hears PathPilot’s reply.
- **Voice-first experience:** ElevenLabs powers low-latency spoken narration and answers. The customer can interrupt PathPilot while it is speaking; playback stops immediately, the question is transcribed, and the answer is spoken back.
- **Interruption-aware state:** After each action, PathPilot persists the current, completed, and next workflow step. A customer question, pause, stop, or safe skip request does not restart the walkthrough or lose progress.

## Example demo workflow

The working reference workflow is on a dummy HubSpot portal: **Review an inbound lead and prepare a follow-up.** PathPilot opens Contacts, finds a test lead, opens the record, reviews the activity history, and previews the Task interface without saving anything. If interrupted with “Why is this a new inbound lead?”, it can answer from the recorded lifecycle-stage event and inbound website-request note, then resume.

## FlytBase focus and safety

PathPilot is ready to teach a FlytBase **test/sandbox** workflow such as reviewing drone-operation status and recent mission information. It is intentionally read-only: launch, takeoff, arm, disarm, land, mission execution, emergency controls, configuration changes, publishing, deletion, and bulk actions are permanently blocked. The agent never operates a real fleet.

## Security and validation

The extension is Manifest V3, the local controller is bound to `127.0.0.1`, and a local pairing token protects controller access. API keys, browser profiles, cookies, and runtime audio are never committed. Four Playwright safety tests currently pass, verifying HTTPS/domain enforcement, cross-origin rejection, read-only workflow construction, and rejection of unsafe teaching input.

**PathPilot turns a one-time demo into a reusable, interactive, voice-enabled product walkthrough.**
