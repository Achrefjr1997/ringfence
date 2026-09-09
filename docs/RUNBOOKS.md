# Runbooks (T-7.4)

Operational procedures for the things that go wrong or need doing on a
schedule. Keep each one short enough to follow at 3am.

---

## Rotate `RF_SESSION_SECRET`

Signs every session, verification, reset and invite token. Rotating it
**logs everyone out and voids all outstanding action tokens** — by design.

1. Generate: `openssl rand -base64 48`.
2. Set the new value in `.env.prod`, re-encrypt (`sops`), deploy.
3. Announce the forced logout if you have active users.

There is no dual-key grace period for session tokens — they are cheap to
re-mint (just log in again). If a silent rotation ever matters, that is a
feature to build (accept two secrets, verify against both), not a thing to
improvise.

---

## Rotate `RF_DATA_ENCRYPTION_KEY` (at-rest column encryption)

Encrypts `cases.feedback_note` and a retained `cases.transcript`. The
value is a **comma-separated list**: encrypt with the first, decrypt with
any. So rotation is zero-downtime and needs no migration.

1. Generate a new key:
   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. Prepend it: `RF_DATA_ENCRYPTION_KEY=<new>,<old>`. Deploy. From now on
   new writes use `<new>`; old rows still decrypt with `<old>`.
3. Re-encrypt existing rows in place (safe to run repeatedly, and while
   the service is up):
   ```
   python -m packages.db.reencrypt        # reads with any key, rewrites with the first
   ```
4. Once it reports 0 rows re-encrypted on a second run, drop `<old>`:
   `RF_DATA_ENCRYPTION_KEY=<new>`. Deploy.

If the key is **lost**: encrypted `feedback_note` / `transcript` values are
unrecoverable. Everything else (decision chains, scores, feedback labels,
identity, API keys) is unaffected — it was never encrypted with this key.

---

## Rotate a leaked provider key (AssemblyAI / Ollama)

> The AssemblyAI and Ollama keys used during the build were pasted into a
> chat transcript. **Rotate both before any real traffic.**

**AssemblyAI**

1. Dashboard → API keys → create a new key → revoke the old one.
2. Update `ASSEMBLYAI_API_KEY` in `.env.prod`, re-encrypt, deploy.
3. Confirm a `/replay` or live `/ws/capture` still transcribes.

**Ollama Cloud**

1. ollama.com → settings → API keys → rotate.
2. Update `OLLAMA_API_KEY`, re-encrypt, deploy.
3. Confirm the Tier-2 judge returns a verdict (watch a session in the
   console, or check `ringfence_*` logs for judge activity). If the judge
   is down the pipeline runs rules-only — degraded, not broken.

Provider keys are read from the environment (or the repo-root `.env` in
dev); there is no cache to bust beyond restarting the gateway.

---

## Rotate a tenant's API key

Self-service, no ops involvement:

1. Admin: `POST /orgs/keys` — issue the replacement (plaintext shown once).
2. Roll it out to that tenant's gateway callers.
3. Admin: `DELETE /orgs/keys/{id}` — revoke the old one. A revoked key
   stops resolving immediately (`resolve_api_key` returns `None`).

---

## Suspected credential / data incident

1. **Contain.** Revoke the suspect credential (API key: `DELETE
   /orgs/keys/{id}`; session secret or data key: rotate per above; provider
   key: rotate per above). If a whole tenant is compromised, revoke all its
   keys.
2. **Assess.** `GET /metrics` for admission/rejection anomalies; the JSON
   request log (`method`, `path`, `status`, `dur_ms` per line) for the
   access pattern. Transcripts are not retained by default, so a database
   read does **not** expose call content unless `RF_RETAIN_TRANSCRIPTS` was
   on — check.
3. **Notify.** Whoever owns the affected tenant, and — if EU personal data
   is in scope — assess the GDPR 72-hour breach-notification duty with
   counsel. Have counsel's contact in this file before you need it:
   `TODO: <name / firm / phone>`.
4. **Record.** Timeline, what was accessed, what was rotated, what was
   communicated. Written during, not reconstructed after.
5. **Recover.** New credentials everywhere, redeploy from a known-good
   image tag, watch `/metrics` and the logs for recurrence.

---

## Restore from backup

State lives on two volumes: `pg_data` (Postgres) and `caddy_data` (TLS
certs — re-issuable, so lower priority).

1. Stop the stack: `docker compose -f docker-compose.prod.yml down`.
2. Restore the `pg_data` volume from your snapshot.
3. `up -d`. Run `python -m packages.db.migrate` (idempotent) in case the
   snapshot predates a schema addition.
4. Verify `/health` and a `/cases` read.

---

## Alerts

Prometheus (`infra/monitoring/`) scrapes `gateway:8000/metrics` and
evaluates `alert-rules.yml`; Alertmanager POSTs firing/resolved alerts to
`RF_ALERT_WEBHOOK_URL`. Grafana serves the **RingFence — Gateway**
dashboard (admits, rejections by reason, active vs capacity, per-tenant
sessions, ASR breaker state), datasource + dashboard provisioned from
`infra/monitoring/grafana/`; the admin password is `RF_GRAFANA_ADMIN_PASSWORD`.
All three services are internal — reach the UIs over an SSH tunnel
(`ssh -L 3000:localhost:3000 -L 9090:localhost:9090 -L 9093:localhost:9093 box`).
Locally they are published on `:3000` / `:9090` / `:9093`; Grafana opens
straight onto the dashboard (anonymous Admin).

| Alert | Severity | Means | First response |
|---|---|---|---|
| **GatewayDown** | critical | scrape failing 2m | is the container up? `docker compose ps`, `logs gateway`. Caddy still serving? |
| **GatewayNoMetrics** | critical | scrape OK, `ringfence_*` absent | app wedged or misbuilt — `logs gateway`, roll back `RF_IMAGE_TAG` |
| **ASRBreakerOpen** | warning | a provider's breaker open 3m; calls run rules-only | check provider status page + the key (`RUNBOOKS` → rotate provider key); breaker half-opens itself after 30s once the provider recovers |
| **HighRejectionRate** | warning | >50% of admission attempts rejected 10m | break down `ringfence_rejected_total` by `reason`; usually AUTH (bad integration) or CAPACITY |
| **AuthRejectionSpike** | warning | sustained bad-key hits (>30/min) | identify the source IP from the JSON access log; revoke the key if it's a leak; the ingress rate limiter is already throttling it |
| **BillingCapHitsRepeatedly** | info | a tenant keeps hitting its plan hard cap 15m | `GET /usage` for the tenant; upsell, or fix a mis-assigned plan (`POST /orgs/plan`) |
| **NearSessionCapacity** | warning | >90% of `capacity` in use 5m | scale out (another node) or raise `capacity`; CAPACITY rejections are imminent |

Silence noisy alerts from the Alertmanager UI while you work; don't edit
the rules on the box.
