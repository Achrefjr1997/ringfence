# SIPREC ingress adapter (Adapter A — carrier, `[P1 revenue path]`)

`docs/DESIGN_PRODUCTION.md` §3.2. The operator's SBC forks call media to
RingFence over SIPREC (RFC 7866): a SIP `INVITE` carrying SDP **and**
`rs-metadata` (RFC 7865), plus **two RTP streams, one per participant**.
RingFence is the Session Recording Server (SRS); the SBC is the client (SRC).

Why it matters:

* **Exact role attribution.** Two labelled streams — caller and callee are
  known from metadata, not inferred acoustically. This is the ground truth
  every other adapter is measured against (§11.6).
* **Signalling for free.** Calling number, trunk, STIR/SHAKEN attestation
  where the operator has it → enrichment (§6.4).
* **Passive by construction.** RingFence receives a fork; it is never in the
  call path and cannot drop a call even if it crashes.

The engine downstream of `/ws/capture` is unchanged. The SRS is a **standalone
client** of `/ws/capture`, one WebSocket per leg (`leg=far` → caller,
`leg=near` → callee), exactly as the browser two-socket SDK path already works
(`apps/gateway/app.py::capture`). Nothing new is wired into the gateway.

No new dependencies: SIP/SDP are text, RTP is a 12-byte header, G.711 is a
lookup table, and `packages/media/normalise.resample_to_16k` (scipy, already a
dep) does the rate conversion. `websockets` (already a dep) is the uplink.

---

## Phases (one PR each, against `main`)

### P1 — protocol core `packages/ingress/siprec/` — pure, no sockets ← this PR

| module | surface |
|---|---|
| `g711.py` | `ulaw_decode(bytes) -> NDArray[int16]`, `alaw_decode(...)`, `decode(payload, pt)` — vectorised ITU-T G.711 tables |
| `rtp.py` | `RtpPacket.parse(bytes) -> RtpPacket` (RFC 3550: CSRC list, header-extension skip, padding trim); `SeqReorderer` — bounded seq-window reorder, 16-bit wrap-safe, drops late/duplicate |
| `sdp.py` | `parse_offer(str) -> SdpOffer` (per-`m=` line: media kind, port, payload-type→codec map, direction); `build_answer(offer, *, local_ip, ports) -> str` — `recvonly`, echoes only the payload types we decode |
| `sipmsg.py` | `parse_message(bytes) -> SipMessage` (request/status line, folded headers, body); `split_multipart(body, boundary)`; `build_response(req, code, reason, *, extra_headers, body, content_type)` — copies `Via`/`From`/`To`/`Call-ID`/`CSeq`, adds `tag` |
| `metadata.py` | `parse_recording_metadata(xml) -> RecordingMetadata` (RFC 7865): `session_id`, `participants[]` (aor, name, `nameID`), `streams[]` (`label`, ssrc), and `role_for_stream(label) -> "caller"\|"callee"\|None` from participant `<send>`/`<recv>` associations |

Full offline unit coverage (`tests/unit/test_siprec_*.py`). No marker — runs in
the default CI lane.

### P2 — the SRS server `packages/ingress/siprec/srs.py` ✅ (PR #55)

`SiprecSrs` — asyncio UDP, modelled on `AudioSocketServer`:

* one SIP dialog: `INVITE` → `200 OK` with the answer SDP → `ACK`; `BYE` /
  `CANCEL` → `200 OK`; `OPTIONS` → `200 OK` + `Allow` (SBC keepalive — design
  §3.2 failure mode); retransmitted `INVITE` re-sends the stored `200 OK`;
  `close()` tears every live dialog down as `"shutdown"`.
* one RTP receiver per answered port. Per packet: `RtpPacket.parse` →
  `SeqReorderer` → `g711.decode` → `AudioNormaliser` (shared `SessionNormaliser`
  across legs — normalise the session, not each leg) → a bounded per-dialog
  queue → `on_audio(session_id, leg, 40 ms PCM16 @ 16 kHz)`. `datagram_received`
  never blocks; a full queue drops.
* `on_session_start(SiprecSession)` / `on_session_end(session_id, reason)`.
* leg id: `metadata.leg_for_label` + `caller_aor` → `far` / `near`; otherwise
  `leg-<label>` (capture path falls back to acoustic — degraded, never wrong).

Covered by `tests/unit/test_siprec_srs.py` — a scripted SBC over real
localhost UDP: `INVITE` + RTP + `BYE`, `OPTIONS` keepalive, `INVITE` with no
SDP → `488`, garbage datagrams ignored.

### P3 — uplink + local dev SRC ✅ (PR #56)

* `apps/siprec/` — `SiprecUplink` runs a `SiprecSrs` and, per `(session, leg)`,
  opens one `websockets` client to `/ws/capture` (`session=` / `leg=` from the
  SRS, `key=` from `RF_SIPREC_API_KEY`, optional `tenant=`). Binary 40 ms
  frames go straight onto the socket; a reader logs any `rejected` message.
  `python -m apps.siprec` runs it, env-configured, with SIGINT/SIGTERM
  teardown. The gateway is untouched — this is the browser two-socket path.
* `packages/ingress/siprec/loopback.py` — a loopback SIPREC *client*:
  `play_call(srs, far, near, ...)` sends a real `INVITE` + `rs-metadata` + two
  µ-law RTP streams + `BYE` over UDP. `python -m packages.ingress.siprec.loopback
  --far a.wav --near b.wav --speed 1` for a live local call; `speed=0` (no
  pacing) drives the tests. Needs `g711.ulaw_encode` (added — nearest-level
  inverse of the decode table).
* Covered by `tests/unit/test_siprec_uplink.py` — loopback → SRS → uplink → a
  stand-in `/ws/capture`, asserting one binary WS per leg, keyed and tagged.

**Local run** (all on `localhost`, ephemeral RTP ports — no compose overlay
needed): issue an API key in the console, then

    RF_SIPREC_API_KEY=rf_... RF_SIPREC_CALLER_AOR=sip:caller@pstn \
      python -m apps.siprec                       # SRS on udp/5060 -> :8000
    python -m packages.ingress.siprec.loopback \
      --far corpus/fixtures/audio/<caller>.wav \
      --near corpus/fixtures/audio/<callee>.wav --speed 1

The call then shows up in the console with a live score, transcript, and —
audio retention being on — a recording. A `docker-compose.siprec.yml` overlay
+ a configurable RTP port range (for an external FreeSWITCH/Asterisk SRC) come
with P4.

### P4 — carrier mode, attribution, admission, metrics ✅ (PR #57)

* **`mode=carrier`** — `/ws/capture` reads a `mode=` hint (`carrier` /
  `enterprise`, else `SDK` as before); the uplink sends `carrier`, so the
  `SessionDescriptor` / `SessionSnapshot` carry `Mode.CARRIER`.
* **Callee attribution** — `RecordingMetadata.other_participant(caller_aor=…)`
  is the non-caller; the uplink passes its AOR / name as `user=` / `user_label=`
  so a carrier call is attributed in the console.
* **Consent** — `RF_SIPREC_CONSENT_TOKEN`, when set, is passed as `consent=`
  (`admit()` already enforces it in the §3.6 order).
* **RTP port range** — `SiprecSrs(rtp_port_range=(lo, hi))` /
  `RF_SIPREC_RTP_PORTS=35000-35099`; ephemeral when unset. An external SBC can
  now be firewalled to a known span.
* **Metrics** — `SiprecSrs.stats()` (`siprec_sessions_total` / `_active`,
  `siprec_rtp_packets_total`, `siprec_rtp_lost_total`,
  `siprec_reorder_depth_max`) exposed by a dependency-free Prometheus text
  endpoint in `apps/siprec` (`RF_SIPREC_METRICS_PORT`, default 9105).
* **`infra/compose/docker-compose.siprec.yml`** — overlay on
  `docker-compose.prod.yml` running `python -m apps.siprec` on the gateway
  image; udp/5060 + the RTP range published, `/metrics` on 9105.

---

## Not in scope

* SRTP / mTLS termination — done at the SBC or a TLS front (`infra/`), not here.
* AMR-WB / EVS / Opus decode — G.711 (PT 0/8) is the carrier baseline; add
  codecs behind `g711.decode`'s dispatch when a deployment needs them.
* Full SIP transaction state machine — a SIPREC SRS handles exactly one
  `INVITE`/`BYE` dialog per call plus stateless `OPTIONS`; nothing more.
