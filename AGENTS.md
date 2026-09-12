# Ringfence — agent working agreement

## What this is
Ringfence listens to a live phone call, detects social-engineering fraud in
real time, and warns the person being scammed. It is an **observer**: it never
speaks to the caller, never blocks a call, never moves money.

**One documented exception, off by default:** the verification agent
(`packages/verify`, `docs/VERIFY.md`). With `RF_VERIFY_ENABLED=true`, an
INTERVENE whose caller named a listed institution may open one short,
self-identifying conversation with *that institution's desk* to ask whether
it placed the call. It still never speaks to the caller. It may only reach
desks listed in `config/verify/directory.yaml`, never takes a destination from
anything said on the call, and never runs in dry-run or replay (invariant #4).

Design documents (read the relevant section, not the whole file):
- `docs/DESIGN_MVP.md` — the buildable system
- `docs/DESIGN_PRODUCTION.md` — the interfaces we are building against

## Non-negotiable invariants
These are asserted in `tests/invariants/`. If your change breaks one, your
change is wrong — do not edit the test to make it pass.

1. **A CALLEE's speech never raises the risk score.** A victim repeating a
   scammer's words must not incriminate themselves.
2. **AUTH_CLAIM + URGENCY alone never reaches ALERT.** Real banks do both.
3. **The LLM judge can never fire an intervention on its own.** It is bounded
   to ±30 points; rules retain control.
4. **In dry-run or replay mode, no external side effect ever occurs.**
5. **Transcripts are never written to disk** unless `RF_RETAIN_TRANSCRIPTS=true`.
6. **No unbounded queue and no unbounded retry** anywhere on the audio path.

## Layout
```
packages/contracts/   types only, no logic, no I/O  ← the keystone
packages/policy/      policy pack loading + validation
packages/risk/        extractors, scoring, state machine
packages/asr/         ASR provider interface + implementations
packages/media/       resampling, VAD, role inference
packages/ingress/     capture adapters
packages/intervene/   interventions + notifications
packages/verify/      verification agent (off by default) — docs/VERIFY.md
packages/eval/        corpus replay + metrics
apps/gateway/         FastAPI: WebSocket ingest, SSE out
apps/console/         static dashboard
```

## Rules
- **Types first.** Everything crossing a package boundary is a type in
  `packages/contracts`. Never duplicate a shape.
- **No I/O in `risk/` or `policy/`.** Pure functions. This is what makes the
  test suite fast and the evaluation harness possible.
- **Test-first.** Write the failing test, show it fail, then implement.
- **No new dependencies** without asking. The allowed list is in
  `pyproject.toml` and it is closed.
- **Any consumer of `EventBus.subscribe()` that may stop iterating early
  (disconnect, session end, `break`) MUST wrap it in `contextlib.aclosing()`.**
- **One task, one commit.** Never squash — commit history is graded.
- **Type hints everywhere.** `make typecheck` must pass.
- **If the spec is ambiguous, stop and ask.** Do not guess and do not invent
  an API. A wrong guess costs more than a question.

## Commands
```
make check    # lint + typecheck + test — run before every commit
make inv      # invariant suite only — run after every change to risk/
make eval     # full corpus evaluation
make run      # dev server on :8000
```

## Commit format
```
<type>(<scope>): <summary>

Task: T-x.y
<what changed and why, 1-3 lines>
```
Types: `feat` `fix` `test` `refactor` `docs` `chore`.