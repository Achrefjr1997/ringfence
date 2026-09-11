# Live-call demo over Telnyx

Ring a number from a real handset, talk, watch the score climb. Nothing is
installed on the phone being protected.

Telnyx rather than Twilio for one reason: **Twilio would not send an SMS
verification code to a +216 number**, so the account could not be created.
Telnyx accepted the same number. The ingress is provider-neutral
(`packages/ingress/mediastream/`), so that was a config change, not a
rewrite — `apps/twilio/` still works anywhere Twilio does.

## What this demo does and does not prove

**Does:** a real inbound call, forked at the network, scored in real time,
with the protected person on an ordinary phone using the native dialer.
This is the production architecture — only the number's country changes.

**Does not:** capture audio from a personal line you do not provision. That
remains impossible (iOS never exposed call audio; Android closed the
Accessibility-API route in May 2022). RingFence protects lines you
provision. Say that to prospects before a pilot discovers it.

## The two credentials, which are not interchangeable

| Variable | What it is | Where |
|---|---|---|
| `RF_TELNYX_PUBLIC_KEY` | Telnyx's Ed25519 **public** key. Verifies webhooks *they* send *us*. | Portal → Account Settings → **Public Key** |
| `RF_TELNYX_GATEWAY_KEY` | A **RingFence** API key. Authenticates *us* to our own `/ws/capture`. | your RingFence org's API keys |

A Telnyx **API key** (`KEY0123...`, from Portal → **API Keys**) is needed
for neither. It drives the Call Control REST API; this ingress is webhook +
TeXML only, so it never authenticates outbound to Telnyx. If you paste one
into `RF_TELNYX_GATEWAY_KEY` the app refuses to start and names both
variables — `packages/ingress/capture_uplink.py::read_gateway_key`.

⚠️ **API keys are bearer credentials.** Anything that has one can spend
money on the account. Never commit one, never paste one into a chat or an
issue; if one leaks, delete it in the portal and issue a new one. Deleting
is free and instant, and a key that has never been used has nothing
depending on it.

## Setup

### 1. Telnyx portal

1. **Buy a number.** Any country Telnyx sells. A Tunisian handset can ring a
   French or UK number; the demo does not need a +216 DID.
2. **Create a TeXML Application** (Voice → TeXML Applications) with its
   voice webhook pointed at `https://<tunnel>/voice`, method POST.
3. **Assign the number** to that application.
4. Copy the **Public Key** from Account Settings.

Two things to check in the portal rather than trust from here, because
trial terms change and a demo is the wrong place to discover them:

- whether a **pretrial/trial** account restricts inbound calls to verified
  numbers, as Twilio's does. If it does, verify both handsets first.
- the **termination rate to Tunisian mobile** for the `<Dial>` leg. The
  per-minute CPaaS cost is cents; international mobile termination is the
  part worth reading before you loop it all afternoon.

### 2. Local

```sh
cp .env.example .env          # fill in the Telnyx block
docker compose up -d          # gateway on :8000
python -m apps.telnyx         # ingress on :8101
cloudflared tunnel --url http://localhost:8101
```

The tunnel URL goes in `RF_TELNYX_PUBLIC_URL` **and** in the TeXML
application's webhook. Restart `apps.telnyx` after setting it — the stream
URL is derived from it at construction.

### 3. The call

Ring the Telnyx number from a handset. TeXML forks both tracks to
`wss://<tunnel>/media` and dials `RF_TELNYX_DIAL_TO`:

```
caller ──▶ Telnyx number ──<Dial>──▶ RF_TELNYX_DIAL_TO (native dialer, no app)
              │
              └─<Start><Stream track="both_tracks">──▶ /media ──▶ /ws/capture
```

`inbound` → leg `far` → `RoleHint.CALLER`; `outbound` → `near` →
`RoleHint.CALLEE`. That split is why scoring can weight the caller at 1.0
and the callee at 0.0 instead of falling back to acoustic attribution.

## Demo day

**Run it in French or English.** AssemblyAI transcribes no Arabic, so Derja
scores on signalling only. French detection is real (#66) and the French
warning copy exists.

The French bank-impersonation script in
`tests/integration/test_language_resolution.py` hits `URGENCY`,
`VERIF_INVERT` and `RAIL_UNUSUAL` — a reliable climb to ALERT.

**Consent.** This records a live call. Tunisia's position on call recording
is not something I could establish from public sources. Colleagues
role-playing who know they are being recorded is fine; before any real
customer call, `docs/LEGAL_AUDIO.md` is the checklist.

## If it does not work

| Symptom | Cause |
|---|---|
| 403 from `/voice`, `<Hangup/>` | `RF_TELNYX_PUBLIC_KEY` wrong or absent. Public Key page, not API Keys. |
| 503 from `/voice` | `cryptography` missing — install the `db` extra. Deliberately not a 403: "cannot verify" is not "forged". |
| Webhook never arrives | Tunnel URL not in the TeXML app, or the number not assigned to it. |
| Call connects, score stays 0 | Check `/metrics` on :8101 for `ringfence_capture_legs_open`. 0 = the media socket never opened; 2 = audio is flowing and the problem is downstream. |
| App exits naming two variables | A provider credential in the gateway-key slot. |

## Not this, for production

`<Siprec>` can target the SRS in `packages/ingress/siprec/` (#54–57), and
that is the carrier/enterprise path. It needs SIP over TLS, SRTP, digest
auth and a public 5060/5061 plus a wide UDP media range — none of which is
written. Media Streams needs one `wss://` on 443. See `docs/SIPREC.md`.
