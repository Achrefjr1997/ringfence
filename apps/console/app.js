// RingFence console live view (T-4.3).
// One EventSource -> three regions: pressure gauge, role-coloured transcript
// with signal chips, and a signal/transition timeline. The timeline is the
// point: it makes the slope visible, and the slope is the story.

const STATES = ["CALM", "WATCH", "ALERT", "INTERVENE"];

export function mountConsole(sessionId, els, query = "") {
  const { gauge, gaugeLabel, transcript, timeline, cf, status } = els;
  const es = new EventSource(`/events/${encodeURIComponent(sessionId)}${query}`);
  const marks = []; // {t, kind: "signal"|"transition", label, state}
  let maxT = 30;
  let peak = 0;

  es.onopen = () => status && (status.textContent = "watching " + sessionId);
  es.onerror = () => status && (status.textContent = "stream error");

  es.addEventListener("turn", (e) => {
    const d = JSON.parse(e.data);
    maxT = Math.max(maxT, d.t_end + 5);
    const row = document.createElement("div");
    row.className = "turn turn-" + d.role;
    const role = document.createElement("span");
    role.className = "role";
    role.textContent = d.role;
    const txt = document.createElement("span");
    txt.className = "txt";
    txt.textContent = d.text;
    row.append(role, txt);
    for (const s of d.signals || []) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = s;
      row.append(chip);
      marks.push({ t: d.t_start, kind: "signal", label: s });
    }
    transcript.append(row);
    transcript.scrollTop = transcript.scrollHeight;
    drawTimeline();
  });

  es.addEventListener("decision", (e) => {
    const d = JSON.parse(e.data);
    maxT = Math.max(maxT, d.t + 5);
    peak = Math.max(peak, d.score);
    setGauge(d.score, d.state);
    marks.push({ t: d.t, kind: "transition", label: d.state, state: d.state });
    if (cf) cf.textContent = d.counterfactual || "";
    drawTimeline();
  });

  es.addEventListener("warning", (e) => {
    const d = JSON.parse(e.data);
    if (!els.coach) return;
    els.coach.textContent = d.text;
    els.coach.classList.add("live");
    // The static template publishes first, instantly; if an LLM coach is
    // configured (packages/intervene/coach.py) a richer sentence reasoning
    // over every active signal follows a moment later as a second warning
    // event with this template_id -- tag it so the refinement is visible,
    // not just felt.
    if (d.template_id === "LLM_COACH") {
      const src = document.createElement("span");
      src.className = "src";
      src.textContent = "agent";
      els.coach.append(src);
    }
    // A simulated verification (packages/verify/simulated.py) produces a
    // result nobody actually obtained. Say so on screen, every time.
    if (d.simulated) {
      const sim = document.createElement("span");
      sim.className = "src";
      sim.textContent = "simulated";
      els.coach.append(sim);
    }
  });

  // Verification agent progress (packages/verify). The outcome arrives as a
  // warning event above, already translated; these are the steps before it,
  // so the person watches the check happen instead of waiting in silence.
  // "skipped" and "failed" stay off the banner on purpose: operator
  // information, not something the person on the call needs to read.
  es.addEventListener("verification", (e) => {
    const d = JSON.parse(e.data);
    if (!els.coach) return;
    const who = d.institution_display;
    let text;
    if (d.stage === "dialing") text = `Verifying with ${who}…`;
    else if (d.stage === "ringing") text = `Calling ${who}'s verification desk…`;
    else if (d.stage === "connected") text = `${who} answered. Asking whether they called you…`;
    else if (d.stage === "transcript") text = `${d.role === "agent" ? "RingFence" : who}: “${d.text}”`;
    else return;
    els.coach.textContent = text;
    els.coach.classList.add("live");
  });

  es.addEventListener("end", () => {
    es.close();
    status && (status.textContent = "session ended");
  });

  function setGauge(score, state) {
    const pct = Math.max(0, Math.min(100, score));
    gauge.style.height = pct + "%";
    gauge.dataset.state = state;
    gaugeLabel.textContent = `${Math.round(score)} · ${state}`;
    gaugeLabel.dataset.state = state;
  }

  function drawTimeline() {
    const W = 680;
    const H = 104;
    const pad = 26;
    const x = (t) => pad + (t / maxT) * (W - 2 * pad);
    const step = Math.max(5, Math.round(maxT / 6 / 5) * 5);
    const parts = [`<svg viewBox="0 0 ${W} ${H}" class="tl">`];
    parts.push(`<line x1="${pad}" y1="${H - 20}" x2="${W - pad}" y2="${H - 20}" class="axis"/>`);
    for (let s = 0; s <= maxT; s += step) {
      parts.push(`<text x="${x(s)}" y="${H - 6}" class="tick">${s}s</text>`);
    }
    for (const m of marks) {
      const px = x(m.t).toFixed(1);
      if (m.kind === "signal") {
        parts.push(`<line x1="${px}" y1="34" x2="${px}" y2="${H - 20}" class="sig"/>`);
        parts.push(
          `<text x="${px}" y="30" class="siglab" transform="rotate(-38 ${px} 30)">${m.label}</text>`,
        );
      } else {
        parts.push(`<line x1="${px}" y1="6" x2="${px}" y2="${H - 20}" class="tr tr-${m.state}"/>`);
        parts.push(`<text x="${px}" y="4" class="trlab tr-${m.state}">${m.label}</text>`);
      }
    }
    parts.push("</svg>");
    timeline.innerHTML = parts.join("");
  }

  drawTimeline();
  return () => es.close();
}

export { STATES };
