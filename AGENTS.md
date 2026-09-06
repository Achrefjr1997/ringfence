# Ringfence — agent working agreement

## What this is
Ringfence observes a live voice call in real time, detects social-engineering
fraud, and intervenes on the side of the person at risk. It is an **observer**:
it never speaks to the adversary, never blocks a call, never moves money.

Design document (read the relevant section, not the whole file):
- `docs/RINGFENCE_PRODUCTION_DESIGN.md` — the full production system

## Non-negotiable invariants
Asserted in `tests/invariants/`. If your change breaks one, your change is wrong.

1. **A CALLEE's speech never raises the risk score.** A victim repeating a
   scammer's words must not incriminate themselves.
2. **AUTH_CLAIM + URGENCY alone never reaches ALERT.** Real banks do both.
3. **The LLM judge can never fire an intervention on its own.** Bounded to
   ±max_adjustment; rules retain control.
4. **In dry-run or replay mode, no external side effect ever occurs.**
5. **Transcripts are never written to disk** unless `RF_RETAIN_TRANSCRIPTS=true`.
6. **No unbounded queue and no unbounded retry** anywhere on the audio path.
7. **No transcript content in any API or webhook payload.** Ever.

## Layout
```
cmd/gateway/         Go: WS ingest, admission, SSE out
cmd/normalizer/      Go: resample, VAD, frame emission
pkg/ingest/          Go: frame ingestion contract
pkg/frame/           Go: Frame struct, PCM handling
pkg/session/         Go: session + leg lifecycle
pkg/audiosocket/     Go: Asterisk AudioSocket adapter
pkg/siprec/          Go: SIPREC SRS adapter
packages/contracts/  Python: types only, no logic, no I/O  ← keystone
packages/policy/     Python: policy pack loading + validation
packages/risk/       Python: extractors, scoring, state machine
packages/asr/        Python: ASR provider interface + implementations
packages/media/      Python: role inference (acoustic), VAD bridge
packages/ingress/    Python: Python-side adapter wiring
packages/intervene/  Python: interventions + notifications
packages/eval/       Python: corpus replay + metrics
apps/console/        Static: dashboard (HTML/JS)
```

## Rules
- **Types first.** Everything crossing a package boundary is a type in
  `packages/contracts` (Python) or `pkg/` (Go). Never duplicate a shape.
- **No I/O in `risk/` or `policy/`.** Pure functions only.
- **Test-first.** Write the failing test, show it fail, then implement.
- **No new dependencies** without asking. The allowed list is closed.
- **One task, one commit.** Never squash.
- **Type hints everywhere.** `make typecheck` must pass. `go vet` must pass.
- **If the spec is ambiguous, stop and ask.** Do not invent an API.

## Commands
```
make check    # lint + typecheck + test
make inv      # invariant suite
make eval     # full corpus evaluation
make run      # dev server on :8000
make dev-up   # NATS + Redis + Postgres + MinIO
```

## Commit format
```
<type>(<scope>): <summary>

Task: T-x.y
<what changed and why, 1-3 lines>
```
Types: `feat` `fix` `test` `refactor` `docs` `chore`.
