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

### P3 — uplink + local dev SRC `apps/siprec/__main__.py`

* wires `SiprecSrs` callbacks to `/ws/capture` — a `websockets` client per leg,
  `key=` from `RF_SIPREC_API_KEY`, `session=` from metadata, `leg=far|near`
  from `role_for_stream`, `user`/`user_label` from the participant `nameID`.
* `scripts/siprec_send.py` — a minimal SRC test double: `INVITE`s the SRS with
  real `rs-metadata` and streams the two `corpus/fixtures/audio/*` legs as RTP
  at real-time pace. Deterministic two-leg ground truth, no carrier, no
  FreeSWITCH.
* `infra/compose/docker-compose.siprec.yml` — optional overlay running
  `apps/siprec` against the gateway; doc for pointing FreeSWITCH `mod_siprec` /
  Asterisk at it.

### P4 — admission + signalling enrichment

* metadata `<participant>` numbers → `Signalling` (caller/callee E.164, trunk,
  attestation) on the session descriptor → §6.4 enrichment.
* consent-token check (§3.6) before the first frame; rejections counted per
  reason.
* `Mode.CARRIER` on the `SessionDescriptor`; SRS metrics (`siprec_sessions`,
  `siprec_rtp_lost`, `siprec_reorder_depth`).

---

## Not in scope

* SRTP / mTLS termination — done at the SBC or a TLS front (`infra/`), not here.
* AMR-WB / EVS / Opus decode — G.711 (PT 0/8) is the carrier baseline; add
  codecs behind `g711.decode`'s dispatch when a deployment needs them.
* Full SIP transaction state machine — a SIPREC SRS handles exactly one
  `INVITE`/`BYE` dialog per call plus stateless `OPTIONS`; nothing more.
