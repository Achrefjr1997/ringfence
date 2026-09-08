# Deploying Ringfence (T-7.2c)

The engineering half of the SaaS roadmap's T-7.2. It does **not** clear the
legal gate (T-7.0) — do not process a real customer's calls before counsel
signs off on consent and a GDPR lawful basis.

## Topology

`infra/compose/docker-compose.prod.yml` is standalone — one file for what
runs in production:

```
internet ──443──▶ caddy ──▶ gateway:8000 ──▶ nats / redis / postgres
                  (auto TLS)   (no published port)
```

* **caddy** (`caddy:2-alpine`) terminates TLS, provisions and renews a
  Let's Encrypt cert for `$RF_DOMAIN` on its own, adds HSTS + the usual
  hardening headers, and reverse-proxies everything — WebSocket
  (`/ws/capture`) and SSE (`/events/*`) included — to the gateway.
* **gateway** is pulled by tag (`ghcr.io/achrefjr1997/ringfence-gateway:$RF_IMAGE_TAG`),
  never built on the box. `RF_DEV_MODE=0`, `RF_LOG_FORMAT=json`,
  `RF_SESSION_SECRET` and `RF_DATABASE_URL` required.
* **postgres** state is on the `pg_data` volume; **caddy** cert state on
  `caddy_data`. Back both up.

Sizing: the production design scopes ~100 concurrent calls per node. Start
with a VPS matched to that (e.g. a 4 vCPU / 8 GB Hetzner or Scaleway
instance in the EU), not the 10k tier.

## One-time server setup

1. Install Docker Engine + the compose plugin.
2. `git clone` the repo (or just copy `infra/compose/`).
3. Point `RF_DOMAIN`'s A/AAAA records at the box **before** starting Caddy,
   or ACME will fail the challenge.
4. Create `infra/compose/.env.prod` from `.env.prod.example`, fill every
   `CHANGE_ME` and the empty required vars. Generate the session secret with
   `openssl rand -base64 48`.

## Secrets: SOPS + age

`.env.prod` is gitignored. Keep the **encrypted** copy in the repo (or a
private ops repo) and decrypt on the box at deploy time.

```bash
# once, on your workstation
age-keygen -o age.key                       # keep age.key OUT of git
export SOPS_AGE_RECIPIENTS=$(grep -oP 'public key: \K.*' age.key)

# encrypt (produces .env.prod.enc, safe to commit)
sops --encrypt --age "$SOPS_AGE_RECIPIENTS" \
  infra/compose/.env.prod > infra/compose/.env.prod.enc

# on the box, at deploy time (age.key delivered out of band / via the CD secret store)
SOPS_AGE_KEY_FILE=age.key sops --decrypt \
  infra/compose/.env.prod.enc > infra/compose/.env.prod
```

A dedicated vault (HashiCorp Vault, or a managed KMS) is the next step up
once there is a budget for it; SOPS + age is the "no infrastructure,
still not plaintext on disk" baseline.

## First deploy

```bash
cd infra/compose

# 1. migrate the database (idempotent; safe to re-run on every deploy)
docker run --rm --env-file .env.prod --network host \
  ghcr.io/achrefjr1997/ringfence-gateway:$RF_IMAGE_TAG \
  python -m packages.db.migrate

# 2. bring it up
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d

# 3. verify
curl -fsS https://$RF_DOMAIN/health
curl -fsS https://$RF_DOMAIN/metrics | head
```

While DNS is still moving, set `RF_ACME_CA` to the Let's Encrypt **staging**
line from `.env.prod.example` so a misconfigured run does not burn the prod
CA rate limit. Remove it for real certificates, then
`docker compose ... up -d` again.

## Staging first, always

Run an identical stack on a separate host / subdomain with its own
`.env.staging`, its own database, and `RF_DRY_RUN=true`. Deploy there,
smoke-test (`/health`, `/metrics`, a `/replay` against a fixture, a real
`/ws/capture` round trip), then promote the **same image tag** to
production. Never build or test-fix directly on the production box.

## Rolling back

The image tag is the version. To roll back:

```bash
# set RF_IMAGE_TAG in .env.prod back to the previous good git sha, then
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Database migrations are additive and idempotent (`CREATE ... IF NOT
EXISTS`, no destructive `ALTER`), so an older image runs against a newer
schema without a down-migration. If that ever stops being true, the
migration that breaks it needs an explicit, tested rollback path before it
merges.

## CI → CD

`.github/workflows/release.yml`:

1. **build-push** (on every push to `main`): builds the gateway image,
   pushes `ghcr.io/achrefjr1997/ringfence-gateway:<sha>` and `:latest`.
2. **deploy-staging** (auto, after build-push): SSHes to the staging host,
   decrypts `.env.prod.enc`, runs the migrate step, `compose up -d`.
3. **deploy-production** (`environment: production` → **manual approval** in
   the GitHub UI): same steps against the production host.

Both deploy jobs are inert until their `*_SSH_HOST` / `*_SSH_KEY` /
`AGE_KEY` secrets are set on the repo — the workflow skips them with a
notice rather than failing, so the pipeline is green before a server
exists.
