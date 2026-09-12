// RingFence verification desk (packages/verify/desk.py, apps/gateway/verify_desk.py).
//
// Plays the institution's fraud desk. Offers arrive over SSE; Answer redeems
// the single-use ticket over a WebSocket carrying 24 kHz PCM16 both ways:
// this page's microphone up to the Voice Agent, the agent's voice down.
//
// Unlike capture.js, echo cancellation stays ON here. The agent's voice comes
// out of this machine's speakers, and without AEC the microphone would hand it
// straight back to the agent as if the desk had said it.

const RATE = 24000;
const WS = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
// RF_VERIFY_DESK_TOKEN, when the gateway sets one: open this page as
// /verify-desk?token=... and it is forwarded to every desk endpoint.
const TOKEN = new URLSearchParams(location.search).get("token");
const Q = TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : "";
const $ = (id) => document.getElementById(id);
const els = {
  card: $("card"),
  state: $("state"),
  who: $("who"),
  answer: $("answer"),
  decline: $("decline"),
  hangup: $("hangup"),
  transcript: $("transcript"),
  echo: $("echo"),
  echoStatus: $("echo-status"),
  conn: $("conn"),
};
const baseTitle = document.title;

let ringing = null; // the offer currently ringing
let call = null; // the open audio line
let echo = null; // the open echo test
const declined = new Set();

// -- audio ------------------------------------------------------------------

async function openAudio(url, { onOpen, onEvent, onClose }) {
  const ctx = new AudioContext();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    await ctx.audioWorklet.addModule("/desk-worklet.js");
  } catch (err) {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    await ctx.close();
    throw err;
  }
  const mic = new AudioWorkletNode(ctx, "desk-capture");
  ctx.createMediaStreamSource(stream).connect(mic); // not to destination: no self-monitoring

  const ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  mic.port.onmessage = (e) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(e.data);
  };

  // Agent audio arrives in bursts faster than real time; schedule each chunk
  // to start where the previous one ends.
  let playhead = 0;
  ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      try {
        onEvent && onEvent(JSON.parse(e.data));
      } catch {
        /* not ours */
      }
      return;
    }
    const pcm = new Int16Array(e.data, 0, e.data.byteLength >> 1);
    if (!pcm.length) return;
    const buf = ctx.createBuffer(1, pcm.length, RATE); // the browser resamples to the context rate
    const out = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 32768;
    const node = ctx.createBufferSource();
    node.buffer = buf;
    node.connect(ctx.destination);
    playhead = Math.max(playhead, ctx.currentTime + 0.04);
    node.start(playhead);
    playhead += buf.duration;
  };

  let closed = false;
  const close = ({ drain = false } = {}) => {
    if (closed) return;
    closed = true;
    stream.getTracks().forEach((t) => t.stop());
    if (ws.readyState <= WebSocket.OPEN) ws.close();
    // On a server-side close, let the agent finish its goodbye first.
    const tail = drain ? Math.max(0, playhead - ctx.currentTime) : 0;
    setTimeout(() => ctx.close(), tail * 1000 + 50);
  };
  let opened = false;
  ws.onopen = () => {
    opened = true;
    onOpen && onOpen();
  };
  ws.onclose = () => {
    close({ drain: true });
    onClose && onClose(opened);
  };
  return { close };
}

// -- ringing ----------------------------------------------------------------

let ringTimer = 0;
function startRing() {
  let on = false;
  ringTimer = setInterval(() => {
    document.title = (on = !on) ? "📞 Incoming verification call" : baseTitle;
  }, 700);
}
function stopRing() {
  clearInterval(ringTimer);
  document.title = baseTitle;
}

// -- state ------------------------------------------------------------------

function show(state, label) {
  els.card.dataset.state = state;
  els.state.textContent = label;
  els.answer.hidden = state !== "ringing";
  els.decline.hidden = state !== "ringing";
  els.hangup.hidden = state !== "connected";
}

function idle(label = "Waiting for a verification call") {
  ringing = null;
  stopRing();
  show("idle", label);
}

function ring(offer) {
  ringing = offer;
  els.who.textContent = `${offer.institution} · ${offer.line_label}`;
  els.transcript.replaceChildren();
  show("ringing", "Incoming verification call");
  startRing();
}

function addLine(role, text, offer) {
  const li = document.createElement("li");
  li.className = role;
  const who = document.createElement("span");
  who.className = "role";
  who.textContent = role === "agent" ? "RingFence agent" : `You · ${offer.institution}`;
  li.append(who, document.createTextNode(text));
  els.transcript.append(li);
}

const VERDICT = {
  true: "Reported: the institution confirmed the call",
  false: "Reported: the institution has no record of the call",
  unknown: "Call ended without a clear answer",
};

async function answer() {
  const offer = ringing;
  if (!offer || call) return;
  ringing = null;
  stopRing();
  show("connecting", "Connecting…");
  let verdict = null;
  try {
    call = await openAudio(`${WS}/ws/verify-desk/${encodeURIComponent(offer.ticket)}${Q}`, {
      onOpen: () => show("connected", "On a verification call"),
      onEvent: (m) => {
        if (m.type === "transcript") addLine(m.role, m.text, offer);
        else if (m.type === "ended") verdict = m.verified;
      },
      onClose: (opened) => {
        call = null;
        if (!opened) idle("That call is no longer available");
        else idle(VERDICT[verdict] || "Call ended");
      },
    });
  } catch (err) {
    call = null;
    idle(`Microphone unavailable: ${err.message || err}`);
  }
}

els.answer.addEventListener("click", answer);
els.decline.addEventListener("click", () => {
  if (ringing) declined.add(ringing.ticket);
  idle("Declined");
});
els.hangup.addEventListener("click", () => call && call.close());

// -- echo test --------------------------------------------------------------

els.echo.addEventListener("click", async () => {
  if (echo) {
    echo.close();
    return;
  }
  els.echoStatus.textContent = "starting…";
  try {
    echo = await openAudio(`${WS}/ws/verify-desk/echo${Q}`, {
      onOpen: () => {
        els.echo.textContent = "Stop echo test";
        els.echoStatus.textContent = "speak — you should hear yourself";
      },
      onClose: () => {
        echo = null;
        els.echo.textContent = "Start echo test";
        els.echoStatus.textContent = "stopped";
      },
    });
  } catch (err) {
    echo = null;
    els.echoStatus.textContent = `microphone unavailable: ${err.message || err}`;
  }
});

// -- offers -----------------------------------------------------------------

const offers = new EventSource(`/verify-desk/offers${Q}`);
offers.onopen = () => (els.conn.textContent = "desk online");
offers.onerror = () =>
  (els.conn.textContent = TOKEN
    ? "desk offline — wrong desk token, or verification is off. retrying…"
    : "desk offline — is verification enabled (and a desk token required)? retrying…");
offers.addEventListener("offer", (e) => {
  const offer = JSON.parse(e.data);
  if (!call && !ringing && !declined.has(offer.ticket)) ring(offer);
});
offers.addEventListener("withdrawn", (e) => {
  const offer = JSON.parse(e.data);
  if (ringing && ringing.ticket === offer.ticket) idle("Missed call");
});
