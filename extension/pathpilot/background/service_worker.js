/**
 * PathPilot background service worker (Manifest V3).
 *
 * Responsibilities:
 *   - Opens the side panel when the toolbar icon is clicked.
 *   - Tracks which tab belongs to the dedicated PathPilot Chromium session
 *     so the side panel only ever acts on that tab (never a random tab).
 *   - Provides the active tab's URL to the side panel on request, so the
 *     side panel can ask the local controller to validate the domain
 *     before enabling Start -- this worker does not itself decide
 *     anything, it only relays tab state.
 *
 * This worker never talks to ElevenLabs and never sees the pairing token
 * or any secret -- all of that lives in the side panel + controller-client.
 */

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

chrome.action.onClicked.addListener(async (tab) => {
  if (tab.windowId != null) {
    await chrome.sidePanel.open({ windowId: tab.windowId });
  }
});

// Relay: side panel asks "what tab/window am I attached to and what's its URL"
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "PATHPILOT_GET_ACTIVE_TAB") {
    chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      sendResponse({
        url: tab?.url || null,
        tabId: tab?.id || null,
        windowId: tab?.windowId || null,
        title: tab?.title || null,
      });
    });
    return true; // keep sendResponse alive for the async chrome.tabs.query
  }
});
