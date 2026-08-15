/**
 * PathPilot local-controller API client.
 *
 * Talks ONLY to http://127.0.0.1:5057 (the local PathPilot controller).
 * Every request carries the local pairing token (never the ElevenLabs key --
 * that key never leaves the controller process). The pairing token is
 * stored in chrome.storage.local, set once via the pairing/options page,
 * and is never logged or included in any message sent elsewhere.
 */

const CONTROLLER_BASE = "http://127.0.0.1:5057";

async function getPairingToken() {
  const { pathpilot_pairing_token } = await chrome.storage.local.get("pathpilot_pairing_token");
  return pathpilot_pairing_token || null;
}

async function setPairingToken(token) {
  await chrome.storage.local.set({ pathpilot_pairing_token: token });
}

async function apiFetch(path, options = {}) {
  const token = await getPairingToken();
  const headers = Object.assign({}, options.headers || {});
  if (token) headers["X-PathPilot-Token"] = token;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
  }
  const resp = await fetch(CONTROLLER_BASE + path, { ...options, headers });
  let data = null;
  try { data = await resp.json(); } catch (_) { /* non-JSON response */ }
  if (!resp.ok) {
    const err = new Error((data && data.error) || `HTTP ${resp.status}`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const PathPilotAPI = {
  getPairingToken,
  setPairingToken,

  async bootstrapPairing() {
    // The one unauthenticated call -- only works because the controller
    // only binds to 127.0.0.1 on this same machine.
    const resp = await fetch(CONTROLLER_BASE + "/api/pair/bootstrap", { method: "POST" });
    return resp.json();
  },

  async checkConnection() {
    try {
      await apiFetch("/api/sites");
      return true;
    } catch (_) {
      return false;
    }
  },

  listSites() {
    return apiFetch("/api/sites");
  },

  listWorkflows(siteId) {
    return apiFetch(`/api/workflows?site_id=${encodeURIComponent(siteId)}`);
  },

  workflowDetail(siteId, workflowId) {
    return apiFetch(`/api/workflow-detail?site_id=${encodeURIComponent(siteId)}&workflow_id=${encodeURIComponent(workflowId)}`);
  },

  validateDomain(siteId, url) {
    return apiFetch("/api/validate-domain", { method: "POST", json: { site_id: siteId, url } });
  },

  startTeach(activeTabUrl, displayName) {
    return apiFetch("/api/teach/start", { method: "POST", json: { active_tab_url: activeTabUrl, display_name: displayName } });
  },

  conversationalSignedUrl() {
    return apiFetch("/api/convai/signed-url", { method: "POST", json: {} });
  },

  startSession(siteId, workflowId, activeTabUrl, sessionId) {
    return apiFetch("/api/session/start", {
      method: "POST",
      json: { site_id: siteId, workflow_id: workflowId, active_tab_url: activeTabUrl, session_id: sessionId },
    });
  },

  pauseSession(sessionId) {
    return apiFetch("/api/session/pause", { method: "POST", json: { session_id: sessionId } });
  },

  resumeSession(sessionId) {
    return apiFetch("/api/session/resume", { method: "POST", json: { session_id: sessionId } });
  },

  stopSession(sessionId) {
    return apiFetch("/api/session/stop", { method: "POST", json: { session_id: sessionId } });
  },

  skipSession(sessionId, targetStep) {
    return apiFetch("/api/session/skip", { method: "POST", json: { session_id: sessionId, target_step: targetStep } });
  },

  getSession(sessionId) {
    return apiFetch(`/api/session/${encodeURIComponent(sessionId)}`);
  },

  getEvents(sessionId, since) {
    return apiFetch(`/api/events?session_id=${encodeURIComponent(sessionId)}&since=${since}`);
  },

  interrupt(sessionId) {
    return apiFetch("/api/interrupt", { method: "POST", json: { session_id: sessionId } });
  },

  askTextQuestion(sessionId, text, mode) {
    return apiFetch("/api/text-question", { method: "POST", json: { session_id: sessionId, text, mode } });
  },

  async askVoiceQuestion(sessionId, audioBlob, mode) {
    const token = await getPairingToken();
    const form = new FormData();
    form.append("audio", audioBlob, "question.webm");
    form.append("session_id", sessionId);
    form.append("mode", mode);
    const resp = await fetch(CONTROLLER_BASE + "/api/voice-question", {
      method: "POST",
      headers: token ? { "X-PathPilot-Token": token } : {},
      body: form,
    });
    const data = await resp.json();
    if (!resp.ok) {
      const err = new Error((data && data.error) || `HTTP ${resp.status}`);
      err.data = data;
      throw err;
    }
    return data;
  },
};
