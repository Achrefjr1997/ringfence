# Call-audio recording — legal checklist (T-7.0 / P7)

RingFence's default posture is **observer-only, no audio stored**
(`docs/DESIGN_PRODUCTION.md`; stricter than invariant #5). Phase 7 adds an
**opt-in** recording path. It is off unless **both** switches are on:

* `RF_RETAIN_AUDIO=true` (global), and
* `retain_audio: true` for the tenant in `config/tenants.yaml`.

Do not enable either in production until counsel has signed off on every
item below. This file is the checklist, not legal advice.

## What P7 does when enabled

* Tees the raw PCM of each call leg into memory during the call, mixes the
  legs on close, encodes **Ogg/Opus**, and writes it to the object store
  at `tenant/YYYY-MM/<session>.opus`.
* Records `audio_key`, `audio_bytes`, `audio_retain_until` on
  `call_ledger`. An hourly sweep deletes objects past `audio_retain_until`
  (`RF_AUDIO_RETENTION_DAYS`, default 7) and nulls the columns.
* Serves the file from `GET /calls/{id}/audio` — access-checked exactly
  like the call detail (owner / share / mention / admin; private calls
  stay private), with **every play and download written to the P5 access
  audit log**, including the actor and source IP.

## Sign-off checklist

1. **Lawful basis for recording**, per jurisdiction of the *callee* and
   the *caller*: one-party vs. all-party consent (US state-by-state; many
   EU/UK, AU, CA regimes require all-party). If the customer's own call
   flow already captures consent, confirm it covers a third-party
   processor retaining the audio for fraud review.
2. **GDPR / UK-GDPR** (if any data subject is in scope): Art. 6 basis
   (likely 6(1)(f) legitimate interest — document the balancing test) and,
   because scam calls surface financial and sometimes special-category
   data, an Art. 9 condition. DPIA completed.
3. **Data Processing Agreement** between RingFence and the customer naming
   audio as a processing purpose, with RingFence as processor.
4. **Sub-processors**: the object-store host (and any future S3/CDN)
   listed and flowed down in the DPA.
5. **Retention**: `RF_AUDIO_RETENTION_DAYS` set to the shortest defensible
   period; documented in the customer-facing privacy notice.
6. **Data-subject rights**: a runbook for access/erasure requests that
   reaches the object store, `call_transcript`, and the ledger row.
7. **Wiretap / interception statutes** (US ECPA, UK IPA, etc.): confirm a
   fraud-prevention processor storing a forked copy is permitted, or that
   an exception applies.
8. **Security**: encryption at rest for the object store volume; access to
   `GET /calls/{id}/audio` limited to the roles above; audit log
   retention ≥ the audio retention.
9. **Breach**: audio in scope of the incident-response plan
   (`docs/RUNBOOKS.md`).

## Turning it off

Set `RF_RETAIN_AUDIO=false` (or drop the tenant's `retain_audio`). New
calls stop recording immediately. Existing objects age out on the sweep;
to purge now, set `RF_AUDIO_RETENTION_DAYS=0` for one sweep cycle, or
delete the `call_audio` volume.
