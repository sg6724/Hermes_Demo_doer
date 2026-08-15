"""
PathPilot voice narration helper (ElevenLabs TTS) — shared low-latency
streaming voice layer used by both the hubspot-live-walkthrough baseline
skill and the generic platform-walkthrough-agent skill.

Security rules enforced by this module:
  - The ElevenLabs API key is read ONLY from D:\\hermes\\.env or the
    ELEVENLABS_API_KEY process environment variable. It is never printed,
    logged, returned, or written anywhere by this module.
  - Narration event metadata (text, timestamp, voice id, step id, duration,
    time-to-first-audio) is appended to D:\\hermes\\data\\runtime\\narration.jsonl.
    Audio bytes and secrets are never written to that file.
  - Generated audio is streamed and, when a temp file is needed at all, is
    written only under D:\\hermes\\data\\runtime\\audio\\ and deleted
    immediately after playback finishes (best-effort, always via
    try/finally). The default streaming path avoids writing a temp file at
    all — audio is piped directly into the player process's stdin.

Usage (from another script):
    from voice_narrator import speak, load_voice_config, voice_enabled

    if voice_enabled():
        speak("Let's open the Contacts list.")

CLI usage:
    python voice_narrator.py --test
    python voice_narrator.py --say "Some short sentence"
    python voice_narrator.py --no-voice --say "..."   # metadata logged, not spoken
    python voice_narrator.py --benchmark              # 3 short sentences, TTFA + total time
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(r"D:\hermes")
ENV_PATH = REPO_ROOT / ".env"
VOICE_CONFIG_PATH = REPO_ROOT / "config" / "voice_config.json"
AUDIO_DIR = REPO_ROOT / "data" / "runtime" / "audio"
NARRATION_LOG_PATH = REPO_ROOT / "data" / "runtime" / "narration.jsonl"

MAX_WORDS_DEFAULT = 35
TTFA_TARGET_SECONDS = 3.0
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

CREATE_NO_WINDOW = 0x08000000


class VoiceError(RuntimeError):
    pass


def _load_dotenv(path: Path) -> dict:
    """Minimal .env parser. Never logs values."""
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
    """
    Reads ELEVENLABS_API_KEY from D:\\hermes\\.env first, then falls back to
    the process environment. Raises VoiceError (with NO key value inside the
    message) if not found. The returned value must never be logged/printed
    by any caller.
    """
    env_file_values = _load_dotenv(ENV_PATH)
    key = env_file_values.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError(
            "ELEVENLABS_API_KEY not found in D:\\hermes\\.env or process environment."
        )
    return key


def load_voice_config() -> dict:
    if not VOICE_CONFIG_PATH.exists():
        raise VoiceError(f"Voice config not found at {VOICE_CONFIG_PATH}")
    return json.loads(VOICE_CONFIG_PATH.read_text(encoding="utf-8"))


def voice_enabled(override: bool | None = None) -> bool:
    """
    Voice is ON by default. Resolution order:
      1. explicit override argument (e.g. --no-voice CLI flag / function arg)
      2. PATHPILOT_VOICE env var ("0"/"false" disables)
      3. config/voice_config.json "enabled" field
    """
    if override is not None:
        return override
    env_flag = os.environ.get("PATHPILOT_VOICE")
    if env_flag is not None:
        return env_flag.strip().lower() not in ("0", "false", "off", "no")
    try:
        cfg = load_voice_config()
        return bool(cfg.get("enabled", True))
    except VoiceError:
        return True


def _truncate_to_word_limit(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _log_event(event: dict) -> None:
    NARRATION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Defensive: never allow a "key"/"api_key"/secret-looking field through.
    safe_event = {k: v for k, v in event.items() if "key" not in k.lower() and "secret" not in k.lower()}
    with open(NARRATION_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(safe_event, ensure_ascii=False) + "\n")


def _find_ffplay() -> str | None:
    from shutil import which

    return which("ffplay")


def _stream_speak_via_ffplay(
    text: str, voice_id: str, model_id: str, output_format: str, ffplay_path: str
) -> dict:
    """
    True low-latency path: pipes ElevenLabs' streamed MP3 bytes directly into
    ffplay's stdin as they arrive (no temp file, no full-download wait).
    Returns timing metrics: time_to_first_audio_seconds (time from request
    start to first audio byte received) and total_seconds (through playback
    completion).
    """
    import requests

    api_key = _get_api_key()
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "output_format": output_format,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    params = {"output_format": output_format, "optimize_streaming_latency": "4"}

    t_start = time.time()
    ttfa = None

    proc = subprocess.Popen(
        [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "quiet", "-i", "pipe:0"],
        stdin=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        with requests.post(url, headers=headers, params=params, json=payload, stream=True, timeout=30) as resp:
            if resp.status_code != 200:
                raise VoiceError(f"ElevenLabs TTS request failed with status {resp.status_code}")
            for chunk in resp.iter_content(chunk_size=2048):
                if not chunk:
                    continue
                if ttfa is None:
                    ttfa = time.time() - t_start
                try:
                    proc.stdin.write(chunk)
                except (BrokenPipeError, OSError):
                    break
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.wait(timeout=60)
    except Exception as exc:  # noqa: BLE001
        proc.kill()
        raise VoiceError(f"Streaming TTS playback failed: {exc}") from exc

    total = time.time() - t_start
    return {"time_to_first_audio_seconds": round(ttfa, 3) if ttfa is not None else None, "total_seconds": round(total, 3)}


def _synthesize_to_file(text: str, voice_id: str, model_id: str, output_format: str) -> tuple[Path, float | None]:
    """Fallback path: downloads ElevenLabs TTS audio to a temp mp3 file, then plays it. Returns (path, ttfa)."""
    import requests

    api_key = _get_api_key()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out_path = AUDIO_DIR / f"narration_{uuid.uuid4().hex}.mp3"

    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "output_format": output_format,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    params = {"output_format": output_format, "optimize_streaming_latency": "4"}

    t_start = time.time()
    ttfa = None
    try:
        with requests.post(url, headers=headers, params=params, json=payload, stream=True, timeout=30) as resp:
            if resp.status_code != 200:
                raise VoiceError(f"ElevenLabs TTS request failed with status {resp.status_code}")
            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=4096):
                    if chunk:
                        if ttfa is None:
                            ttfa = time.time() - t_start
                        f.write(chunk)
    except Exception as exc:  # noqa: BLE001
        if out_path.exists():
            out_path.unlink(missing_ok=True)
        raise VoiceError(f"TTS synthesis failed: {exc}") from exc

    return out_path, ttfa


def _play_audio_file_hidden(path: Path) -> None:
    """Plays a completed audio file with no visible window (fallback path)."""
    ffplay = _find_ffplay()
    if ffplay:
        subprocess.run(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            check=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return

    ps_script = (
        "Add-Type -AssemblyName presentationCore; "
        "$player = New-Object System.Windows.Media.MediaPlayer; "
        f"$player.Open([Uri]::new('{path.as_posix()}')); "
        "Start-Sleep -Milliseconds 400; "
        "$player.Play(); "
        "Start-Sleep -Seconds ([Math]::Ceiling($player.NaturalDuration.TimeSpan.TotalSeconds + 1)); "
        "$player.Stop();"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
        check=True,
        creationflags=CREATE_NO_WINDOW,
    )


def speak(
    text: str,
    step_id: str | None = None,
    voice_override_enabled: bool | None = None,
) -> dict:
    """
    Speaks a single short narration sentence aloud (ElevenLabs -> Windows
    default speakers, no visible player window) using true streaming
    playback when ffplay is available (audio piped directly to its stdin,
    no temp file). Logs event metadata including time-to-first-audio.

    On any voice failure, logs voice_failed=True with the error and returns
    normally (spoken=False) so callers can continue with text-only
    narration instead of stopping the walkthrough.

    Returns the metadata dict that was logged (never contains the API key or
    audio bytes).
    """
    cfg = load_voice_config()
    max_words = cfg.get("max_words_per_utterance", MAX_WORDS_DEFAULT)
    text_to_speak = _truncate_to_word_limit(text.strip(), max_words)

    enabled = voice_enabled(voice_override_enabled)
    start = time.time()
    event = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "step_id": step_id,
        "text": text_to_speak,
        "word_count": len(text_to_speak.split()),
        "voice_provider": cfg.get("provider"),
        "voice_id": cfg.get("voice_id"),
        "voice_name": cfg.get("voice_name"),
        "spoken": False,
        "time_to_first_audio_seconds": None,
        "duration_seconds": None,
        "voice_failed": False,
        "error": None,
    }

    if not enabled:
        event["note"] = "voice disabled; narration printed/logged only"
        _log_event(event)
        return event

    ffplay_path = _find_ffplay()
    try:
        if ffplay_path:
            timing = _stream_speak_via_ffplay(
                text_to_speak,
                voice_id=cfg["voice_id"],
                model_id=cfg.get("model_id", "eleven_flash_v2_5"),
                output_format=cfg.get("output_format", "mp3_44100_128"),
                ffplay_path=ffplay_path,
            )
            event["time_to_first_audio_seconds"] = timing["time_to_first_audio_seconds"]
        else:
            audio_path, ttfa = _synthesize_to_file(
                text_to_speak,
                voice_id=cfg["voice_id"],
                model_id=cfg.get("model_id", "eleven_flash_v2_5"),
                output_format=cfg.get("output_format", "mp3_44100_128"),
            )
            event["time_to_first_audio_seconds"] = round(ttfa, 3) if ttfa is not None else None
            try:
                _play_audio_file_hidden(audio_path)
            finally:
                audio_path.unlink(missing_ok=True)
        event["spoken"] = True
    except VoiceError as exc:
        event["spoken"] = False
        event["voice_failed"] = True
        event["error"] = str(exc)
    finally:
        event["duration_seconds"] = round(time.time() - start, 2)
        _log_event(event)

    return event


def run_benchmark(sentences: list[str] | None = None) -> list[dict]:
    """
    Speaks up to 3 short benchmark sentences once each, reporting
    time-to-first-audio and total playback time per sentence. Intended to be
    invoked manually/sparingly — never call this in a loop or automatically
    on every run.
    """
    sentences = sentences or [
        "Let's open the Contacts list to find your lead.",
        "Here's the activity history for this contact.",
        "This is where a follow-up task would be created.",
    ]
    results = []
    for i, s in enumerate(sentences[:3], start=1):
        result = speak(s, step_id=f"benchmark-{i}")
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="PathPilot ElevenLabs voice narration helper")
    parser.add_argument("--test", action="store_true", help="Speak the standard voice test line")
    parser.add_argument("--say", type=str, help="Speak an arbitrary short sentence")
    parser.add_argument("--no-voice", action="store_true", help="Disable speech; log metadata only")
    parser.add_argument("--benchmark", action="store_true", help="Run the 3-sentence voice latency benchmark once")
    args = parser.parse_args()

    if not args.test and not args.say and not args.benchmark:
        parser.error("Provide --test, --say '...', or --benchmark")

    try:
        _get_api_key()
    except VoiceError as exc:
        print(f"Voice blocked: {exc}", file=sys.stderr)
        return 1

    if args.benchmark:
        results = run_benchmark()
        for r in results:
            print(r["text"])
            print(
                f"[spoken={r['spoken']} ttfa={r['time_to_first_audio_seconds']}s "
                f"total={r['duration_seconds']}s voice_failed={r['voice_failed']}]"
            )
        return 0

    text = (
        "Welcome. I'll walk you through how to review an inbound lead in HubSpot."
        if args.test
        else args.say
    )
    result = speak(text, step_id="cli-test" if args.test else "cli-say", voice_override_enabled=(not args.no_voice))
    print(text)
    print(
        f"[spoken={result['spoken']} ttfa={result['time_to_first_audio_seconds']}s "
        f"total={result['duration_seconds']}s voice_failed={result['voice_failed']}]"
    )
    return 0 if result["spoken"] or args.no_voice else 1


if __name__ == "__main__":
    raise SystemExit(main())
