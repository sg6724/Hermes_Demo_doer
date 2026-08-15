// Persistent ElevenLabs Conversational AI client for PathPilot.
// Uses only a short-lived signed URL supplied by the paired localhost controller.
// No ElevenLabs API key is ever present in extension code or storage.
export class ConvAIClient {
  constructor({ getSignedUrl, onStatus, onTranscript, onResponse, onAudio, onError, playAudio = true }) {
    Object.assign(this, { getSignedUrl, onStatus, onTranscript, onResponse, onAudio, onError, playAudio });
    this.ws = null; this.ctx = null; this.stream = null; this.source = null;
    this.processor = null; this.capturing = false; this.playing = new Set();
    this.nextPlayTime = 0; // scheduling cursor so streamed chunks queue instead of overlapping
  }
  async connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return;
    this.onStatus?.("Connecting voice…");
    // ElevenLabs sends raw PCM. Create/resume the output context while this
    // is still running inside the user-initiated Start/Teach click gesture.
    this._audioContext();
    const { signed_url } = await this.getSignedUrl();
    await new Promise((resolve, reject) => {
      const ws = this.ws = new WebSocket(signed_url);
      ws.onopen = () => { this.onStatus?.("Voice ready"); resolve(); };
      ws.onerror = () => reject(new Error("Voice connection failed"));
      ws.onclose = () => { if (this.capturing) this.onStatus?.("Voice disconnected"); };
      ws.onmessage = (e) => this._message(e.data);
    });
  }
  async _message(raw) {
    let m; try { m = JSON.parse(raw); } catch { return; }
    if (m.type === "agent_response") {
      const text = m.agent_response_event?.agent_response || "";
      if (text) { this.onResponse?.(text); this.onStatus?.("PathPilot is speaking"); }
    } else if (m.type === "user_transcript") {
      const text = m.user_transcription_event?.user_transcript || "";
      if (text) { this.onTranscript?.(text); this.onStatus?.("Checking the current screen"); }
    } else if (m.type === "audio") {
      const b64 = m.audio_event?.audio_base_64;
      if (b64 && this.playAudio) this._play(b64);
    } else if (m.type === "interruption") {
      this.stopAudio(); this.onStatus?.("Listening");
    }
  }
  async prepareMic() {
    if (this.stream) return;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation:true, noiseSuppression:true, autoGainControl:true } });
    // Reuse the single output AudioContext instead of creating a second one:
    // two AudioContexts fighting over the same audio device is what was
    // causing multi-second stalls and unclear/garbled playback.
    const ctx = this._audioContext();
    this.ctx = ctx;
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.processor = this.ctx.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (e) => { if (this.capturing) this._sendPcm(e.inputBuffer.getChannelData(0)); };
    this.source.connect(this.processor); this.processor.connect(this.ctx.destination);
  }
  sendText(text) {
    if (!text?.trim() || this.ws?.readyState !== WebSocket.OPEN) throw new Error("Voice conversation is not connected");
    this.ws.send(JSON.stringify({ type: "user_message", text: text.trim() }));
    this.onTranscript?.(text.trim()); this.onStatus?.("Checking the current screen");
  }
  startCapture() { if (!this.ws || this.ws.readyState !== WebSocket.OPEN) throw new Error("Voice is not connected"); this.stopAudio(); this.capturing=true; this.onStatus?.("Listening"); }
  stopCapture() {
    if (!this.capturing) return;
    this.capturing = false;
    // The server detects end-of-turn with its own VAD over the streamed
    // audio, not from a client "stop" message. If we just go silent here,
    // the VAD never sees the trailing pause and the turn hangs until an
    // unrelated server-side fallback timeout finally closes it (the ~1
    // minute delay). Streaming real silence samples lets the server's VAD
    // observe the pause immediately, like it would if the mic just went quiet.
    this._sendSilencePadding();
    this.onStatus?.("Transcribing");
  }
  _sendPcmBytes(pcm) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const bytes = new Uint8Array(pcm.buffer); let binary=""; for (const b of bytes) binary += String.fromCharCode(b);
    this.ws.send(JSON.stringify({ type:"user_audio_chunk", user_audio_chunk:btoa(binary) }));
  }
  _sendPcm(float32) {
    const rate = this.ctx.sampleRate, target = 16000, ratio = rate / target, n = Math.floor(float32.length / ratio);
    const pcm = new Int16Array(n);
    for (let i=0;i<n;i++) { const x=float32[Math.min(float32.length-1, Math.floor(i*ratio))]; pcm[i]=Math.max(-1,Math.min(1,x))*0x7fff; }
    this._sendPcmBytes(pcm);
  }
  _sendSilencePadding(ms = 700) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const rate = 16000, chunkMs = 100, chunkSamples = Math.round(rate * chunkMs / 1000);
    for (let sent = 0; sent < ms; sent += chunkMs) this._sendPcmBytes(new Int16Array(chunkSamples));
  }
  _audioContext() {
    if (!this.ctx || this.ctx.state === "closed") this.ctx = new AudioContext();
    if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
    return this.ctx;
  }
  _play(b64) {
    try {
      // agent_output_audio_format is pcm_16000: signed 16-bit little-endian
      // samples, not an encoded WAV or MP3 file.
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const pcm = new Int16Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
      const ctx = this._audioContext();
      const buffer = ctx.createBuffer(1, pcm.length, 16000);
      const channel = buffer.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
      const src = ctx.createBufferSource(); src.buffer = buffer; src.connect(ctx.destination);
      this.playing.add(src); src.onended = () => this.playing.delete(src);
      // Queue this chunk right after whatever is already scheduled instead of
      // starting every chunk at "now" — that was the cause of overlapping,
      // garbled speech when several streamed chunks arrived close together.
      const startAt = Math.max(ctx.currentTime, this.nextPlayTime);
      src.start(startAt);
      this.nextPlayTime = startAt + buffer.duration;
      this.onAudio?.();
    } catch (error) { this.onError?.(new Error(`Voice playback failed: ${error.message}`)); }
  }
  stopAudio() { for (const src of this.playing) try { src.stop(); } catch {} this.playing.clear(); this.nextPlayTime = 0; }
  close() { this.stopAudio(); try { this.ws?.close(); } catch {} this.stream?.getTracks().forEach(t=>t.stop()); this.ctx?.close(); this.ws=null; this.stream=null; this.ctx=null; }
}
