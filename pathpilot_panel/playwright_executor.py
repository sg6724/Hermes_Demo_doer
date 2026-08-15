"""Controller-owned, read-only Playwright execution for PathPilot.

This module never attaches to a person's Chrome profile. It launches a
separate Chromium profile and enforces HTTPS plus exact policy-host matching
before every navigation. Workflow files contain verified human-facing link
text and URLs, never browser selectors or page data.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse


class PlaywrightSafetyError(RuntimeError):
    """Raised before any browser action outside the approved policy boundary."""


def assert_same_origin_https(url: str, policy: dict[str, Any]) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise PlaywrightSafetyError("Only HTTPS navigation is allowed")
    if parsed.username or parsed.password or parsed.port:
        raise PlaywrightSafetyError("Credentialed or non-standard-port URLs are not allowed")
    if hostname not in set(policy.get("allowed_domains", [])):
        raise PlaywrightSafetyError("Navigation target is outside the approved website")
    return urlunparse(("https", parsed.netloc, parsed.path or "/", parsed.params, parsed.query, ""))


def build_readonly_navigation_workflow(
    *, policy: dict[str, Any], workflow_id: str, title: str, start_url: str,
    verified_steps: list[dict[str, str]],
) -> dict[str, Any]:
    assert_same_origin_https(start_url, policy)
    if not verified_steps or len(verified_steps) > 6:
        raise PlaywrightSafetyError("A teach capture requires 1 to 6 verified read-only steps")
    steps = []
    for index, step in enumerate(verified_steps, start=1):
        from_url = assert_same_origin_https(step["from_url"], policy)
        to_url = assert_same_origin_https(step["to_url"], policy)
        visible_link_text = step["visible_link_text"].strip()
        if not visible_link_text:
            raise PlaywrightSafetyError("A captured navigation needs visible link text")
        steps.append({
            "step_id": f"step-{index}",
            "step_number": index,
            "name": step["name"].strip(),
            "live_narration_goal": f"Open {visible_link_text} and confirm the destination is visible.",
            "expected_page_state": {"url": to_url, "verification_text": step["verification_text"].strip()},
            "semantic_ui_evidence": {"role": "link", "visible_text": visible_link_text},
            "fallback_visible_text": [visible_link_text],
            "action_safety_level": "read_only",
            "action": {"type": "navigate", "from_url": from_url, "to_url": to_url},
            "verification": step["verification_text"].strip(),
            "reference_capture": {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
        })
    return {
        "schema_version": "1.0",
        "site_id": policy["site_id"],
        "workflow_id": workflow_id,
        "workflow_title": title.strip(),
        "status": "draft",
        "execution_engine": "playwright_readonly",
        "action_mode": "read_only",
        "start_url": assert_same_origin_https(start_url, policy),
        "steps": steps,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _open_context(policy: dict[str, Any], *, headless: bool):
    from playwright.sync_api import sync_playwright

    profile = Path(policy["browser_profile"]["path"])
    profile.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=headless, viewport={"width": 1280, "height": 900},
        )
    except Exception:
        playwright.stop()
        raise
    return playwright, context


def _visible_same_origin_links(page, policy: dict[str, Any]) -> list[dict[str, str]]:
    anchors = page.locator("a[href]").evaluate_all("""
        elements => elements.map(a => ({
          text: (a.innerText || a.textContent || '').trim(), href: a.href,
          visible: !!(a.offsetWidth || a.offsetHeight || a.getClientRects().length)
        })).filter(a => a.visible && a.text && a.href)
    """)
    links = []
    for item in anchors:
        try:
            href = assert_same_origin_https(item["href"], policy)
        except PlaywrightSafetyError:
            continue
        links.append({"text": item["text"], "href": href})
    return links


def capture_readonly_navigation(
    *, policy: dict[str, Any], title: str, start_url: str, requested_link_text: str,
    headless: bool = False,
) -> dict[str, Any]:
    """Capture one verified same-origin visible-link navigation in isolated Chromium."""
    start_url = assert_same_origin_https(start_url, policy)
    requested = requested_link_text.strip()
    if not requested:
        raise PlaywrightSafetyError("Choose a visible link to demonstrate")
    playwright, context = _open_context(policy, headless=headless)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
        links = _visible_same_origin_links(page, policy)
        candidate = next((link for link in links if link["text"].casefold() == requested.casefold()), None)
        if candidate is None:
            candidate = next((link for link in links if requested.casefold() in link["text"].casefold()), None)
        if candidate is None:
            raise PlaywrightSafetyError(f"No safe visible link matching '{requested}' was found")
        # Navigate only to the already policy-validated same-origin destination.
        page.goto(candidate["href"], wait_until="domcontentloaded", timeout=30_000)
        destination = assert_same_origin_https(page.url, policy)
        visible_text = page.locator("body").inner_text(timeout=10_000).strip()[:400]
        verification = f"Navigated to {urlparse(destination).path or '/'}; destination page body is visible."
        return build_readonly_navigation_workflow(
            policy=policy,
            workflow_id=f"{policy['site_id']}-{'-'.join(re.findall(r'[a-z0-9]+', title.lower()))[:48]}",
            title=title,
            start_url=start_url,
            verified_steps=[{
                "name": f"Open {candidate['text']}", "from_url": start_url, "to_url": destination,
                "visible_link_text": candidate["text"], "verification_text": verification,
            }],
        ) | {"capture_summary": {"destination_url": destination, "visible_page_excerpt": visible_text}}
    finally:
        context.close()
        playwright.stop()


def replay_readonly_workflow(*, policy: dict[str, Any], workflow: dict[str, Any], headless: bool = False) -> list[dict[str, str]]:
    """Replay persisted read-only URL steps, validating policy before every action."""
    playwright, context = _open_context(policy, headless=headless)
    results: list[dict[str, str]] = []
    try:
        page = context.pages[0] if context.pages else context.new_page()
        for step in workflow.get("steps", []):
            if step.get("action_safety_level") != "read_only" or step.get("action", {}).get("type") != "navigate":
                raise PlaywrightSafetyError("Replay refused: workflow contains a non-read-only navigation step")
            target = assert_same_origin_https(step["action"]["to_url"], policy)
            page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            actual = assert_same_origin_https(page.url, policy)
            results.append({"step_id": step["step_id"], "url": actual, "verification": step["verification"]})
        return results
    finally:
        context.close()
        playwright.stop()
