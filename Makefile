.PHONY: install test inv lint typecheck check eval run dev-up dev-down

install:    ; pip install -e ".[dev]"
test:       ; pytest -q
inv:        ; pytest -q -m invariant
lint:       ; ruff check . && ruff format --check . && go vet ./cmd/... ./pkg/...
typecheck:  ; mypy packages
check:      ; $(MAKE) lint && $(MAKE) typecheck && $(MAKE) test
eval:       ; python -m packages.eval.run --all --report reports/latest.json
run:        ; uvicorn apps.gateway.main:app --reload --port 8000
dev-up:     ; docker compose -f infra/compose/docker-compose.yml up -d
dev-down:   ; docker compose -f infra/compose/docker-compose.yml down