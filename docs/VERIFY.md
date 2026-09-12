# Verification agent

> Off by default. `RF_VERIFY_ENABLED=true` turns it on; `RF_VERIFY_AGENT=voice_agent`
> makes it real. Code: `packages/verify/`, `apps/gateway/verify_dispatch.py`,
> `apps/gateway/verify_desk.py`, `apps/console/verify-desk.*`.

Most scam-call tools score the call and show a warning. This one goes further:
when a call reaches **INTERVENE** and the caller has named an institution, an
agent **independently checks with that institution** while the call is still
live. It asks one question and reports the answer to the person being targeted:

> *"We called Amazon directly. They have no record of this call. Hang up now."*

The conversation runs on the AssemblyAI **Voice Agent API** (speech-to-speech
with tool calling) over a WebSocket. **There is no telephony and no phone number
anywhere in this feature.** The institution's side is a *verification desk*, a
page we serve (`/verify-desk`), where a person or a script answers.

## Where this sits against "RingFence is an observer"

`AGENTS.md` says RingFence never speaks to the caller, never blocks a call and
never moves money. All three still hold. The verification agent is the **one
documented exception** to "only ever listens". It may hold one short
conversation, and only with the desk of the institution the caller *claimed*
to represent. We treat that as a real side effect and constrain it as one:

| Property | How it is enforced |
|---|---|
| Never speaks to the caller | The agent's only audio peer is the desk line. It has no path to the protected call. |
| Never contacts an arbitrary destination | Destinations come only from `config/verify/directory.yaml`, looked up by `Directory.resolve()`. `packages/verify/` contains no number or endpoint parser, and a unit test greps for one. |
| The victim cannot trigger it | Only CALLER speech names the institution (UNKNOWN speech as a fallback, CALLEE never). Someone repeating "Amazon" back to the scammer contacts nobody. |
| Always says it is automated | The greeting is hard-coded, not configurable, and tested. |
| Never invents specifics | The model gets the institution name and desk only. It gets the amount only if one was extracted from CALLER speech; otherwise the prompt contains no digit at all (tested). |
| Nothing in dry-run or replay | `may_open_session()` runs before the desk is rung, not just before the connect. Invariant #4 has three tests for this. |
| Bounded cost | One verification per call, 3 per tenant per hour, 1 at a time, a 120 s hard cap, and `session.end` always sent (closing the socket alone does not stop billing). No retries. |

The directory holds **demo entries only**, and every `desk_id` is a desk we
serve. Never add a real institution's genuine contact route.

## Flow

```
caller: "…this is Amazon account security…"        rf.<tenant>.turn   (role CALLER)
pipeline → INTERVENE                                  rf.<tenant>.decision
VerificationDispatcher
  resolve institution from CALLER text               → stage "dialing"
  budget: once per call · 3/h per tenant · 1 at a time
VoiceAgentVerifier
  guard (dry-run / replay → stop, nobody is paged)
  DeskExchange.offer → single-use ticket, 30 s        → stage "ringing"
  desk answers: WS /ws/verify-desk/{ticket}          → stage "connected"
  VoiceAgentSession (wss://agents.assemblyai.com/v1/ws)
    session.update: prompt + report_verification tool
    desk mic → input.audio · reply.audio → desk speaker
    transcript.user / transcript.agent               → stage "transcript" (each line)
    tool.call report_verification(verified, reason)
    tool.result after reply.done · goodbye · session.end
                                                     → stage "result"
                                                     → rf.<tenant>.warning VERIFY_*
```

The protected person's console shows every stage on the coaching banner. The
final `VERIFY_*` warning uses the existing template path, so it is translated
(en / fr / ar_tn).

### Outcomes

`verified` has three states on purpose. **Never coerce `None` to `False`**:
"we could not reach them" is not "they denied calling".

| `verified` | Banner template | Typical cause |
|---|---|---|
| `false` | `VERIFY_UNCONFIRMED` | The desk said it has no record of the call |
| `true` | `VERIFY_CONFIRMED` | The desk confirmed the call |
| `None` | `VERIFY_FAILED` | `no_answer`, `desk_hangup`, `duration_cap`, `connect:*`, an AssemblyAI error code |
| (verifier returned nothing) | stage `failed`, no banner | The guard refused, or the verifier raised |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RF_VERIFY_ENABLED` | `false` | Master switch. |
| `RF_VERIFY_AGENT` | `simulated` | `simulated`: no conversation, and every result is labelled "simulated" on screen. `voice_agent`: the real session. |
| `ASSEMBLYAI_API_KEY` | — | Required for `voice_agent`. Used server-side only; the browser never sees it. |
| `RF_DRY_RUN` | `true` | The real session is refused under dry-run. `voice_agent` with dry-run on falls back to simulated **and logs a warning**. |
| `RF_VERIFY_DESK_TOKEN` | unset | Shared secret for the desk endpoints. **Set it on any public URL**: whoever answers the desk decides the verdict. Open the page as `/verify-desk?token=…`. |

The base `docker-compose.yml` pins `RF_DRY_RUN=true` (and a test enforces it).
The real agent is a separate, explicit overlay:

```bash
docker compose --env-file .env \
  -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.verify-live.yml up -d --build gateway
```

Add `--force-recreate` if compose reports the gateway as `Running` after code
changes; otherwise the old container keeps serving.

## Running the demo

1. Start the gateway with the overlay above.
2. **Tab 1**, `http://localhost:8000` → Live: start a capture, or use *Feed audio
   file* with a scam recording that names Amazon (`demo-scam-call.wav`).
3. **Tab 2**, `http://localhost:8000/verify-desk`: run the **echo test** first,
   using headphones. Echo cancellation is on, but without headphones the agent
   can hear itself through your speakers.
4. When the call reaches INTERVENE, tab 2 rings ("Amazon · account security").
   Click **Answer**. The agent introduces itself as automated and asks whether
   Amazon placed the call. Answer as the desk would, e.g. *"No, we have no
   record of calling that customer."*
5. Tab 1 shows *Calling Amazon's verification desk…* → the live transcript →
   **"We called Amazon directly. They have no record of this call. Hang up now."**

A whole verification takes about 30–40 s, mostly the agent speaking.

## Metrics

`/metrics` exposes these when verification is enabled:

- `ringfence_verifications_total{outcome, simulated}`, where outcome is `confirmed` · `unconfirmed` · `unanswered` · `failed`
- `ringfence_verifications_skipped_total{reason}`, where reason is `no_institution` · `already_verified` · `concurrent` · `tenant_hourly`
- `ringfence_verification_errors_total{error}`, the cause of an unanswered check (prefix only, so label values stay bounded)
- `ringfence_verification_agent_seconds_total`, real Voice Agent session time. Multiply by $4.50/h for spend.

If `no_institution` climbs, callers are naming institutions that aren't in the
directory. If `no_answer` climbs, nobody is watching the desk.

## Known limits

- **English only.** The prompt and the desk conversation are English, even
  though the banner is translated.
- **Amount extraction reuses `AMOUNT_RE`**, which matches "500 dollars" but not
  "$500". A missed amount is just left out, which is the safe direction.
- **The desk is a demo stand-in.** It proves the agent's behaviour end to end.
  It is not an integration with any real institution.
- **Budgets are in-memory and per gateway process**, like the rest of the MVP.

## Tests

- `tests/unit/test_verify_*.py`: wire codecs, prompt, directory, budget,
  session (scripted fake socket), desk exchange, live verifier, dispatcher, stats.
- `tests/integration/test_verify_desk_gateway.py`: tickets refused before
  accept, audio both ways, hang-up, echo, desk token.
- `tests/invariants/test_dry_run.py`: no session and no ring in dry-run or replay,
  plus a non-vacuous live-mode check.
