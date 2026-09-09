// Browser / speakerphone capture wiring (T-3.4).
//
// The phone is on speaker; this page's microphone hears BOTH sides of the
// call. The three getUserMedia flags below are the single most common way
// this build silently fails — see the comments.

const GW = location.host || "localhost:8000";

let ctx = null;
let stream = null;
let ws = null;

export async function startCapture(sessionId, { onLevel, onStatus, onDecision, query = "&tenant=console" } = {}) {
  const status = (s) => onStatus && onStatus(s);

  // A speakerphone held to the mic is one mixed stream. Send leg=mixed so
  // the pipeline separates the far (telephone-band) party from the near
  // (room-mic) one acoustically and attributes each turn's role — rather
  // than pinning the whole call to one role. `query` carries ?key=/&tenant=
  // for the authenticated (non-dev) path; the leading `?` is already here.
  const q = query.startsWith("?") ? "&" + query.slice(1) : query;
  ws = new WebSocket(`ws://${GW}/ws/capture?session=${encodeURIComponent(sessionId)}&leg=mixed${q}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => status("connected");
  ws.onclose = () => status("closed");
  ws.onerror = () => status("error");
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === "rejected") status("rejected: " + m.reason);
    } catch {
      /* binary / non-JSON, ignore */
    }
  };

  stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: false, // CRITICAL: AEC deletes the far-end (loudspeaker) voice
      noiseSuppression: false, // NS mangles the band-limited phone voice
      autoGainControl: false, // AGC destroys the level cue role inference needs
    },
  });

  ctx = new AudioContext({ sampleRate: 48000 });
  await ctx.audioWorklet.addModule("/worklet.js");
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "ringfence-capture");

  // Two level meters: full band, and <= 3.4 kHz only (telephone band). If the
  // other person is talking and ONLY the full-band meter moves, the far-end
  // voice is band-limited and present; if NEITHER moves during their turn,
  // echo cancellation is on and eating it.
  const wide = ctx.createAnalyser();
  wide.fftSize = 1024;
  const tele = ctx.createAnalyser();
  tele.fftSize = 1024;
  const lp = ctx.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.value = 3400;

  src.connect(node);
  src.connect(wide);
  src.connect(lp).connect(tele);

  node.port.onmessage = (e) => {
    const m = e.data;
    if (m.type === "frame") {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(m.buffer);
    }
  };

  const wideBuf = new Float32Array(wide.fftSize);
  const teleBuf = new Float32Array(tele.fftSize);
  const rms = (b) => Math.sqrt(b.reduce((a, x) => a + x * x, 0) / b.length);
  let raf = 0;
  const tick = () => {
    wide.getFloatTimeDomainData(wideBuf);
    tele.getFloatTimeDomainData(teleBuf);
    onLevel && onLevel({ wide: rms(wideBuf), tele: rms(teleBuf) });
    raf = requestAnimationFrame(tick);
  };
  tick();
  ctx._stopMeter = () => cancelAnimationFrame(raf);

  status("capturing");

  // Live decisions for this session over SSE.
  if (onDecision) {
    const es = new EventSource(`http://${GW}/events/${encodeURIComponent(sessionId)}${query || ""}`);
    es.addEventListener("decision", (ev) => onDecision(JSON.parse(ev.data)));
    es.addEventListener("end", () => es.close());
    ctx._events = es;
  }
}

export function stopCapture() {
  if (ctx && ctx._stopMeter) ctx._stopMeter();
  if (ctx && ctx._events) ctx._events.close();
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (ctx) ctx.close();
  if (ws && ws.readyState <= 1) ws.close();
  ctx = stream = ws = null;
}
