#!/usr/bin/env bash
# Deploy one environment (T-7.2c). Called by .github/workflows/release.yml
# with SSH_HOST / SSH_USER / SSH_KEY / AGE_KEY / IMAGE_TAG in the env.
#
#   ./infra/deploy/remote.sh staging|production
#
# Idempotent: migrate is safe to re-run, `compose up -d` only recreates
# what changed. Rollback = re-run with an older IMAGE_TAG.
set -euo pipefail

ENVIRONMENT="${1:?usage: remote.sh staging|production}"
: "${SSH_HOST:?}" "${SSH_USER:?}" "${SSH_KEY:?}" "${AGE_KEY:?}" "${IMAGE_TAG:?}"

REMOTE_DIR="/opt/ringfence"
IMAGE="ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/ringfence-gateway:${IMAGE_TAG}"

key_file="$(mktemp)"
age_file="$(mktemp)"
trap 'rm -f "$key_file" "$age_file"' EXIT
printf '%s' "$SSH_KEY" > "$key_file" && chmod 600 "$key_file"
printf '%s' "$AGE_KEY" > "$age_file" && chmod 600 "$age_file"

ssh_opts=(-i "$key_file" -o StrictHostKeyChecking=accept-new)
ssh "${ssh_opts[@]}" "${SSH_USER}@${SSH_HOST}" "mkdir -p ${REMOTE_DIR}"
scp "${ssh_opts[@]}" \
  infra/compose/docker-compose.prod.yml \
  infra/compose/Caddyfile \
  infra/compose/.env.prod.enc \
  "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/"
scp "${ssh_opts[@]}" "$age_file" "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/age.key"

ssh "${ssh_opts[@]}" "${SSH_USER}@${SSH_HOST}" bash -s <<EOF
set -euo pipefail
cd "${REMOTE_DIR}"

# decrypt secrets with the age key we just shipped, then shred it
SOPS_AGE_KEY_FILE=age.key sops --decrypt .env.prod.enc > .env.prod
shred -u age.key 2>/dev/null || rm -f age.key

# pin the tag we are rolling out
grep -q '^RF_IMAGE_TAG=' .env.prod \
  && sed -i "s|^RF_IMAGE_TAG=.*|RF_IMAGE_TAG=${IMAGE_TAG}|" .env.prod \
  || echo "RF_IMAGE_TAG=${IMAGE_TAG}" >> .env.prod

docker pull "${IMAGE}"
docker run --rm --env-file .env.prod --network host "${IMAGE}" python -m packages.db.migrate
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
rm -f .env.prod

for i in \$(seq 1 30); do
  curl -fsS "http://localhost/health" >/dev/null && break
  sleep 2
done
curl -fsS "http://localhost/health"
echo "deployed ${IMAGE_TAG} to ${ENVIRONMENT}"
EOF
