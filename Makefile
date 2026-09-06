.PHONY: install test inv lint typecheck check eval run dev-up dev-down

# `test` / `check` skip the markers that need a network key or a heavy model.
# Run those explicitly: pytest -m needs_key | needs_model | needs_ollama
LIVE_MARKERS := not needs_key and not needs_model and not needs_ollama

install:    ; pip install -e ".[dev]"
test:       ; pytest -q -m "$(LIVE_MARKERS)"
inv:        ; pytest -q -m invariant
lint:       ; ruff check . && ruff format --check .
typecheck:  ; mypy packages apps
check:      ; $(MAKE) lint && $(MAKE) typecheck && $(MAKE) test
eval:       ; python -m packages.eval.run --all --report reports/latest.json
run:        ; uvicorn apps.gateway.main:app --reload --port 8000
dev-up:     ; docker compose -f infra/compose/docker-compose.yml up -d
dev-down:   ; docker compose -f infra/compose/docker-compose.yml down
