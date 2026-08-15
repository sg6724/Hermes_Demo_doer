"""
PathPilot Local Controller (v2) -- secure local backend for the PathPilot
Chrome extension frontend.

Architecture, preserved from v1 and extended, not replaced:
  - Hermes remains the ONLY workflow/decision-making brain. QUESTION answers
    are produced by shelling out to the real `hermes` CLI, grounded in the
    live-page facts captured for the workflow's current step. "Start
    walkthrough" spawns a real, independent `hermes chat` agent process
    (with its own computer_use/browser tool access) that executes the
    workflow live and reports narration/verification/safety events back to
    this controller via /api/events and /api/speak -- this controller never
    invents narration or clicks anything itself.
  - ElevenLabs is used ONLY for: (1) customer speech-to-text, (2) PathPilot
    speech output. It never receives page content, cookies, credentials,
    or screenshots.
  - The ElevenLabs API key is read only from D:\\hermes\\.env /
    ELEVENLABS_API_KEY env var, server-side, and is never sent to the
    extension, logged, or embedded in any response.
  - New in v2: a local pairing token (separate secret from the ElevenLabs
    key) that the extension must present on every request. Generated on
    first run, stored locally, never logged, never sent to any third party.
  - New in v2: workflow/site registry endpoints, live domain validation,
    a persisted event queue (narration/transcript/answer/verification/
    safety) for reliable delivery to the extension's conversation feed, and
    session lifecycle endpoints (start/pause/resume/stop/skip) matching the
    generic platform-walkthrough-agent commands.

Run:
    "C:/Users/Dell/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe" app.py
Binds to 127.0.0.1 only -- never 0.0.0.0. Not reachable from outside this
machine.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory

REPO_ROOT = Path(r"D:\hermes")
ENV_PATH = REPO_ROOT / ".env"
VOICE_CONFIG_PATH = REPO_ROOT / "config" / "voice_config.json"
RUNTIME_DIR = REPO_ROOT / "data" / "runtime"
AUDIO_DIR = RUNTIME_DIR / "audio"
NARRATION_LOG_PATH = RUNTIME_DIR / "narration.jsonl"
VOICE_TRANSCRIPT_LOG_PATH = RUNTIME_DIR / "voice_transcripts.jsonl"
DEBUG_AUDIO_RETENTION = os.environ.get("PATHPILOT_DEBUG_KEEP_AUDIO", "0") == "1"
DEBUG_AUDIO_DIR = RUNTIME_DIR / "audio_debug"

WORKFLOWS_DIR = REPO_ROOT / "data" / "workflows"
WORKFLOWS_INDEX_PATH = WORKFLOWS_DIR / "index.json"
SITE_POLICIES_DIR = REPO_ROOT / "data" / "site-policies"

PAIRING_TOKEN_PATH = Path(__file__).parent / ".pairing_token"
CONVAI_AGENT_ID_PATH = Path(__file__).parent / ".convai_agent_id"
CONTROLLER_MODE = os.environ.get("PATHPILOT_MODE", "development")  # "development" | "demo"

HERMES_EXE = r"C:\Users\Dell\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes"
if not Path(HERMES_EXE).exists():
    HERMES_EXE = "hermes"

app = Flask(__name__, static_folder="static", static_url_path="")

# ---- Pairing token ------------------------------------------------------------

def _get_or_create_pairing_token() -> str:
    """
    A local shared secret between this controller and the PathPilot
    extension -- NOT the ElevenLabs key, never sent to any external
    service. Generated once, stored in a local file (gitignored),
    printed to the server console on first run so a human can paste it
    into the extension's one-time pairing field.
    """
    if PAIRING_TOKEN_PATH.exists():
        return PAIRING_TOKEN_PATH.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    PAIRING_TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


PAIRING_TOKEN = _get_or_create_pairing_token()


def _require_pairing_token():
    if request.method == "OPTIONS":
        return None
    supplied = request.headers.get("X-PathPilot-Token", "")
    if not secrets.compare_digest(supplied, PAIRING_TOKEN):
        return jsonify({"error": "invalid or missing pairing token"}), 401
    return None


@app.before_request
def _auth_gate():
    # Static UI (legacy dashboard) and the pairing bootstrap endpoint stay open;
    # everything else requires the pairing token.
    if request.path in ("/", "/api/pair/bootstrap") or request.path.startswith("/static/"):
        return None
    return _require_pairing_token()


@app.after_request
def _cors(resp):
    origin = request.headers.get("Origin", "")
    if origin.startswith("chrome-extension://"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-PathPilot-Token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/api/pair/bootstrap", methods=["POST"])
def api_pair_bootstrap():
    """
    One-time pairing helper: the human runs this locally once (or the
    extension's options page calls it) to fetch the token that was
    generated on this machine. This endpoint is intentionally the ONLY
    unauthenticated one, and it only works from localhost by construction
    (the server itself only binds to 127.0.0.1). It never reveals the
    ElevenLabs key -- only the local pairing token.
    """
    return jsonify({"pairing_token": PAIRING_TOKEN, "controller_url": "http://127.0.0.1:5057"})


@app.route("/api/convai/signed-url", methods=["POST"])
def api_convai_signed_url():
    """Mint a short-lived ElevenLabs conversational connection URL.

    The extension receives only this expiring URL after local pairing. The
    ElevenLabs API key and agent-management credentials remain server-side.
    No page text, cookies, screenshots, or browser profile data are sent to
    ElevenLabs by this endpoint.
    """
    if not CONVAI_AGENT_ID_PATH.exists():
        return jsonify({"error": "voice agent is not provisioned"}), 503
    agent_id = CONVAI_AGENT_ID_PATH.read_text(encoding="utf-8").strip()
    if not agent_id:
        return jsonify({"error": "voice agent is not configured"}), 503
    import requests
    try:
        resp = requests.get(
            "https://api.elevenlabs.io/v1/convai/conversation/get_signed_url",
            params={"agent_id": agent_id},
            headers={"xi-api-key": _get_api_key()}, timeout=20,
        )
        if resp.status_code != 200:
            return jsonify({"error": "could not create voice connection"}), 502
        signed_url = (resp.json() or {}).get("signed_url")
        if not signed_url:
            return jsonify({"error": "voice service returned no connection URL"}), 502
        return jsonify({"signed_url": signed_url, "agent_id": agent_id, "expires": "short_lived"})
    except Exception:
        return jsonify({"error": "voice service unavailable"}), 502


# ---- Shared helpers -------------------------------------------------------------

def _load_dotenv(path: Path) -> dict:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def _get_api_key() -> str:
    env_vals = _load_dotenv(ENV_PATH)
    key = env_vals.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not found in D:\\hermes\\.env or process environment.")
    return key


def _load_voice_config() -> dict:
    return json.loads(VOICE_CONFIG_PATH.read_text(encoding="utf-8"))


def _session_path(session_id: str) -> Path:
    return RUNTIME_DIR / f"{session_id}.json"


def _events_path(session_id: str) -> Path:
    return RUNTIME_DIR / f"{session_id}.events.jsonl"


def _load_session(session_id: str) -> dict:
    p = _session_path(session_id)
    if not p.exists():
        raise RuntimeError(f"No session at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _save_session(session: dict) -> None:
    session["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _session_path(session["session_id"]).write_text(json.dumps(session, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in obj.items() if "key" not in k.lower() and "secret" not in k.lower() and "token" not in k.lower()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(safe, ensure_ascii=False) + "\n")


_event_counter_lock = threading.Lock()
_event_counters: dict[str, int] = {}


def push_event(session_id: str, event_type: str, text: str, step_id: str | None = None, meta: dict | None = None) -> dict:
    """
    Appends one event (narration / transcript / answer / verification /
    safety) to the session's durable event log for the extension's
    conversation feed to poll. event_type must be one of:
    narration, transcript, answer, verification, safety, state.
    """
    with _event_counter_lock:
        _event_counters[session_id] = _event_counters.get(session_id, 0) + 1
        event_id = _event_counters[session_id]
    event = {
        "id": event_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event_type": event_type,
        "step_id": step_id,
        "text": text,
        "meta": meta or {},
    }
    _append_jsonl(_events_path(session_id), event)
    return event


def _load_events(session_id: str, since: int = 0) -> list[dict]:
    p = _events_path(session_id)
    if not p.exists():
        return []
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("id", 0) > since:
            events.append(e)
    return events


def _bootstrap_event_counter(session_id: str) -> None:
    if session_id in _event_counters:
        return
    events = _load_events(session_id, since=0)
    _event_counters[session_id] = max((e.get("id", 0) for e in events), default=0)


# ---- Site / workflow registry ---------------------------------------------------

def _list_sites() -> list[dict]:
    """Return only sites that have at least one registered active workflow.

    Policies without workflows (for example a pre-authored FlytBase safety
    policy) are deliberately NOT presented in the Run selector. They become
    selectable only after the Teach flow has captured and registered a pack.
    """
    grouped: dict[str, list[dict]] = {}
    for workflow in _list_workflows():
        if workflow.get("status") == "active":
            grouped.setdefault(workflow["site_id"], []).append(workflow)

    sites = []
    for site_id, workflows in sorted(grouped.items()):
        try:
            policy = _load_site_policy(site_id)
        except RuntimeError:
            continue
        sites.append({
            "site_id": site_id,
            "display_name": policy.get("display_name", site_id),
            "workflow_count": len(workflows),
        })
    return sites


def _load_site_policy(site_id: str) -> dict:
    p = SITE_POLICIES_DIR / f"{site_id}.json"
    if not p.exists():
        raise RuntimeError(f"No site policy for {site_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def _list_workflows(site_id: str | None = None) -> list[dict]:
    if not WORKFLOWS_INDEX_PATH.exists():
        return []
    index = json.loads(WORKFLOWS_INDEX_PATH.read_text(encoding="utf-8"))
    wfs = index.get("workflows", [])
    if site_id:
        wfs = [w for w in wfs if w.get("site_id") == site_id]
    return wfs


def _load_workflow_file(path_str: str) -> dict:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def _step_by_id(workflow: dict, step_id: str) -> dict | None:
    for s in workflow.get("steps", []):
        if s.get("step_id") == step_id:
            return s
    return None


def _step_index(workflow: dict, step_id: str | None) -> tuple[int, int]:
    steps = workflow.get("steps", [])
    total = len(steps)
    if not step_id:
        return 0, total
    for i, s in enumerate(steps, start=1):
        if s.get("step_id") == step_id:
            return i, total
    return 0, total


def _validate_domain(site_id: str, url: str) -> dict:
    try:
        policy = _load_site_policy(site_id)
    except RuntimeError as exc:
        return {"valid": False, "reason": str(exc)}
    hostname = urlparse(url).hostname or ""
    allowed = policy.get("allowed_domains", [])
    valid = hostname in allowed
    return {"valid": valid, "domain": hostname, "allowed_domains": allowed}


# ---- ElevenLabs STT -------------------------------------------------------------

def transcribe_audio(audio_bytes: bytes, content_type: str) -> dict:
    import requests

    api_key = _get_api_key()
    ext = "webm"
    if "wav" in content_type:
        ext = "wav"
    elif "mp4" in content_type or "m4a" in content_type:
        ext = "mp4"
    elif "ogg" in content_type:
        ext = "ogg"

    t0 = time.time()
    files = {"file": (f"customer_question.{ext}", audio_bytes, content_type or "audio/webm")}
    data = {"model_id": "scribe_v1"}
    resp = requests.post(
        "https://api.elevenlabs.io/v1/speech-to-text",
        headers={"xi-api-key": api_key},
        files=files,
        data=data,
        timeout=30,
    )
    latency = round(time.time() - t0, 3)
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs STT failed: {resp.status_code} {resp.text[:200]}")
    result = resp.json()
    text = (result.get("text") or "").strip()
    return {"text": text, "latency_seconds": latency, "raw_language_code": result.get("language_code")}


# ---- Classification --------------------------------------------------------------

def classify_utterance(text: str) -> dict:
    t = text.strip().lower()
    if not t:
        return {"type": "unclear", "reason": "empty transcript"}
    if re.fullmatch(r"(?:hi|hello|hey|good (?:morning|afternoon|evening))(?:\s+pathpilot)?[!. ]*", t):
        return {"type": "GREETING"}
    if re.search(r"\bstop\b", t):
        return {"type": "STOP"}
    if re.search(r"\bpause\b|\bhold on\b|\bwait a (sec|second|moment)\b", t):
        return {"type": "PAUSE"}
    if re.search(r"\bskip to\b", t):
        m = re.search(r"skip to (?:the )?(step[\s-]?\d+|\w[\w-]*)", t)
        target = m.group(1).replace(" ", "-") if m else None
        return {"type": "SKIP", "target_hint": target}
    if re.search(r"\bcontinue\b|\bgo ahead\b|\bresume\b|\bkeep going\b|\bproceed\b", t):
        return {"type": "CONTINUE"}
    if t.endswith("?") or re.match(r"^(why|what|how|when|where|who|which|can|could|does|do|is|are|will|would)\b", t):
        return {"type": "QUESTION"}
    return {"type": "unclear", "reason": "did not match any known intent pattern"}


def _fuzzy_match_step(workflow: dict, hint: str | None) -> dict | None:
    if not hint:
        return None
    hint_norm = hint.replace("-", " ").strip().lower()
    for s in workflow.get("steps", []):
        sid = s.get("step_id", "")
        name = s.get("name", "").lower()
        if hint_norm == sid.replace("-", " ") or hint_norm in name:
            return s
    # numeric hint like "step 4" / "4"
    m = re.search(r"(\d+)", hint_norm)
    if m:
        n = int(m.group(1))
        for s in workflow.get("steps", []):
            if s.get("step_number") == n:
                return s
    return None


# ---- Hermes as the decision-making brain -----------------------------------------

def ask_hermes(question: str, session: dict, workflow: dict) -> tuple[str, float]:
    current_step_id = session.get("current_step") or "step-1"
    step = _step_by_id(workflow, current_step_id) or {}
    reference_facts = step.get("reference_capture", {})
    verification_text = step.get("verification", "")

    grounding = (
        f"Current workflow step: {step.get('name', current_step_id)}. "
        f"Verified visible facts on the live page at this step: {json.dumps(reference_facts)}. "
        f"Step verification description: {verification_text}"
    )
    prompt = (
        "You are PathPilot, a voice walkthrough assistant answering a customer's spoken question "
        "in the middle of a live HubSpot CRM demo. Answer ONLY using the verified visible page facts "
        "given below as ground truth. If the facts don't cover it, you may add general knowledge but "
        "you MUST prefix that part with 'General guidance:'. Never guess. Keep the answer under 60 words, "
        "plain spoken English, no markdown, no lists, no selectors, no JSON, no internal reasoning.\n\n"
        f"{grounding}\n\nCustomer's question: {question}"
    )

    t0 = time.time()
    result = subprocess.run(
        [HERMES_EXE, "chat", "-Q", "-q", prompt, "--max-turns", "1", "--ignore-rules"],
        capture_output=True, text=True, timeout=60,
    )
    latency = round(time.time() - t0, 3)
    answer = (result.stdout or "").strip()
    if not answer:
        answer = "I couldn't reach the Hermes reasoning process just now -- let's try that again."
    return answer, latency


def spawn_hermes_run(session_id: str, site_id: str, workflow_id: str) -> None:
    """
    Spawns a real, independent `hermes chat` agent process to execute the
    workflow live (its own computer_use/browser tool access, per the
    platform-walkthrough-agent skill contract), reporting progress back to
    this controller via /api/events and /api/speak as it goes. Fire-and-
    forget: failures are logged as a safety event, never silently dropped.
    """
    prompt = (
        f"Load the platform-walkthrough-agent skill. RUN the workflow with site_id={site_id}, "
        f"workflow_id={workflow_id}, session_id={session_id}. Resume the existing runtime state at "
        f"D:\\hermes\\data\\runtime\\{session_id}.json starting from its next_step. For each step: "
        f"POST the narration sentence to http://127.0.0.1:5057/api/speak with header "
        f"X-PathPilot-Token: <read from D:\\hermes\\pathpilot_panel\\.pairing_token>, then perform the "
        f"browser action, then POST a verification event to http://127.0.0.1:5057/api/events, then update "
        f"the runtime state file. Stop before any step whose action_safety_level is not read_only without "
        f"explicit separate user confirmation."
    )

    def _run():
        try:
            subprocess.run(
                [HERMES_EXE, "chat", "-Q", "-q", prompt, "--max-turns", "60", "-s", "platform-walkthrough-agent"],
                capture_output=True, text=True, timeout=900,
            )
        except Exception as exc:  # noqa: BLE001
            push_event(session_id, "safety", f"Background RUN process failed to complete: {exc}", meta={"error": True})

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ---- ElevenLabs TTS with barge-in support ----------------------------------------

_playback_lock = threading.Lock()
_current_ffplay_proc: subprocess.Popen | None = None
_speaking_sessions: dict[str, bool] = {}


def _find_ffplay() -> str | None:
    from shutil import which
    return which("ffplay")


CREATE_NO_WINDOW = 0x08000000


def stop_speaking(session_id: str | None = None) -> bool:
    global _current_ffplay_proc
    was_speaking = False
    with _playback_lock:
        if _current_ffplay_proc is not None and _current_ffplay_proc.poll() is None:
            was_speaking = True
            try:
                _current_ffplay_proc.kill()
            except OSError:
                pass
        _current_ffplay_proc = None
    if session_id:
        _speaking_sessions[session_id] = False
    return was_speaking


def speak_text(session_id: str, text: str, step_id: str | None, event_type: str, mode: str = "voice") -> dict:
    """
    event_type in {"narration", "answer"}. mode in {"text_text","text_voice","voice_voice"} --
    text_text never produces audio (still logs the event for the transcript).
    """
    global _current_ffplay_proc
    import requests

    cfg = _load_voice_config()
    max_words = cfg.get("max_words_per_utterance", 35)
    if event_type == "answer":
        max_words = max(max_words, 60)
    words = text.strip().split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])

    event_meta = {"voice_id": cfg.get("voice_id"), "mode": mode}
    result = {
        "text": text, "spoken": False, "voice_failed": False,
        "time_to_first_audio_seconds": None, "total_seconds": None, "error": None,
    }

    push_event(session_id, event_type, text, step_id=step_id, meta=event_meta)

    if mode == "text_text" or not cfg.get("enabled", True):
        result["note"] = "no audio in this mode / voice disabled"
        _append_jsonl(NARRATION_LOG_PATH, {**result, "event_type": event_type, "step_id": step_id, "session_id": session_id})
        return result

    ffplay = _find_ffplay()
    t0 = time.time()
    _speaking_sessions[session_id] = True
    try:
        if not ffplay:
            raise RuntimeError("ffplay not found on PATH")
        api_key = _get_api_key()
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['voice_id']}/stream"
        headers = {"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
        payload = {
            "text": text,
            "model_id": cfg.get("model_id", "eleven_flash_v2_5"),
            "output_format": cfg.get("output_format", "mp3_44100_128"),
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        params = {"output_format": cfg.get("output_format", "mp3_44100_128"), "optimize_streaming_latency": "4"}

        proc = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-i", "pipe:0"],
            stdin=subprocess.PIPE, creationflags=CREATE_NO_WINDOW,
        )
        with _playback_lock:
            _current_ffplay_proc = proc

        ttfa = None
        with requests.post(url, headers=headers, params=params, json=payload, stream=True, timeout=30) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"ElevenLabs TTS failed: {resp.status_code}")
            for chunk in resp.iter_content(chunk_size=2048):
                if not chunk:
                    continue
                if ttfa is None:
                    ttfa = time.time() - t0
                if proc.poll() is not None:
                    break
                try:
                    proc.stdin.write(chunk)
                except (BrokenPipeError, OSError):
                    break
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.wait(timeout=60)
        result["time_to_first_audio_seconds"] = round(ttfa, 3) if ttfa is not None else None
        result["spoken"] = True
    except Exception as exc:  # noqa: BLE001
        result["voice_failed"] = True
        result["error"] = str(exc)
    finally:
        with _playback_lock:
            _current_ffplay_proc = None
        _speaking_sessions[session_id] = False
        result["total_seconds"] = round(time.time() - t0, 3)
        _append_jsonl(NARRATION_LOG_PATH, {**result, "event_type": event_type, "step_id": step_id, "session_id": session_id})

    return result


# ---- Routes: static (legacy dashboard, unauthenticated for convenience) ---------

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---- Routes: registry -------------------------------------------------------------

@app.route("/api/sites")
def api_sites():
    return jsonify({"sites": _list_sites(), "mode": CONTROLLER_MODE})


@app.route("/api/workflows")
def api_workflows():
    site_id = request.args.get("site_id")
    return jsonify({"workflows": _list_workflows(site_id)})


@app.route("/api/workflow-detail")
def api_workflow_detail():
    site_id = request.args.get("site_id")
    workflow_id = request.args.get("workflow_id")
    wfs = _list_workflows(site_id)
    match = next((w for w in wfs if w.get("workflow_id") == workflow_id), None)
    if not match:
        return jsonify({"error": "workflow not found"}), 404
    workflow = _load_workflow_file(match["path"])
    policy = _load_site_policy(site_id)
    return jsonify({"workflow": workflow, "site_policy": policy})


@app.route("/api/teach/start", methods=["POST"])
def api_teach_start():
    """Begin generic site onboarding without touching the page or its credentials.

    This creates only a local, read-only draft policy for the active tab's
    exact HTTPS hostname. It does not create a workflow pack or enable RUN.
    Hermes must still capture 3–6 real, human-supervised steps before the
    website appears in the normal Run selector.
    """
    payload = request.get_json(force=True) or {}
    url = payload.get("active_tab_url", "")
    display_name = (payload.get("display_name") or "").strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname:
        return jsonify({"error": "teach_requires_https_active_tab"}), 400
    site_id = re.sub(r"[^a-z0-9]+", "-", hostname).strip("-")
    if not display_name:
        display_name = hostname

    policy_path = SITE_POLICIES_DIR / f"{site_id}.json"
    if not policy_path.exists():
        draft_policy = {
            "site_id": site_id,
            "schema_version": "1.0",
            "display_name": display_name,
            "allowed_domains": [hostname],
            "browser_profile": {
                "path": str(REPO_ROOT / "data" / "browser-profiles" / site_id),
                "description": "Dedicated PathPilot test-profile placeholder. Human login only; never inspect profile data."
            },
            "default_action_mode": "read_only",
            "allowed_actions": ["navigate", "view", "scroll"],
            "reversible_write_actions_requiring_confirmation": [],
            "prohibited_actions": ["delete", "archive", "merge", "publish", "import", "export", "bulk_action", "send_email", "payment_action", "edit_credentials"],
            "confirmation_requirements": {"reversible_writes": "Explicit named confirmation required.", "irreversible_or_prohibited": "Never perform."},
            "site_specific_risk_keywords": [],
            "captcha_and_security_policy": "Never bypass CAPTCHAs, 2FA, permission prompts, or security checkpoints."
        }
        policy_path.write_text(json.dumps(draft_policy, indent=2), encoding="utf-8")

    teach_id = f"teach-{site_id}-{uuid.uuid4().hex[:8]}"
    return jsonify({
        "teach_id": teach_id,
        "site_id": site_id,
        "display_name": display_name,
        "domain": hostname,
        "status": "draft_policy_created",
        "message": "Draft safety policy created. Next: Hermes TEACH captures 3–6 visible, read-only steps. The site remains absent from Run until a workflow pack is registered."
    })


@app.route("/api/validate-domain", methods=["POST"])
def api_validate_domain():
    payload = request.get_json(force=True) or {}
    site_id = payload.get("site_id")
    url = payload.get("url", "")
    return jsonify(_validate_domain(site_id, url))


# ---- Routes: session lifecycle -----------------------------------------------------

@app.route("/api/session/start", methods=["POST"])
def api_session_start():
    payload = request.get_json(force=True) or {}
    site_id = payload.get("site_id")
    workflow_id = payload.get("workflow_id")
    active_tab_url = payload.get("active_tab_url", "")
    session_id = payload.get("session_id") or f"{site_id}-{workflow_id}-{uuid.uuid4().hex[:8]}"

    domain_check = _validate_domain(site_id, active_tab_url)
    if not domain_check.get("valid"):
        push_event(session_id, "safety", f"Domain validation failed for {domain_check.get('domain')}; Start refused.")
        return jsonify({"error": "domain_not_allowed", "domain_check": domain_check}), 403

    wfs = _list_workflows(site_id)
    match = next((w for w in wfs if w.get("workflow_id") == workflow_id), None)
    if not match:
        return jsonify({"error": "workflow_not_found"}), 404
    workflow = _load_workflow_file(match["path"])

    sp = _session_path(session_id)
    if sp.exists():
        session = _load_session(session_id)
        session["status"] = "in_progress"
    else:
        first_step = workflow["steps"][0]["step_id"]
        session = {
            "schema_version": "1.0",
            "session_id": session_id,
            "site_id": site_id,
            "workflow_id": workflow_id,
            "status": "in_progress",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "browser": {"pid": None, "window_id": None, "profile_path": None},
            "last_completed_step": None,
            "current_step": first_step,
            "next_step": first_step,
            "action_mode": "read_only",
            "pending_confirmation": None,
            "interruption_log": [],
            "voice": {"enabled": True, "last_voice_failed": False},
            "notes": "Session created via PathPilot extension Start button.",
        }
    _save_session(session)
    _bootstrap_event_counter(session_id)
    push_event(session_id, "safety", f"Domain validated: {domain_check['domain']} is allowed for {site_id}.", meta=domain_check)
    push_event(session_id, "narration", "Welcome. I’ll show you how to review a new inbound lead. Ask a question at any time.", step_id=workflow["steps"][0]["step_id"], meta={"opening": True})
    push_event(session_id, "state", f"Workflow started: {workflow.get('workflow_title')}", meta={"status": "in_progress"})

    spawn_hermes_run(session_id, site_id, workflow_id)

    return jsonify({"session_id": session_id, "session": session, "domain_check": domain_check})


@app.route("/api/session/pause", methods=["POST"])
def api_session_pause():
    payload = request.get_json(force=True) or {}
    session_id = payload.get("session_id")
    session = _load_session(session_id)
    session["status"] = "paused"
    _save_session(session)
    push_event(session_id, "state", "Walkthrough paused.", meta={"status": "paused"})
    return jsonify({"session": session})


@app.route("/api/session/resume", methods=["POST"])
def api_session_resume():
    payload = request.get_json(force=True) or {}
    session_id = payload.get("session_id")
    session = _load_session(session_id)
    session["status"] = "in_progress"
    _save_session(session)
    push_event(session_id, "state", f"Resuming from {session.get('next_step')}.", meta={"status": "in_progress"})
    spawn_hermes_run(session_id, session["site_id"], session["workflow_id"])
    return jsonify({"session": session})


@app.route("/api/session/stop", methods=["POST"])
def api_session_stop():
    payload = request.get_json(force=True) or {}
    session_id = payload.get("session_id")
    session = _load_session(session_id)
    session["status"] = "stopped"
    _save_session(session)
    push_event(session_id, "state", "Stopped. No further action will occur.", meta={"status": "stopped"})
    return jsonify({"session": session})


@app.route("/api/session/skip", methods=["POST"])
def api_session_skip():
    payload = request.get_json(force=True) or {}
    session_id = payload.get("session_id")
    target_hint = payload.get("target_step") or payload.get("target_hint")
    session = _load_session(session_id)

    wfs = _list_workflows(session["site_id"])
    match = next((w for w in wfs if w.get("workflow_id") == session["workflow_id"]), None)
    workflow = _load_workflow_file(match["path"])
    policy = _load_site_policy(session["site_id"])

    target_step = _fuzzy_match_step(workflow, target_hint)
    if not target_step:
        push_event(session_id, "safety", f"Skip request '{target_hint}' did not match a known step; refused.")
        return jsonify({"allowed": False, "reason": "no matching step"}), 200

    unsafe = target_step.get("action_safety_level") not in ("read_only", "verify_visible")
    if unsafe:
        push_event(session_id, "safety", f"Skip to {target_step['name']} refused: destination requires confirmation ({target_step.get('action_safety_level')}).")
        return jsonify({"allowed": False, "reason": "destination requires confirmation", "target_step": target_step["step_id"]})

    push_event(session_id, "verification", f"Skip validated: moving to {target_step['name']}.", step_id=target_step["step_id"])
    session["current_step"] = target_step["step_id"]
    session["next_step"] = target_step["step_id"]
    session["last_completed_step"] = target_step["step_id"]
    _save_session(session)
    return jsonify({"allowed": True, "session": session})


@app.route("/api/session/<session_id>")
def api_session_get(session_id):
    session = _load_session(session_id)
    wfs = _list_workflows(session["site_id"])
    match = next((w for w in wfs if w.get("workflow_id") == session["workflow_id"]), None)
    workflow = _load_workflow_file(match["path"]) if match else {"steps": []}
    idx, total = _step_index(workflow, session.get("current_step"))
    policy = _load_site_policy(session["site_id"])
    speaking = _speaking_sessions.get(session_id, False)
    return jsonify({
        "session": session,
        "step_index": idx,
        "step_total": total,
        "speaking": speaking,
        "site_policy_summary": {
            "allowed_domains": policy.get("allowed_domains"),
            "default_action_mode": policy.get("default_action_mode"),
            "prohibited_actions": policy.get("prohibited_actions"),
            "reversible_write_actions_requiring_confirmation": policy.get("reversible_write_actions_requiring_confirmation"),
            "site_specific_risk_keywords": policy.get("site_specific_risk_keywords"),
        },
    })


# ---- Routes: events & speak (used by extension AND by any Hermes RUN process) ----

@app.route("/api/events", methods=["POST"])
def api_events_post():
    payload = request.get_json(force=True) or {}
    session_id = payload.get("session_id")
    event = push_event(
        session_id, payload.get("event_type", "narration"), payload.get("text", ""),
        step_id=payload.get("step_id"), meta=payload.get("meta"),
    )
    return jsonify({"event": event})


@app.route("/api/events")
def api_events_get():
    session_id = request.args.get("session_id")
    since = int(request.args.get("since", 0))
    _bootstrap_event_counter(session_id)
    return jsonify({"events": _load_events(session_id, since)})


@app.route("/api/speak", methods=["POST"])
def api_speak():
    payload = request.get_json(force=True) or {}
    session_id = payload.get("session_id")
    text = payload.get("text", "")
    step_id = payload.get("step_id")
    event_type = payload.get("event_type", "narration")
    mode = payload.get("mode", "voice_voice")
    result = speak_text(session_id, text, step_id, event_type, mode=mode)
    return jsonify(result)


@app.route("/api/interrupt", methods=["POST"])
def api_interrupt():
    payload = request.get_json(force=True) or {}
    session_id = payload.get("session_id")
    was_speaking = stop_speaking(session_id)
    if was_speaking:
        push_event(session_id, "safety", "Barge-in: customer started speaking, PathPilot's audio was stopped immediately.")
    return jsonify({"barge_in": was_speaking})


# ---- Routes: question handling (voice + text fallback) ---------------------------

def _handle_utterance(session_id: str, transcript: str, source: str, stt_latency: float | None, mode: str) -> dict:
    session = _load_session(session_id)
    wfs = _list_workflows(session["site_id"])
    match = next((w for w in wfs if w.get("workflow_id") == session["workflow_id"]), None)
    workflow = _load_workflow_file(match["path"])

    push_event(session_id, "transcript", transcript, step_id=session.get("current_step"), meta={"source": source})

    classification = classify_utterance(transcript)
    answer = None
    answer_latency = None
    resumed = False

    if classification["type"] == "GREETING":
        answer = "Hi — I’m PathPilot. I’m following this walkthrough and you can ask about what’s on the screen, say pause, or ask me to continue."

    elif classification["type"] == "unclear":
        answer = "I heard you. Ask me about what’s visible on the screen, or say pause, continue, skip, or stop."

    elif classification["type"] == "STOP":
        session["status"] = "stopped"
        answer = "Okay, I'm stopping here. No further action will happen until you tell me to continue."
        push_event(session_id, "state", answer, meta={"status": "stopped"})

    elif classification["type"] == "PAUSE":
        session["status"] = "paused"
        answer = "Okay, I've paused. Let me know when you'd like me to continue."
        push_event(session_id, "state", answer, meta={"status": "paused"})

    elif classification["type"] == "CONTINUE":
        session["status"] = "in_progress"
        answer = "Continuing from where we left off."
        resumed = True
        push_event(session_id, "state", f"Resuming from {session.get('next_step')}.", meta={"status": "in_progress"})
        spawn_hermes_run(session_id, session["site_id"], session["workflow_id"])

    elif classification["type"] == "SKIP":
        target_step = _fuzzy_match_step(workflow, classification.get("target_hint"))
        if not target_step:
            answer = "I couldn't confirm a safe destination step for that skip request, so I'm staying where we are."
        elif target_step.get("action_safety_level") not in ("read_only", "verify_visible"):
            answer = f"I can't safely skip to {target_step['name']} without your explicit confirmation first, since it involves a step that needs approval."
            push_event(session_id, "safety", answer, step_id=target_step["step_id"])
        else:
            answer = f"Skipping ahead to {target_step['name']}, since that's safe and allowed by policy."
            session["current_step"] = target_step["step_id"]
            session["next_step"] = target_step["step_id"]
            session["last_completed_step"] = target_step["step_id"]
            push_event(session_id, "verification", answer, step_id=target_step["step_id"])

    elif classification["type"] == "QUESTION":
        push_event(session_id, "state", "PathPilot is checking the page before answering.", step_id=session.get("current_step"))
        answer, answer_latency = ask_hermes(transcript, session, workflow)
        resumed = True

    session.setdefault("interruption_log", []).append({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source, "transcript": transcript, "classification": classification,
        "answer": answer, "stt_latency_seconds": stt_latency, "answer_latency_seconds": answer_latency,
        "resumed": resumed,
    })
    _save_session(session)

    _append_jsonl(VOICE_TRANSCRIPT_LOG_PATH, {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session_id": session_id, "source": source,
        "transcript": transcript, "classification": classification, "answer": answer,
        "stt_latency_seconds": stt_latency, "answer_latency_seconds": answer_latency,
    })

    speak_result = None
    if answer:
        speak_result = speak_text(session_id, answer, session.get("current_step"), "answer", mode=mode)

    if resumed and classification["type"] in ("QUESTION", "CONTINUE"):
        push_event(session_id, "state", "Resuming walkthrough.", meta={"status": session.get("status")})

    return {
        "transcript": transcript, "classification": classification, "answer": answer, "resumed": resumed,
        "session": {"current_step": session.get("current_step"), "next_step": session.get("next_step"), "status": session.get("status")},
        "stt_latency_seconds": stt_latency, "answer_latency_seconds": answer_latency, "speak_result": speak_result,
    }


@app.route("/api/voice-question", methods=["POST"])
def api_voice_question():
    session_id = request.form.get("session_id") or request.args.get("session_id")
    mode = request.form.get("mode", "voice_voice")
    if "audio" not in request.files:
        return jsonify({"error": "no audio file provided"}), 400
    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    content_type = audio_file.content_type or "audio/webm"

    if DEBUG_AUDIO_RETENTION:
        DEBUG_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_AUDIO_DIR / f"debug_{uuid.uuid4().hex}.webm").write_bytes(audio_bytes)

    try:
        stt_result = transcribe_audio(audio_bytes, content_type)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"transcription failed: {exc}"}), 502
    finally:
        del audio_bytes  # never persisted beyond this scope unless debug mode wrote a copy above

    result = _handle_utterance(session_id, stt_result["text"], "voice", stt_result["latency_seconds"], mode)
    return jsonify(result)


@app.route("/api/text-question", methods=["POST"])
def api_text_question():
    payload = request.get_json(force=True) or {}
    session_id = payload.get("session_id")
    text = (payload.get("text") or "").strip()
    mode = payload.get("mode", "text_text")
    if not text:
        return jsonify({"error": "empty text"}), 400
    result = _handle_utterance(session_id, text, "text", None, mode)
    return jsonify(result)


if __name__ == "__main__":
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[PathPilot Controller] mode={CONTROLLER_MODE}")
    print(f"[PathPilot Controller] pairing token file: {PAIRING_TOKEN_PATH}")
    app.run(host="127.0.0.1", port=5057, debug=False)
